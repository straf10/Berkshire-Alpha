from __future__ import annotations

from agent.backtest import dsr


def test_n_trials_matches_trial_ledger_backfill() -> None:
    """docs/strategy_audit_and_loop.md S3.1/S4 P1/P3: the ledger has grown to
    36 rows (docs/trial_ledger.md: 16 backfilled to 25, 26-28 for the
    p_success lognormal/ceiling/DTE-matched-RV trials, 29-32 for the
    CROSS_SECTION_N resize, the VWM_Z_STRONG/VRP_DEBIT_MAX decision pair, and
    the MAX_NET_SPREAD_WIDTH_PCT re-check, 33 for the synthetic chain's
    iv_atm forecast -- blend toward RV_20 instead of the short window alone,
    34 for docs/f1_f3_remediation_plan.md F1.4's winsorized composite_score
    normalisation, 35 for F1's vrp_ratio denominator revert back to RV_20 and
    the post-revert threshold re-examination it gated, 36 for
    docs/prompts/real_iv_surface_free.md Path C's REAL_CHAIN_SPREAD_PCT) --
    this constant must track it, not lead it (dsr.py's own comment)."""
    assert dsr.N_TRIALS == 36


def test_main_picks_up_agent_db_path_set_only_via_dotenv(monkeypatch, capsys) -> None:
    """docs/strategy_audit_and_loop.md S0/S3.1: dsr.py used to read
    os.environ.get("AGENT_DB_PATH", ...) directly, without ever calling
    load_dotenv() the way agent.config.load_settings() does -- an
    AGENT_DB_PATH set only in .env (this repo's normal way of pointing local
    tooling at production) was invisible to a bare os.environ lookup, so
    _main() silently fell through to the empty local ./agent.db default
    without any error. Stands in for a real .env file by making load_dotenv
    itself the thing that populates the environment, and asserts _main reads
    AGENT_DB_PATH only AFTER calling it, using the real postgres:// dispatch
    (agent.storage.db._is_postgres) to prove a Postgres DSN is recognised as
    such rather than silently coerced toward the SQLite path."""
    captured_paths: list[str] = []

    async def fake_daily_equity(db_path: str):
        captured_paths.append(db_path)
        return []

    def fake_load_dotenv() -> None:
        monkeypatch.setenv("AGENT_DB_PATH", "postgres://user:pass@host/db")

    monkeypatch.delenv("AGENT_DB_PATH", raising=False)
    monkeypatch.setattr(dsr, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(dsr, "_daily_equity", fake_daily_equity)

    dsr._main()

    assert captured_paths == ["postgres://user:pass@host/db"]
    assert "reading daily equity from: Postgres" in capsys.readouterr().out


def test_main_warns_when_still_reading_sqlite_with_too_little_data(monkeypatch, capsys) -> None:
    """The empty-local-sqlite failure mode dsr.py used to hit silently now
    prints an explicit, actionable warning instead of just '0 daily equity
    point(s)' -- the same class of fix as the rest of this audit: a
    misconfiguration must not look identical to a legitimately small sample."""
    async def fake_daily_equity(db_path: str):
        return []

    monkeypatch.delenv("AGENT_DB_PATH", raising=False)
    monkeypatch.setattr(dsr, "load_dotenv", lambda: None)
    monkeypatch.setattr(dsr, "_daily_equity", fake_daily_equity)

    dsr._main()

    out = capsys.readouterr().out
    assert "reading daily equity from: SQLite" in out
    assert "set AGENT_DB_PATH to its postgres:// DSN" in out
