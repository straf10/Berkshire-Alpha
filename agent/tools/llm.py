from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, TypeVar

import aiosqlite
import httpx
from pydantic import BaseModel, ValidationError

from agent.config import (
    LLM_BASE_URL,
    LLM_COST_IN_PER_MTOK,
    LLM_COST_OUT_PER_MTOK,
    LLM_DAILY_SPEND_CEILING_USD,
    LLM_MAX_CALLS_PER_SESSION,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_S,
    LLM_VALIDATION_RETRIES,
)
from agent.storage import write as storage_write

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_ERROR_TRACE_MAX_CHARS = 800

# Providers observed not to support response_format={"type":"json_object"} --
# probed once per process, then skipped for the rest of the session. Not a
# validation retry (docs/day3_llm_plan.md S2).
_JSON_MODE_UNSUPPORTED: set[str] = set()


class LlmUnavailable(RuntimeError):
    """Transport-level: 429, 5xx, timeout, connection error. Caller degrades to quant-only."""


class LlmBudgetExceeded(LlmUnavailable):
    """Daily ceiling or session call cap. Subclass so one `except LlmUnavailable`
    catches both, while the orchestrator can distinguish them for the gate flag."""


class LlmValidationDropped(RuntimeError):
    """Two ValidationErrors on one node. NOT a subclass of LlmUnavailable --
    the provider is fine, this model output is not, and it must not trip the
    quant-only fallback for the whole cycle."""


@dataclass
class LlmBudget:
    spent_usd: Decimal
    calls: int
    ceiling_usd: Decimal = LLM_DAILY_SPEND_CEILING_USD
    max_calls: int = LLM_MAX_CALLS_PER_SESSION

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.ceiling_usd or self.calls >= self.max_calls

    def charge(self, usd: Decimal) -> None:
        self.spent_usd += usd
        self.calls += 1


