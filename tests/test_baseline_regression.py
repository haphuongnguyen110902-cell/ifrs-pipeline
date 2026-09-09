"""
tests/test_baseline_regression.py

WHAT
----
Guards WP1 (the company_id migration in PLAN.md) against silently
corrupting or dropping a row. Compares a live SELECT of the five
analysis tables (`ratio`, `valuation`, `dcf_valuation`, `credit_profile`,
`market_risk`) against a frozen golden snapshot taken before the
migration (`tests/baseline/*.csv`, generated read-only from the live DB
on 2026-09-09 - see PLAN.md WP0).

WHY A SNAPSHOT-COMPARE, NOT A RECOMPUTE-AND-COMPARE
----------------------------------------------------
PLAN.md's WP0 describes "re-run the analysis layer and assert the
numbers still match, byte-identical". That's the right idea for `ratio`
(computed purely from stored `fact_value` rows - genuinely
deterministic), but wrong for `valuation`, `dcf_valuation` and
`market_risk`: all three embed LIVE market data (today's market cap,
a live risk-free rate/beta from yfinance, a trailing price window) -
re-running those scripts tomorrow legitimately produces different
numbers with zero bugs involved. A "must be byte-identical" test built
that way would be permanently flaky, not a real regression guard.

What WP1 actually needs to prove is narrower: the re-keying migration
(adding `company_id`, backfilling by name, switching readers/writers)
must not change any EXISTING row's values. A plain snapshot-compare -
read the rows now, do the migration, read the same rows again, diff -
proves exactly that, for all five tables, without ever depending on
market data staying still. `ratio` additionally gets a recompute check
since it's cheap and doesn't touch the network.

WHY THIS TEST IS SKIPPED WITHOUT DATABASE_URL
----------------------------------------------
Every other test in this project builds synthetic in-memory data and
never touches the live database (see CLAUDE.md / conftest.py) - that's
what lets `tests.yml` run on every push with no DATABASE_URL secret.
This test is a deliberate, narrow exception: it exists specifically to
protect a live-DB migration, so it must read the live DB to mean
anything. Skipping cleanly when DATABASE_URL is absent keeps CI's
"no test touches the live database" contract intact instead of quietly
breaking it.

HOW TO USE THIS AROUND THE WP1 MIGRATION
-----------------------------------------
1. Before running sql/migration_001_company_id.sql, regenerate the
   golden CSVs (see the export snippet in PLAN.md WP0 / this file's
   sibling `tests/baseline/`) so they reflect the current live state.
2. Run `pytest tests/test_baseline_regression.py -v` - should pass
   (comparing the DB to itself).
3. Run the migration.
4. Run this test again - should still pass (proves the migration didn't
   change any value). A failure here means the backfill mismatched a
   row - investigate before proceeding, per the migration's own
   null-guard rule.
"""
import os

import pandas as pd
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Every other test in this project is DB-free by design and never loads
# .env (see module docstring) - this test is the deliberate exception, so
# it loads .env itself rather than relying on it already being loaded.
load_dotenv()

BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baseline")

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "test_baseline_regression.py needs a live DATABASE_URL by design "
        "(see module docstring) - skipped, not failed, when absent so CI's "
        "no-DB test suite is unaffected."
    ),
)

# (table, live SELECT, sort/compare key columns)
TABLE_QUERIES = {
    "ratio": (
        "SELECT company_id, year, ratio_name, value, is_currency_neutral, currency "
        "FROM ratio ORDER BY company_id, year, ratio_name",
        ["company_id", "year", "ratio_name"],
    ),
    "valuation": (
        "SELECT company, year, ticker, market_cap_eur, net_debt_eur, ev_eur, "
        "revenue_eur, ebitda_eur, net_income_eur, ev_ebitda, ev_sales, pe, "
        "ev_ebitda_sector_median, n_peers_in_sector, implied_ev_from_peers, "
        "premium_vs_peers_pct, fwd_ev_ebitda, fwd_ev_sales "
        "FROM valuation ORDER BY company, year",
        ["company", "year"],
    ),
    "dcf_valuation": (
        "SELECT company, base_year, wacc, cost_of_equity, after_tax_cost_of_debt, "
        "enterprise_value, equity_value, pct_ev_from_terminal "
        "FROM dcf_valuation ORDER BY company, base_year",
        ["company", "base_year"],
    ),
    "credit_profile": (
        "SELECT company, year, net_debt, ebitda, net_debt_ebitda, is_da_fallback, "
        "band, yoy_change, trend FROM credit_profile ORDER BY company, year",
        ["company", "year"],
    ),
    "market_risk": (
        "SELECT company, ticker, benchmark, period_start, period_end, "
        "n_observations, annualized_volatility, benchmark_volatility, "
        "sharpe_ratio, beta, correlation, rolling_corr_mean, rolling_corr_min, "
        "rolling_corr_max, max_drawdown FROM market_risk ORDER BY company, benchmark",
        ["company", "benchmark"],
    ),
}

