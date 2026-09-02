from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

# The five models below are transcribed VERBATIM from plan.md's "Schema
# enforcement" section (field names, types, Literal members, and
# descriptions), with exactly one deviation: plan.md's SpreadProposal.legs
# constraint uses the pydantic v1 list-length kwargs. This repo runs pydantic
# 2.10.4, where those kwargs still work but emit DeprecationWarning (removed
# in v3), so they are transcribed here using pydantic v2's equivalent
# length-constraint kwargs instead -- identical semantics, identical JSON
# Schema output, no warning (docs/day3_llm_plan.md S0.5).


class QuantAnalystOutput(BaseModel):
    ticker: str
    iv_rv_interpretation: Literal["RICH", "CHEAP", "NEUTRAL"]
    skew_bias: Literal["BULLISH", "BEARISH", "FLAT"]
    directional_momentum: Literal["STRONG_UP", "WEAK_UP", "NEUTRAL", "WEAK_DOWN", "STRONG_DOWN"]
    key_levels: List[float] = Field(..., description="Support/resistance derived from VWAP")
    analyst_summary: str


class DebateNodeOutput(BaseModel):
    agent_persona: Literal["BULL", "BEAR"]
    doc_action: Literal["DISAGREE", "COMMIT"]
    evidence_cited: List[str]
    volatility_view: str
    rebuttal_argument: str


class OptionLegProposal(BaseModel):
    contract_type: Literal["CALL", "PUT"]
    side: Literal["BUY", "SELL"]
    strike_price: float
    ratio_qty: int = Field(..., ge=1, le=4)


class SpreadProposal(BaseModel):
    underlying: str
    strategy_name: str
    expiration_date: str                     # YYYY-MM-DD
    legs: List[OptionLegProposal] = Field(..., min_length=2, max_length=4)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str


class RiskManagerOutput(BaseModel):
    persona: Literal["AGGRESSIVE", "NEUTRAL", "CONSERVATIVE"]
    decision: Literal["APPROVE", "REJECT", "RESIZE"]
    max_loss_acceptable: bool
    risk_reward_ratio_acceptable: bool
    manager_notes: str


# plan.md defines no schema for the news analyst -- [NEW],
# docs/day3_llm_plan.md Group 3, same house style as the other models above.


class NewsAnalystOutput(BaseModel):
    ticker: str
    catalyst_summary: str
    expected_impact: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    impact_horizon_days: int = Field(..., ge=0, le=14)
    headline_ids_cited: List[str]
    analyst_summary: str


# Day 4 (docs/day4_action_plan.md Step 5) -- same house style as the two
# analyst outputs above.


class ReflectorOutput(BaseModel):
    verdict: Literal["LOOSEN", "HOLD", "TIGHTEN"]
    argument: str = Field(..., min_length=40, max_length=1200)
    proposed_change: str | None = Field(default=None, max_length=120)
