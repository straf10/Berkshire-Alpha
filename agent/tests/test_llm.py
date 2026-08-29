from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import BaseModel

from agent.config import LLM_BASE_URL
from agent.storage import db as storage_db
from agent.tools.llm import (
    LlmBudget,
    LlmBudgetExceeded,
    LlmClient,
    LlmUnavailable,
    LlmValidationDropped,
    _cost,
    _extract_json,
    load_budget,
)
from agent.tools import llm as llm_module

_URL = f"{LLM_BASE_URL}/chat/completions"


class _Simple(BaseModel):
    value: int


def _openai_response(content: str, *, with_usage: bool = True) -> dict:
    body: dict = {"choices": [{"message": {"content": content}}]}
    if with_usage:
        body["usage"] = {"prompt_tokens": 120, "completion_tokens": 20}
    return body


@pytest.fixture(autouse=True)
def _reset_json_mode_flag():
    llm_module._JSON_MODE_UNSUPPORTED.clear()
    yield
    llm_module._JSON_MODE_UNSUPPORTED.clear()


@pytest.fixture
async def conn(tmp_path):
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as c:
        yield c


def _budget() -> LlmBudget:
    return LlmBudget(spent_usd=Decimal("0"), calls=0)


async def _row_count(conn, *, ok: int | None = None) -> int:
    if ok is None:
        cur = await conn.execute("SELECT COUNT(*) FROM llm_calls")
    else:
        cur = await conn.execute("SELECT COUNT(*) FROM llm_calls WHERE ok = ?", (ok,))
    return (await cur.fetchone())[0]


@respx.mock
async def test_complete_json_happy_path(conn) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_openai_response('{"value": 7}')))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        result = await client.complete_json("test prompt", _Simple, node="TEST")
    assert result.value == 7
    assert await _row_count(conn) == 1
    assert await _row_count(conn, ok=1) == 1


@respx.mock
async def test_retry_once_then_succeed(conn) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(200, json=_openai_response("not json at all")),
        httpx.Response(200, json=_openai_response('{"value": 3}')),
    ]
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        result = await client.complete_json("test prompt", _Simple, node="TEST")
    assert result.value == 3
    assert route.call_count == 2
    assert await _row_count(conn) == 2
    assert await _row_count(conn, ok=0) == 1
    assert await _row_count(conn, ok=1) == 1


@respx.mock
async def test_retry_error_trace_in_second_prompt(conn) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(200, json=_openai_response("garbage")),
        httpx.Response(200, json=_openai_response('{"value": 3}')),
    ]
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        await client.complete_json("test prompt", _Simple, node="TEST")
    second_body = route.calls[1].request.content.decode()
    assert "failed schema validation" in second_body
    assert "value" in second_body


@respx.mock
async def test_two_validation_errors_drop(conn) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(200, json=_openai_response("garbage 1")),
        httpx.Response(200, json=_openai_response("garbage 2")),
        httpx.Response(200, json=_openai_response('{"value": 1}')),
    ]
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        with pytest.raises(LlmValidationDropped):
            await client.complete_json("test prompt", _Simple, node="TEST")
    assert route.call_count == 2
    assert await _row_count(conn) == 2
    assert await _row_count(conn, ok=0) == 2


@respx.mock
async def test_429_raises_unavailable(conn) -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        with pytest.raises(LlmUnavailable):
            await client.complete_json("test prompt", _Simple, node="TEST")
    assert route.call_count == 1
    assert await _row_count(conn) == 1
    assert await _row_count(conn, ok=0) == 1


@respx.mock
async def test_timeout_raises_unavailable(conn) -> None:
    respx.post(_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        with pytest.raises(LlmUnavailable):
            await client.complete_json("test prompt", _Simple, node="TEST")


@respx.mock
async def test_json_mode_fallback(conn) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(400, json={"error": "response_format not supported"}),
        httpx.Response(200, json=_openai_response('{"value": 9}')),
    ]
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        result = await client.complete_json("test prompt", _Simple, node="TEST")
    assert result.value == 9
    assert route.call_count == 2
    # the 400 probe is not a validation retry -- only the successful attempt is logged.
    assert await _row_count(conn) == 1
    second_request_body = route.calls[1].request.content.decode()
    assert "response_format" not in second_request_body


@respx.mock
async def test_fenced_json_extracted(conn) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_openai_response('```json\n{"value": 5}\n```')))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        result = await client.complete_json("test prompt", _Simple, node="TEST")
    assert result.value == 5


@respx.mock
async def test_budget_blocks_before_http(conn) -> None:
    route = respx.post(_URL)
    budget = LlmBudget(spent_usd=Decimal("100"), calls=0, ceiling_usd=Decimal("4.00"))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, budget, api_key="k")
        with pytest.raises(LlmBudgetExceeded):
            await client.complete_json("test prompt", _Simple, node="TEST")
    assert route.call_count == 0


@respx.mock
async def test_call_cap_blocks(conn) -> None:
    budget = LlmBudget(spent_usd=Decimal("0"), calls=80, max_calls=80)
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, budget, api_key="k")
        with pytest.raises(LlmBudgetExceeded):
            await client.complete_json("test prompt", _Simple, node="TEST")


async def test_budget_survives_restart(conn) -> None:
    from agent.storage import write as storage_write

    for i in range(3):
        await storage_write.insert_llm_call(conn, storage_write.LlmCallRow(
            ts_utc="2026-08-31T12:00:00+00:00", node="TEST", provider="featherless", model="m",
            prompt_tokens=100, completion_tokens=20, latency_ms=50, est_cost_usd=Decimal("0.01"), ok=True,
        ))
    budget = await load_budget(conn, "2026-08-31")
    assert budget.spent_usd == Decimal("0.03")
    assert budget.calls == 3


@respx.mock
async def test_missing_usage_estimates_cost(conn) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_openai_response('{"value": 1}', with_usage=False)))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(http, conn, _budget(), api_key="k")
        await client.complete_json("test prompt", _Simple, node="TEST")
    cur = await conn.execute("SELECT est_cost_usd FROM llm_calls")
    cost = (await cur.fetchone())[0]
    assert cost > 0


def test_cost_is_monotone() -> None:
    low = _cost(100, 20)
    high = _cost(100, 40)
    assert high > low


def test_extract_json_strips_fence() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('prefix noise {"a": 1} trailing noise') == '{"a": 1}'
