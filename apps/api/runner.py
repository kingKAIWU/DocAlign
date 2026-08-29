from __future__ import annotations

import asyncio

from apps.api.service import ApiService


class JobRunner:
    def __init__(self, service: ApiService, concurrency: int = 1) -> None:
        self.service = service
        self.concurrency = concurrency
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.workers: list[asyncio.Task[None]] = []
        self.scheduled: set[str] = set()
        self.schedule_lock = asyncio.Lock()

    async def start(self) -> None:
        self.workers = [
            asyncio.create_task(self._worker(), name=f"docalign-job-worker-{index}")
            for index in range(self.concurrency)
        ]

    async def enqueue(self, job_id: str) -> bool:
        async with self.schedule_lock:
            if job_id in self.scheduled:
                return False
            self.scheduled.add(job_id)
            await self.queue.put(job_id)
            return True

    async def close(self) -> None:
        for _ in self.workers:
            await self.queue.put(None)
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                if job_id is None:
                    return
                await asyncio.to_thread(self.service.run_job, job_id)
            finally:
                if job_id is not None:
                    async with self.schedule_lock:
                        self.scheduled.discard(job_id)
                self.queue.task_done()
