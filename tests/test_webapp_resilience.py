"""
tests/test_webapp_resilience.py

webapp/app.py is a Streamlit script (top-level code runs on import), so these
tests read its source with `ast` instead of importing it - the same approach
tests/test_financial_gating.py uses for the dashboard caption.

The real failure: the app caches one SQLAlchemy engine for its whole life, but
Neon (serverless Postgres) closes idle connections. Without `pool_pre_ping`
the pool handed the app a dead connection and the first click after a quiet
spell crashed the Ratios tab with `pandas.errors.DatabaseError` ->
`OperationalError` (observed live 2026-09-19; the identical click worked after
a page reload). Reproduced locally by closing a pooled connection's socket:
`create_engine(url)` fails, `create_engine(url, pool_pre_ping=True)` recovers.
"""
import ast
from pathlib import Path

APP = Path(__file__).parent.parent / "webapp" / "app.py"


def _create_engine_calls():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "create_engine"]


def _kwargs(call):
    return {k.arg: k.value for k in call.keywords if k.arg}


def test_the_app_creates_exactly_one_engine():
    assert len(_create_engine_calls()) == 1


def test_the_cached_engine_pings_a_connection_before_using_it():
    kwargs = _kwargs(_create_engine_calls()[0])
    assert "pool_pre_ping" in kwargs and ast.literal_eval(kwargs["pool_pre_ping"]) is True


def test_the_cached_engine_recycles_connections_before_neons_idle_timeout():
    """Neon suspends idle compute after ~5 minutes; recycling at or below that
    keeps the pool from holding connections the server has already dropped."""
    kwargs = _kwargs(_create_engine_calls()[0])
    assert "pool_recycle" in kwargs
    assert 0 < ast.literal_eval(kwargs["pool_recycle"]) <= 300