async def load_budget(conn: aiosqlite.Connection, session_date: str) -> LlmBudget:
    """SUM(est_cost_usd), COUNT(*) over llm_calls for the session date. Survives
    a restart -- the ceiling is a property of the day, not of the process."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(est_cost_usd), 0), COUNT(*) FROM llm_calls WHERE ts_utc LIKE ?",
        (f"{session_date}%",),
    )
    row = await cur.fetchone()
    return LlmBudget(spent_usd=Decimal(str(row[0])), calls=int(row[1]))


def _cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Pure -- doubling completion_tokens strictly increases cost."""
    return (
        (Decimal(prompt_tokens) / Decimal(1_000_000)) * LLM_COST_IN_PER_MTOK
        + (Decimal(completion_tokens) / Decimal(1_000_000)) * LLM_COST_OUT_PER_MTOK
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_FENCE_OPEN_RE = re.compile(r"^```(?:json)?\s*")
_FENCE_CLOSE_RE = re.compile(r"\s*```$")


def _extract_json(text: str) -> str:
    """Strips a leading ```json fence and trailing fence, then takes the
    outermost {...} span. The difference between a working pipeline and a
    100% drop rate if JSON mode is unavailable."""
    stripped = _FENCE_CLOSE_RE.sub("", _FENCE_OPEN_RE.sub("", text.strip()))
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


class LlmPort(Protocol):
    async def complete_json(
        self, prompt: str, schema: type[M], *, node: str,
        system: str | None = None, sink: list[int] | None = None,
    ) -> M: ...


class LlmClient:
    def __init__(
        self, http: httpx.AsyncClient, conn: aiosqlite.Connection, budget: LlmBudget, *,
        provider: str = LLM_PROVIDER, model: str = LLM_MODEL, api_key: str,
    ) -> None:
        """`http` and `conn` are injected: respx needs the transport, and the
        cycle already owns an aiosqlite connection (aiosqlite serialises
        statements on one connection, so concurrent gather()'d calls are safe)."""
        self._http = http
        self._conn = conn
        self._budget = budget
        self._provider = provider
        self._model = model
        self._api_key = api_key

    async def complete_json(
        self, prompt: str, schema: type[M], *, node: str,
        system: str | None = None, sink: list[int] | None = None,
    ) -> M:
        if self._budget.exhausted:
            raise LlmBudgetExceeded(
                f"budget exhausted: spent={self._budget.spent_usd} calls={self._budget.calls}"
            )

        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        user_content = (
            f"{prompt}\n\nRespond with a single JSON object matching this schema. "
            f"No prose, no markdown fence.\n{schema_json}"
        )
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})

        return await self._attempt(messages, schema, node=node, sink=sink, retry_index=0)

    async def _post(self, messages: list[dict[str, str]], *, use_json_mode: bool) -> tuple[httpx.Response, int]:
        payload: dict = {
            "model": self._model, "messages": messages,
            "temperature": LLM_TEMPERATURE, "max_tokens": LLM_MAX_TOKENS,
        }
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        t0 = time.monotonic()
        resp = await self._http.post("/chat/completions", json=payload, headers=headers, timeout=LLM_TIMEOUT_S)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return resp, latency_ms

    async def _record(
        self, *, node: str, prompt_tokens: int, completion_tokens: int,
        latency_ms: int, ok: bool, retry_index: int, sink: list[int] | None,
    ) -> None:
        cost = _cost(prompt_tokens, completion_tokens)
        self._budget.charge(cost)
        row_id = await storage_write.insert_llm_call(
            self._conn,
            storage_write.LlmCallRow(
                ts_utc=datetime.now(timezone.utc).isoformat(), node=node, provider=self._provider,
                model=self._model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, est_cost_usd=cost, ok=ok, retry_index=retry_index,
            ),
        )
        if sink is not None:
            sink.append(row_id)

    async def _attempt(
        self, messages: list[dict[str, str]], schema: type[M], *,
        node: str, sink: list[int] | None, retry_index: int,
    ) -> M:
        use_json = self._provider not in _JSON_MODE_UNSUPPORTED
        prompt_text = "".join(m["content"] for m in messages)

        try:
            resp, latency_ms = await self._post(messages, use_json_mode=use_json)
        except httpx.TimeoutException as e:
            await self._record(
                node=node, prompt_tokens=_estimate_tokens(prompt_text), completion_tokens=0,
                latency_ms=0, ok=False, retry_index=retry_index, sink=sink,
            )
            raise LlmUnavailable(f"timeout on node {node}: {e}") from e
        except httpx.HTTPError as e:
            await self._record(
                node=node, prompt_tokens=_estimate_tokens(prompt_text), completion_tokens=0,
                latency_ms=0, ok=False, retry_index=retry_index, sink=sink,
            )
            raise LlmUnavailable(f"transport error on node {node}: {e}") from e

        # JSON-mode negotiation: not a validation retry, does not consume LLM_VALIDATION_RETRIES.
        if resp.status_code == 400 and use_json:
            _JSON_MODE_UNSUPPORTED.add(self._provider)
            logger.warning("provider %s rejected response_format -- retrying without JSON mode", self._provider)
            return await self._attempt(messages, schema, node=node, sink=sink, retry_index=retry_index)

        if resp.status_code == 429 or resp.status_code >= 500:
            await self._record(
                node=node, prompt_tokens=_estimate_tokens(prompt_text), completion_tokens=0,
                latency_ms=latency_ms, ok=False, retry_index=retry_index, sink=sink,
            )
            raise LlmUnavailable(f"provider error {resp.status_code} on node {node}")

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage")
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
        else:
            logger.warning("provider response for node %s missing usage block -- estimating token counts", node)
            prompt_tokens = _estimate_tokens(prompt_text)
            completion_tokens = _estimate_tokens(text)

        try:
            result = schema.model_validate_json(_extract_json(text))
        except ValidationError as e:
            await self._record(
                node=node, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, ok=False, retry_index=retry_index, sink=sink,
            )
            if retry_index >= LLM_VALIDATION_RETRIES:
                raise LlmValidationDropped(f"schema validation failed twice for node {node}") from e
            error_trace = json.dumps(e.errors(), default=str)[:_ERROR_TRACE_MAX_CHARS]
            retry_messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"Your previous response failed schema validation:\n{error_trace}\nReturn corrected JSON only."},
            ]
            return await self._attempt(retry_messages, schema, node=node, sink=sink, retry_index=retry_index + 1)

        await self._record(
            node=node, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            latency_ms=latency_ms, ok=True, retry_index=retry_index, sink=sink,
        )
        return result
