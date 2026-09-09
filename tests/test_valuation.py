"""
tests/test_valuation.py

Regression tests for scripts/19_valuation.py's populate_sector_std()
(PLAN.md WP3a). Needs a live DATABASE_URL and yfinance network access to
mean anything (real sector lookups against real tickers), so it's
skipped, not failed, when DATABASE_URL is absent - same deliberate,
narrow exception as tests/test_baseline_regression.py.
"""
import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="needs a live DATABASE_URL and yfinance access - see module docstring",
)


@pytest.fixture(scope="module")
def valuation(load_script):
    return load_script("19_valuation.py")


@pytest.fixture(scope="module")
def db_engine():
    return create_engine(DATABASE_URL)


def test_populate_sector_std_resolves_every_ticker_mapped_company(valuation, db_engine):
    """The real point of WP3a: company.sector (free text) had 9 distinct
    strings across 11 companies, so add_peer_stats()/add_implied_valuation()
    almost never found >= 2 peers. This asserts every TICKER_MAP company
    now has a real, non-guessed sector_std."""
    valuation.populate_sector_std(db_engine, force=True)
    with db_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name, sector_std, sector_source FROM company WHERE name = ANY(:names)"
        ), {"names": list(valuation.TICKER_MAP.keys())}).fetchall()

    missing = [r[0] for r in rows if not r[1]]
    assert not missing, f"companies with no sector_std resolved: {missing}"
    assert all(r[2] == "yfinance" for r in rows), "sector_source must record where sector_std came from"


def test_populate_sector_std_produces_at_least_one_real_peer_group(valuation, db_engine):
    """Verified live (see PLAN.md WP3a): the 11 companies collapse into
    Consumer Defensive (5), Consumer Cyclical (3), Healthcare (2) and
    Energy (1, correctly alone) - at least one group must have >= 2
    members, or this WP's whole premise (peer medians becoming meaningful)
    would be false."""
    valuation.populate_sector_std(db_engine, force=True)
    with db_engine.connect() as conn:
        group_sizes = conn.execute(text(
            "SELECT sector_std, COUNT(*) FROM company "
            "WHERE sector_std IS NOT NULL GROUP BY sector_std"
        )).fetchall()

    assert any(count >= 2 for _, count in group_sizes), (
        f"no sector_std group has >= 2 members - peer medians still meaningless: {group_sizes}"
    )


def test_populate_sector_std_skips_already_populated_companies_by_default(valuation, db_engine):
    """force=False (the default, used by 19_valuation.py's __main__) should
    not re-query yfinance for a company that already has sector_std -
    cheap to verify indirectly: after a full force=True pass, a second
    non-forced call should update zero rows."""
    valuation.populate_sector_std(db_engine, force=True)
    n_updated = valuation.populate_sector_std(db_engine, force=False)
    assert n_updated == 0
