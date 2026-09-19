"""
tests/test_national_index_membership.py

Regression tests for scripts/30_national_index_membership.py's pure
logic (find_constituent_table, normalize_ticker) - synthetic DataFrames,
no real HTTP call (that's exercised live via
`python scripts/30_national_index_membership.py --no-db`, verified
against all 5 real Wikipedia pages before this script was trusted - see
PLAN.md). save_rows() (the DB-writing half) is DB-guarded, matching this
project's established pattern for anything that needs a live connection.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def nim(load_script):
    return load_script("30_national_index_membership.py")


# ---------------------------------------------------------------- find_constituent_table

def test_picks_the_table_whose_size_matches_the_real_index(nim):
    """The real bug this guards: BEL 20's Wikipedia page has a 20-row
    current-constituents table AND a 41-row historical/former-members
    table, both with a ticker-like column - picking the first match
    blindly would silently grab the wrong one."""
    current = pd.DataFrame({"Company": [f"C{i}" for i in range(20)], "Ticker symbol": [f"T{i}" for i in range(20)]})
    historical = pd.DataFrame({"Company": [f"H{i}" for i in range(41)], "Ticker symbol": [f"T{i}" for i in range(41)]})
    picked = nim.find_constituent_table([historical, current], expected_size=20)
    assert len(picked) == 20
    assert picked is current


def test_ignores_tables_with_no_ticker_column(nim):
    no_ticker = pd.DataFrame({"Company": ["A", "B"], "Sector": ["X", "Y"]})
    has_ticker = pd.DataFrame({"Company": ["A", "B"], "Ticker": ["A.PA", "B.PA"]})
    picked = nim.find_constituent_table([no_ticker, has_ticker], expected_size=2)
    assert picked is has_ticker


def test_returns_none_when_nothing_matches(nim):
    t = pd.DataFrame({"Foo": [1, 2], "Bar": [3, 4]})
    assert nim.find_constituent_table([t], expected_size=2) is None


# ---------------------------------------------------------------- clean_company_name

@pytest.mark.parametrize("raw,expected", [
    ("Melexis\xa0[nl]", "Melexis"),
    ("WDP [nl]", "WDP"),
    ("arGEN-X [nl]", "arGEN-X"),
    ("Foo Bar [1][2]", "Foo Bar"),
    ("Hermès", "Hermès"),
    ("  Assa   Abloy B ", "Assa Abloy B"),
])
def test_clean_company_name_strips_scrape_artifacts(nim, raw, expected):
    """Real artifacts found live in the national_index rows: a non-breaking
    space plus a trailing '[nl]' language marker."""
    assert nim.clean_company_name(raw) == expected


# ---------------------------------------------------------------- normalize_ticker

def test_already_yfinance_ready_ticker_passes_through(nim):
    assert nim.normalize_ticker("AC.PA") == "AC.PA"


def test_exchange_prefixed_ticker_is_parsed(nim):
    """BEL 20's real raw format: 'Euronext Brussels:ABI' -> 'ABI.BR'."""
    assert nim.normalize_ticker("Euronext Brussels:ABI") == "ABI.BR"


def test_handles_the_real_nbsp_artifact_found_live(nim):
    """The real BEL 20 page's raw value has a non-breaking space instead
    of a plain colon-adjacent space - found live, not assumed."""
    assert nim.normalize_ticker("Euronext Brussels:\xa0ABI") == "ABI.BR"


def test_unmapped_exchange_returns_none_not_a_guess(nim):
    assert nim.normalize_ticker("Warsaw Stock Exchange:XYZ") is None


def test_bare_code_with_no_suffix_and_no_colon_returns_none(nim):
    """A ticker with neither a recognised exchange prefix nor an
    existing suffix has nothing to build a yfinance ticker from - must
    not be guessed at."""
    assert nim.normalize_ticker("ABI") is None
