from __future__ import annotations

import json
from dataclasses import dataclass

from agent.schemas.llm import NewsAnalystOutput, QuantAnalystOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.macro import MacroSnapshot
from agent.strategy.regime import RegimeDecision
from agent.tools.news import Headline

# The DoC protocol is only meaningful if "cited evidence" is checkable. This
# structure produces both the prompt payload and the citation whitelist, so
# they cannot drift (docs/day3_llm_plan.md Group 3).


@dataclass(frozen=True)
class EvidenceBundle:
    symbol: str
    quant: QuantSnapshot                       # deterministic, ALWAYS present
    regime: RegimeDecision                     # deterministic, ALWAYS present
    macro: MacroSnapshot                       # deterministic, ALWAYS present (UNAVAILABLE is a valid reading, not None)
    quant_analyst: QuantAnalystOutput | None
    news_analyst: NewsAnalystOutput | None
    headlines: tuple[Headline, ...]

    def keys(self) -> frozenset[str]:
        """Stable citation tokens. Only keys for analysts that actually
        succeeded appear -- an agent cannot cite evidence that does not exist."""
        ks = {
            "quant.vrp_ratio", "quant.skew_abs", "quant.rsi", "quant.vwm_z",
            "quant.vwap_dev_pct", "regime.structure",
            "macro.regime", "macro.detail",
        }
        if self.quant_analyst is not None:
            ks |= {
                "quant_analyst.iv_rv_interpretation", "quant_analyst.skew_bias",
                "quant_analyst.directional_momentum",
            }
        if self.news_analyst is not None:
            ks |= {"news.expected_impact", "news.catalyst"}
        return frozenset(ks)

    def to_prompt_json(self) -> str:
        """Compact JSON, floats to 3 dp, separators=(',', ':'). ~250 tokens.
        Top-level keys ARE the citation tokens from keys() (plus `symbol`),
        so a valid citation is always a literal substring of this payload."""
        d: dict[str, object] = {
            "symbol": self.symbol,
            "quant.vrp_ratio": round(self.quant.vrp_ratio, 3),
            "quant.skew_abs": round(self.quant.skew_abs, 3),
            "quant.rsi": round(self.quant.rsi, 3),
            "quant.vwm_z": round(self.quant.vwm_z, 3),
            "quant.vwap_dev_pct": round(self.quant.vwap_dev_pct, 3),
            "regime.structure": self.regime.structure.value if self.regime.structure else None,
            "macro.regime": self.macro.regime.value,
            "macro.detail": self.macro.detail,
        }
        if self.quant_analyst is not None:
            d["quant_analyst.iv_rv_interpretation"] = self.quant_analyst.iv_rv_interpretation
            d["quant_analyst.skew_bias"] = self.quant_analyst.skew_bias
            d["quant_analyst.directional_momentum"] = self.quant_analyst.directional_momentum
        if self.news_analyst is not None:
            d["news.expected_impact"] = self.news_analyst.expected_impact
            d["news.catalyst"] = self.news_analyst.catalyst_summary
        return json.dumps(d, separators=(",", ":"))