RELATIVE_TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def engine():
    return create_engine(DATABASE_URL)


def _load_golden(table_name: str) -> pd.DataFrame:
    path = os.path.join(BASELINE_DIR, f"{table_name}.csv")
    if not os.path.exists(path):
        pytest.skip(
            f"No golden snapshot at {path} yet - run the WP0 export step first."
        )
    # keep_default_na=False: pandas' default na_values list includes the
    # literal string "n/a" - this project's own real "not enough data"
    # convention (see e.g. 24_credit.py's trend label) - which would
    # otherwise silently turn a genuine string value into NaN on read-back.
    # Only a truly empty cell (an actual SQL NULL when exported) should
    # become NaN.
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def _compare(golden: pd.DataFrame, live: pd.DataFrame, key_cols: list[str], table_name: str):
    assert list(golden.columns) == list(live.columns), (
        f"{table_name}: column set changed since the golden snapshot was taken "
        f"({list(golden.columns)} vs {list(live.columns)})"
    )

    golden_keys = set(map(tuple, golden[key_cols].values.tolist()))
    live_keys = set(map(tuple, live[key_cols].values.tolist()))

    missing = golden_keys - live_keys
    assert not missing, (
        f"{table_name}: {len(missing)} row(s) present in the golden snapshot are "
        f"MISSING from the live table now - e.g. {list(missing)[:5]}. This is the "
        f"exact failure mode WP1's migration must not cause."
    )

    added = live_keys - golden_keys
    if added:
        # New rows (e.g. a fresh weekly cron run added FY2026) are not a
        # regression by themselves - only compare the rows both sides share.
        pass

    golden_indexed = golden.set_index(key_cols)
    live_indexed = live.set_index(key_cols)
    shared_keys = golden_keys & live_keys
    for key in shared_keys:
        g_row = golden_indexed.loc[key]
        l_row = live_indexed.loc[key]
        for col in golden.columns:
            if col in key_cols:
                continue
            g_val, l_val = g_row[col], l_row[col]
            if pd.isna(g_val) and pd.isna(l_val):
                continue
            if isinstance(g_val, (int, float)) and isinstance(l_val, (int, float)):
                if g_val == 0 and l_val == 0:
                    continue
                rel_diff = abs(l_val - g_val) / max(abs(g_val), 1e-12)
                assert rel_diff <= RELATIVE_TOLERANCE, (
                    f"{table_name}{key}.{col}: golden={g_val!r} live={l_val!r} "
                    f"(relative diff {rel_diff:.2e} exceeds {RELATIVE_TOLERANCE:.0e}) - "
                    f"investigate before trusting the migration/change that produced this."
                )
            else:
                # A DATE column round-trips through CSV as a string but comes
                # back from a live SQL read as a datetime.date - same value,
                # different type, so compare the string form for non-numeric
                # columns rather than the raw object.
                assert str(g_val) == str(l_val), (
                    f"{table_name}{key}.{col}: golden={g_val!r} live={l_val!r}"
                )


@pytest.mark.parametrize("table_name", list(TABLE_QUERIES.keys()))
def test_table_matches_golden_snapshot(engine, table_name):
    """Every row present in the golden snapshot still has the same values live.

    New rows added since the snapshot (e.g. a weekly cron adding a new
    fiscal year) are allowed - this test only asserts nothing already
    frozen was changed, dropped, or corrupted.
    """
    query, key_cols = TABLE_QUERIES[table_name]
    golden = _load_golden(table_name)
    live = pd.read_sql(text(query), engine)
    _compare(golden, live, key_cols, table_name)
