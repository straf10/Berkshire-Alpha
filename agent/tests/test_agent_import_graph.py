"""The invariant docs/day3-llm-plan.md S0.2 exists to protect: the LLM
proposes, the deterministic gate sizes and approves. agent/agents/* returns
values; the orchestrator (agent/main.py, agent/agents/pipeline.py) is the
only code that persists or executes."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BANNED_PREFIXES = ("agent.execution", "agent.risk", "agent.storage.write")


def test_agents_never_execute() -> None:
    agents_dir = REPO_ROOT / "agent" / "agents"
    violations = []
    for path in agents_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(BANNED_PREFIXES):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(BANNED_PREFIXES):
                        violations.append(f"{path.relative_to(REPO_ROOT)}: {alias.name}")
    assert not violations, f"agent/agents/* must not execute or persist: {violations}"


def test_confidence_never_reaches_sizing_or_gates() -> None:
    for rel in ("agent/risk", "agent/strategy"):
        for path in (REPO_ROOT / rel).rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "confidence_score" not in src, f"{path}: confidence_score must never enter sizing/gates"
