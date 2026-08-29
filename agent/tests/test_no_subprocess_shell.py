"""create_subprocess_shell is banned repo-wide: asyncio.create_subprocess_exec
passes argv directly to CreateProcess, so PowerShell 5.1's JSON-mangling
cannot occur (docs/day2-spine-plan.md §0.1). This pins that the shell variant
never creeps back in."""

from __future__ import annotations

import re
from pathlib import Path

PATTERN = re.compile(r"create_subprocess_shell|shell\s*=\s*True")

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_subprocess_shell() -> None:
    agent_dir = REPO_ROOT / "agent"
    self_path = Path(__file__).resolve()
    violations = []
    for path in agent_dir.rglob("*.py"):
        if path.resolve() == self_path:
            continue  # this file's own banned-pattern regex is not a violation
        text = path.read_text(encoding="utf-8")
        if PATTERN.search(text):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, f"banned subprocess-shell usage found in: {violations}"
