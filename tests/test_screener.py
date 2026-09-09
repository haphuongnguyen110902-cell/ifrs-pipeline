"""
tests/test_screener.py

Regression tests for sql/schema_screener.sql's company_latest_metrics
view (PLAN.md WP5). Needs a live DATABASE_URL to mean anything - it's a
SQL view over real tables, not a pure Python function - so this is
skipped, not failed, when DATABASE_URL is absent, same deliberate
exception as tests/test_baseline_regression.py.
"""
import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="needs a live DATABASE_URL - see module docstring",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DATABASE_URL)
    with open("sql/schema_screener.sql", encoding="utf-8") as f:
        ddl = f.read()
    with eng.begin() as conn:
        conn.execute(text(ddl))
    return eng


def test_view_returns_one_row_per_company(engine):
    with engine.connect() as conn:
        n_companies = conn.execute(text("SELECT COUNT(*) FROM company")).scalar()
        n_view_rows = conn.execute(text("SELECT COUNT(*) FROM company_latest_metrics")).scalar()
    assert n_view_rows == n_companies


def test_the_pernod_ricard_null_year_case_is_skipped_not_selected(engine):
    """The real bug found live: Pernod Ricard's numerically latest ratio
    year (2025) has value = NULL for operating_margin (its June 30
    fiscal year end leaves that year empty - the same case
    21_three_statement_model.py's select_base_year_row() exists for).
    Without filtering NULLs before ranking by year, DISTINCT ON ... ORDER
    BY year DESC would select that empty row instead of 2024's real one.
    Cross-checks the view's answer against the ratio table directly,
    rather than hand-coding the expected number, so this doesn't silently
    drift if the underlying ratio value is corrected later."""
    with engine.connect() as conn:
        view_value = conn.execute(text(
            "SELECT operating_margin FROM company_latest_metrics WHERE name = 'Pernod Ricard'"
        )).scalar()
        expected = conn.execute(text("""
            SELECT r.value FROM ratio r JOIN company c ON r.company_id = c.company_id
            WHERE c.name = 'Pernod Ricard' AND r.ratio_name = 'operating_margin'
              AND r.value IS NOT NULL
            ORDER BY r.year DESC LIMIT 1
        """)).scalar()

    assert view_value is not None, "Pernod Ricard's operating_margin came back NULL from the view"
    assert float(view_value) == pytest.approx(float(expected))


def test_no_unexpected_nulls_for_the_current_full_universe(engine):
    """Every one of the 11 current companies has real data for every
    metric this view computes (confirmed live) - a NULL appearing here
    would mean either a genuine data gap (fine, but should be
    investigated, not silently accepted) or the view's join logic
    regressed. This test exists to make that visible immediately rather
    than only noticed by someone reading the screener page."""
    with engine.connect() as conn:
        df_cols = conn.execute(text("SELECT * FROM company_latest_metrics LIMIT 1")).keys()
        null_counts = {}
        for col in df_cols:
            if col in ("company_id", "name"):
                continue
            n_null = conn.execute(text(
                f"SELECT COUNT(*) FROM company_latest_metrics WHERE {col} IS NULL"
            )).scalar()
            if n_null:
                null_counts[col] = n_null

    assert not null_counts, (
        f"Unexpected NULLs in company_latest_metrics: {null_counts} - "
        f"investigate before trusting the screener for these companies."
    )


def test_query_is_a_single_round_trip_not_n_plus_one(engine):
    """The whole point of this view (PLAN.md WP5): the landing page must
    be one query for the whole universe, not one per company. This
    doesn't measure query COUNT directly (that needs SQL-level tracing
    this test doesn't have access to) - it measures that a single
    `SELECT * FROM company_latest_metrics` alone returns every company's
    full metric set, so the webapp genuinely needs only one call here."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM company_latest_metrics")).fetchall()
        n_companies = conn.execute(text("SELECT COUNT(*) FROM company")).scalar()
    assert len(rows) == n_companies
    assert all(row.operating_margin is not None for row in rows)
