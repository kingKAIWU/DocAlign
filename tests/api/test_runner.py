from __future__ import annotations

import asyncio
import threading
from typing import cast

import pytest

from apps.api.runner import JobRunner
from apps.api.service import ApiService


class BlockingService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    def run_job(self, job_id: str) -> None:
        self.calls.append(job_id)
        self.started.set()
        self.release.wait(timeout=2)


@pytest.mark.asyncio
async def test_runner_deduplicates_a_job_while_it_is_scheduled() -> None:
    service = BlockingService()
    runner = JobRunner(cast(ApiService, service), concurrency=1)
    await runner.start()
    try:
        assert await runner.enqueue("job_once") is True
        assert await asyncio.to_thread(service.started.wait, 1) is True
        assert await runner.enqueue("job_once") is False
        service.release.set()
        await asyncio.wait_for(runner.queue.join(), timeout=2)
        assert service.calls == ["job_once"]
    finally:
        service.release.set()
        await runner.close()
