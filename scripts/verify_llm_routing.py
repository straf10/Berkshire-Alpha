"""Preflight check for the per-node LLM model routing (docs/premarket_p1_p3_plan.md P1).

Read-only: one GET https://api.featherless.ai/v1/models, no chat completions,
no cost. For every distinct model referenced by LLM_NODE_MODELS or LLM_MODEL,
confirms it exists on the account and is available on the current plan, prints
its context_length/concurrency_cost/live pricing, and diffs that live pricing
against the LLM_MODEL_COSTS table so a stale cost table can't silently corrupt
the daily spend ceiling.

Exits non-zero if any routed model is missing or unavailable.

    python scripts/verify_llm_routing.py
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from agent.config import LLM_MODEL, LLM_MODEL_COSTS, LLM_NODE_MODELS  # noqa: E402

FEATHERLESS_MODELS_URL = "https://api.featherless.ai/v1/models"
PRICE_MISMATCH_TOLERANCE = Decimal("0.001")


def main() -> int:
    api_key = os.environ.get("FEATHERLESS_API_KEY", "")
    if not api_key:
        print("FEATHERLESS_API_KEY not set -- cannot probe the live model list.")
        return 1

    resp = httpx.get(FEATHERLESS_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0)
    resp.raise_for_status()
    live_models = {m["id"]: m for m in resp.json().get("data", [])}

    routed = dict(LLM_NODE_MODELS)
    routed.setdefault("(fallback) LLM_MODEL", LLM_MODEL)
    distinct_models = sorted(set(routed.values()))

    ok = True
    for model_id in distinct_models:
        nodes = sorted(node for node, m in routed.items() if m == model_id)
        info = live_models.get(model_id)
        if info is None:
            print(f"MISSING  {model_id}  (nodes: {', '.join(nodes)})")
            ok = False
            continue

        available = info.get("available_on_current_plan", False)
        pricing = info.get("pricing", {})
        ctx = info.get("context_length")
        concurrency = info.get("concurrency_cost")
        live_in = Decimal(str(pricing.get("input", "0")))
        live_out = Decimal(str(pricing.get("output", "0")))

        status = "OK" if available else "UNAVAILABLE"
        if not available:
            ok = False
        print(f"{status:11s} {model_id}  (nodes: {', '.join(nodes)})")
        print(f"            context_length={ctx}  concurrency_cost={concurrency}  "
              f"pricing.input={live_in}  pricing.output={live_out}")

        table_prices = LLM_MODEL_COSTS.get(model_id)
        if table_prices is not None:
            table_in, table_out = table_prices
            if abs(table_in - live_in) > PRICE_MISMATCH_TOLERANCE or abs(table_out - live_out) > PRICE_MISMATCH_TOLERANCE:
                print(f"            WARNING: LLM_MODEL_COSTS has ({table_in}, {table_out}), "
                      f"live pricing is ({live_in}, {live_out}) -- update config.py")

    if ok:
        print("\nAll routed models available on plan.")
    else:
        print("\nOne or more routed models are missing or unavailable -- see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
