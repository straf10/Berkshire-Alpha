from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import BaseModel

from agent.config import LLM_BASE_URL, LLM_MODEL_COSTS
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
    low = _cost(100, 20, "m")
    high = _cost(100, 40, "m")
    assert high > low


def test_extract_json_strips_fence() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('prefix noise {"a": 1} trailing noise') == '{"a": 1}'


@respx.mock
async def test_model_routed_per_node(conn) -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200, json=_openai_response('{"value": 1}')))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(
            http, conn, _budget(), model="default-model", api_key="k",
            node_models={"TRADER": "model-a", "NEWS": "model-b"},
        )
        await client.complete_json("test prompt", _Simple, node="TRADER")
        await client.complete_json("test prompt", _Simple, node="NEWS")

    bodies = [json.loads(call.request.content.decode()) for call in route.calls]
    assert bodies[0]["model"] == "model-a"
    assert bodies[1]["model"] == "model-b"

    cur = await conn.execute("SELECT node, model FROM llm_calls ORDER BY id")
    rows = [tuple(row) for row in await cur.fetchall()]
    assert rows[0] == ("TRADER", "model-a")
    assert rows[1] == ("NEWS", "model-b")


@respx.mock
async def test_unrouted_node_falls_back_to_default_model(conn) -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200, json=_openai_response('{"value": 1}')))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(
            http, conn, _budget(), model="default-model", api_key="k",
            node_models={"TRADER": "model-a"},
        )
        await client.complete_json("test prompt", _Simple, node="SOME_OTHER_NODE")

    body = json.loads(route.calls[0].request.content.decode())
    assert body["model"] == "default-model"
    cur = await conn.execute("SELECT model FROM llm_calls")
    assert (await cur.fetchone())[0] == "default-model"


def test_cost_uses_per_model_price() -> None:
    models = list(LLM_MODEL_COSTS)
    cost_a = _cost(1000, 1000, models[0])
    cost_b = _cost(1000, 1000, models[1])
    assert cost_a != cost_b
    # Monotonicity in completion_tokens still holds per-model.
    for model in models:
        assert _cost(100, 40, model) > _cost(100, 20, model)


@respx.mock
async def test_json_mode_probe_is_per_model(conn) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(400, json={"error": "response_format not supported"}),
        httpx.Response(200, json=_openai_response('{"value": 1}')),
        httpx.Response(200, json=_openai_response('{"value": 2}')),
    ]
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(
            http, conn, _budget(), api_key="k",
            node_models={"NODE_A": "model-a", "NODE_B": "model-b"},
        )
        await client.complete_json("test prompt", _Simple, node="NODE_A")
        await client.complete_json("test prompt", _Simple, node="NODE_B")

    assert route.call_count == 3
    assert "response_format" not in route.calls[1].request.content.decode()
    assert "response_format" in route.calls[2].request.content.decode()


@respx.mock
async def test_failed_call_records_routed_model(conn) -> None:
    respx.post(_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with httpx.AsyncClient(base_url=LLM_BASE_URL) as http:
        client = LlmClient(
            http, conn, _budget(), model="default-model", api_key="k",
            node_models={"TRADER": "model-a"},
        )
        with pytest.raises(LlmUnavailable):
            await client.complete_json("test prompt", _Simple, node="TRADER")

    cur = await conn.execute("SELECT model FROM llm_calls")
    assert (await cur.fetchone())[0] == "model-a"
