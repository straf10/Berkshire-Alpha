"""alpaca client classes may only be imported by the wrapper modules
(docs/day2-spine-plan.md §0.1). Enforcement, not convention: a 6-second scan
turning into a 6-second event-loop freeze is the exact regression this test
exists to catch.

The check scans application code under agent/ only -- agent/tests/ is
exempt, since agent/tests/capture_fixtures.py is a deliberate, run-by-hand,
one-time offline script (never part of the running pipeline) that needs the
SDK's request/enum types to build fixtures.
"""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED = {
    Path("agent/execution/alpaca_client.py"),
    Path("agent/tools/market_data.py"),
    Path("agent/execution/broker.py"),
}

IMPORT_RE = re.compile(r"^\s*(from alpaca\.|import alpaca\b)")

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sdk_imports_confined() -> None:
    agent_dir = REPO_ROOT / "agent"
    violations = []
    for path in agent_dir.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if rel in ALLOWED or rel.parts[1] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if any(IMPORT_RE.match(line) for line in text.splitlines()):
            violations.append(str(rel))
    assert not violations, f"alpaca.* imported outside wrapper modules: {violations}"
