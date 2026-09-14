"""
tests/test_universe_membership.py

Regression tests for scripts/29_universe_membership.py's evaluate_candidate() -
the pure decision logic (does this candidate qualify?), with
26_entity_resolution.py's resolve_company() and 19_valuation.py's
fetch_market_data()/fetch_live_fx_rate() monkeypatched, same pattern
test_claude_classify.py already uses for a network-calling function
under test. The real network path (does resolve_company/fetch_market_data
actually work against live GLEIF/OpenFIGI/yfinance) is exercised live via
`python scripts/29_universe_membership.py --candidates ... --no-db`, not
mocked here - this project's established pattern for anything that talks
to a real external API (see test_entity_resolution.py's own docstring).
"""
import pytest


@pytest.fixture(scope="module")
def um(load_script):
    return load_script("29_universe_membership.py")


def _patch_resolution(monkeypatch, um, resolution):
    monkeypatch.setattr(um.er, "resolve_company", lambda name, country: resolution)


def _patch_market(monkeypatch, um, market=None, market_exc=None, rate=1.0, rate_exc=None):
    def fake_market_data(ticker):
        if market_exc:
            raise market_exc
        return market
    def fake_fx_rate(currency):
        if rate_exc:
            raise rate_exc
        return rate
    monkeypatch.setattr(um.v19, "fetch_market_data", fake_market_data)
    monkeypatch.setattr(um.v19, "fetch_live_fx_rate", fake_fx_rate)


UNRESOLVED = {"lei": None, "isin": None, "ticker": None, "ticker_exchange": None,
              "ticker_currency": None, "ticker_source": "UNRESOLVED"}


def test_unresolved_ticker_returns_none_not_excluded(um, monkeypatch):
    _patch_resolution(monkeypatch, um, UNRESOLVED)
    assert um.evaluate_candidate("Some Company", "France") is None


def test_below_threshold_returns_none(um, monkeypatch):
    _patch_resolution(monkeypatch, um, {
        "lei": "LEI123", "ticker": "SMALL.PA", "ticker_currency": "EUR", "ticker_source": "gleif+openfigi",
    })
    _patch_market(monkeypatch, um, market={"market_cap": 500_000_000, "currency": "EUR"})
    assert um.evaluate_candidate("Small Cap Co", "France") is None


def test_qualifying_eur_candidate_produces_a_row(um, monkeypatch):
    _patch_resolution(monkeypatch, um, {
        "lei": "LEI999", "ticker": "BIG.PA", "ticker_currency": "EUR", "ticker_source": "gleif+openfigi",
    })
    _patch_market(monkeypatch, um, market={"market_cap": 5_000_000_000, "currency": "EUR"})
    row = um.evaluate_candidate("Big Co", "France")
    assert row is not None
    assert row["entity_identifier"] == "LEI999"
    assert row["inclusion_rule"] == "market_cap_gt_2bn"
    assert row["ticker"] == "BIG.PA"


def test_non_eur_market_cap_is_converted_before_the_threshold_check(um, monkeypatch):
    """A candidate quoted in SEK with a nominally huge raw number must be
    converted to EUR before comparing to the threshold - not compared in
    its native currency, which would over- or under-qualify it."""
    _patch_resolution(monkeypatch, um, {
        "lei": "LEISE", "ticker": "BIG.ST", "ticker_currency": "SEK", "ticker_source": "gleif+openfigi",
    })
    # 30bn SEK / 10 (EUR/SEK) = 3bn EUR - qualifies
    _patch_market(monkeypatch, um, market={"market_cap": 30_000_000_000, "currency": "SEK"}, rate=10.0)
    row = um.evaluate_candidate("Swedish Co", "Sweden")
    assert row is not None
    assert "EUR 3.0bn" in row["inclusion_detail"]


def test_non_eur_market_cap_below_threshold_after_conversion(um, monkeypatch):
    # 15bn SEK / 10 = 1.5bn EUR - does NOT qualify, even though the raw
    # SEK figure alone looks large
    _patch_resolution(monkeypatch, um, {
        "lei": "LEISE2", "ticker": "MID.ST", "ticker_currency": "SEK", "ticker_source": "gleif+openfigi",
    })
    _patch_market(monkeypatch, um, market={"market_cap": 15_000_000_000, "currency": "SEK"}, rate=10.0)
    assert um.evaluate_candidate("Mid Swedish Co", "Sweden") is None


def test_market_data_fetch_failure_returns_none_not_a_crash(um, monkeypatch):
    _patch_resolution(monkeypatch, um, {
        "lei": "LEIX", "ticker": "BROKEN.PA", "ticker_currency": "EUR", "ticker_source": "gleif+openfigi",
    })
    _patch_market(monkeypatch, um, market_exc=RuntimeError("yfinance down"))
    assert um.evaluate_candidate("Broken Co", "France") is None


def test_fx_failure_returns_none_not_a_crash(um, monkeypatch):
    _patch_resolution(monkeypatch, um, {
        "lei": "LEIY", "ticker": "FXBROKEN.ST", "ticker_currency": "SEK", "ticker_source": "gleif+openfigi",
    })
    _patch_market(monkeypatch, um, market={"market_cap": 30_000_000_000, "currency": "SEK"},
                  rate_exc=ValueError("no live rate"))
    assert um.evaluate_candidate("FX Broken Co", "Sweden") is None
