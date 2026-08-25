from __future__ import annotations

import json

import httpx
import pytest
from docalign_core.domain.formatting_spec import default_academic_spec
from docalign_core.llm.base import RequirementCompilationError
from docalign_core.llm.openai_compatible import (
    OpenAICompatibleChatInterpreter,
    _chat_endpoint,
    _raw_content,
)


@pytest.mark.asyncio
async def test_compatible_interpreter_falls_back_when_schema_is_unsupported() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": "unsupported"})
        content = default_academic_spec().model_dump_json()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    interpreter = OpenAICompatibleChatInterpreter(
        base_url="https://example.test/v1",
        model="test-model",
        json_schema_mode="auto",
        client=client,
    )
    result = await interpreter.compile_requirements("正文宋体小四")
    await client.aclose()
    assert result.spec.schema_version == "formatting-spec.v1"
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert "JSON Schema" in calls[1]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_deepseek_uses_json_object_and_disables_thinking() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": default_academic_spec().model_dump_json()}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        interpreter = OpenAICompatibleChatInterpreter(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-flash",
            json_schema_mode="auto",
            client=client,
        )
        result = await interpreter.compile_requirements("正文宋体")

    assert result.spec.schema_version == "formatting-spec.v1"
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["thinking"] == {"type": "disabled"}
    assert seen["max_tokens"] == 4096


def test_interpreter_requires_configuration_and_normalizes_endpoint() -> None:
    with pytest.raises(RequirementCompilationError) as captured:
        OpenAICompatibleChatInterpreter(base_url="", model="")
    assert captured.value.code == "LLM_NOT_CONFIGURED"
    assert _chat_endpoint("https://example.test") == "https://example.test/v1/chat/completions"
    assert _chat_endpoint("https://example.test/v1/") == (
        "https://example.test/v1/chat/completions"
    )
    assert _chat_endpoint("https://example.test/v1/chat/completions") == (
        "https://example.test/v1/chat/completions"
    )


@pytest.mark.asyncio
async def test_schema_response_accepts_fenced_content_parts_and_auth_header() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        content = f"```json\n{default_academic_spec().model_dump_json()}\n```"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": [{"text": content}]}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        interpreter = OpenAICompatibleChatInterpreter(
            base_url="https://example.test",
            api_key="secret",
            model="test-model",
            json_schema_mode="required",
            client=client,
        )
        result = await interpreter.compile_requirements("标题二号", None)

    assert result.provider == "openai-compatible"
    assert result.spec.source.instruction_hash
    assert seen["authorization"] == "Bearer secret"
    assert "response_format" in seen["body"]


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_once() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "[]" if calls == 1 else default_academic_spec().model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        interpreter = OpenAICompatibleChatInterpreter(
            base_url="https://example.test",
            model="test-model",
            json_schema_mode="disabled",
            client=client,
        )
        result = await interpreter.compile_requirements("正文宋体")

    assert result.spec.schema_version == "formatting-spec.v1"
    assert calls == 2


@pytest.mark.asyncio
async def test_invalid_output_after_repair_is_recoverable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        interpreter = OpenAICompatibleChatInterpreter(
            base_url="https://example.test",
            model="test-model",
            json_schema_mode="disabled",
            client=client,
        )
        with pytest.raises(RequirementCompilationError) as captured:
            await interpreter.compile_requirements("正文宋体")

    assert captured.value.code == "REQUIREMENT_PARSE_FAILED"


@pytest.mark.asyncio
async def test_empty_timeout_and_http_error_have_stable_codes() -> None:
    interpreter = OpenAICompatibleChatInterpreter(
        base_url="https://example.test", model="test-model"
    )
    with pytest.raises(RequirementCompilationError) as empty:
        await interpreter.compile_requirements("   ")
    assert empty.value.code == "REQUIREMENT_PARSE_FAILED"

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        timed = OpenAICompatibleChatInterpreter(
            base_url="https://example.test", model="test-model", client=client
        )
        with pytest.raises(RequirementCompilationError) as captured:
            await timed.compile_requirements("正文宋体")
    assert captured.value.code == "LLM_TIMEOUT"

    def unauthorized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)) as client:
        denied = OpenAICompatibleChatInterpreter(
            base_url="https://example.test", model="test-model", client=client
        )
        with pytest.raises(RequirementCompilationError) as captured:
            await denied.compile_requirements("正文宋体")
    assert captured.value.code == "LLM_REQUEST_FAILED"


def test_malformed_model_content_has_stable_parse_error() -> None:
    for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": 42}}]}):
        with pytest.raises(RequirementCompilationError) as captured:
            _raw_content(payload)
        assert captured.value.code == "REQUIREMENT_PARSE_FAILED"
