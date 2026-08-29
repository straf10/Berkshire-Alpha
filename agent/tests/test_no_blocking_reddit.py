"""praw may only be imported by agent/tools/reddit.py (docs/day3-llm-plan.md
S0.2) -- the same confinement discipline as test_no_blocking_sdk.py, so a
blocking praw call can never freeze the event loop from anywhere else."""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED = {Path("agent/tools/reddit.py")}

IMPORT_RE = re.compile(r"^\s*(from praw\b|import praw\b)")

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_blocking_reddit() -> None:
    agent_dir = REPO_ROOT / "agent"
    violations = []
    for path in agent_dir.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if rel in ALLOWED or rel.parts[1] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if any(IMPORT_RE.match(line) for line in text.splitlines()):
            violations.append(str(rel))
    assert not violations, f"praw imported outside agent/tools/reddit.py: {violations}"
