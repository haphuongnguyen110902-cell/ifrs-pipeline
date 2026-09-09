"""
tests/test_entity_resolution.py

Regression tests for scripts/26_entity_resolution.py's PURE ranking
logic (pick_best_equity_hit) - the network-calling functions
(resolve_lei_candidates, fetch_isins_for_lei, resolve_ticker_from_isins,
resolve_company) are deliberately not unit-tested here; they're
exercised live via `python scripts/26_entity_resolution.py --verify`
(PLAN.md WP4's own required verification step - spot-check against
19_valuation.py's hand-verified TICKER_MAP), not against synthetic data,
since the whole point is confirming real GLEIF/OpenFIGI behavior.

pick_best_equity_hit() was deliberately extracted as a separate function
specifically so this one piece - "given already-fetched OpenFIGI rows,
which one is the right listing" - is testable without mocking HTTP.
"""
import pytest


@pytest.fixture(scope="module")
def er(load_script):
    return load_script("26_entity_resolution.py")


def test_no_hits_returns_none(er):
    assert er.pick_best_equity_hit([]) is None


def test_single_known_exchange_hit_resolves_to_yfinance_suffix(er):
    hits = [{"ticker": "OR", "exchCode": "FP", "isin": "FR0000120321"}]
    result = er.pick_best_equity_hit(hits)
    assert result == {"ticker": "OR.PA", "exchange": "FP", "isin": "FR0000120321", "currency": "EUR"}


def test_the_real_loreal_case_prefers_paris_over_german_regional_listings(er):
    """The actual case found live: L'Oreal's OpenFIGI results include one
    Paris listing (ticker OR) and several German regional-exchange
    listings (ticker LOR, exchanges GR/GF/GD/GY/GS/GM/GI) all sharing the
    same underlying share class. Paris must win - it's the real listing
    19_valuation.py's hand-verified TICKER_MAP uses (OR.PA)."""
    hits = [
        {"ticker": "OR", "exchCode": "FP", "isin": "FR0000120321"},
        {"ticker": "LOR", "exchCode": "GR", "isin": "FR0000120321"},
        {"ticker": "LOR", "exchCode": "GF", "isin": "FR0000120321"},
    ]
    result = er.pick_best_equity_hit(hits, country_iso2="FR")
    assert result["ticker"] == "OR.PA"


def test_unknown_exchange_with_no_known_alternative_is_unresolved(er):
    """Never guess a suffix for an exchange this project hasn't verified
    (see EXCH_TO_YF_SUFFIX's own docstring) - an unmapped exchange with
    nothing else to fall back on must return None, not a made-up ticker."""
    hits = [{"ticker": "XYZ", "exchCode": "ZZ", "isin": "XX0000000000"}]
    assert er.pick_best_equity_hit(hits) is None


def test_bare_ticker_exchange_gets_no_suffix(er):
    """Shell's real case: the US-style listing gets the bare ticker, no
    suffix - see BARE_TICKER_EXCHANGES and 19_valuation.py's TICKER_MAP
    ('SHEL', not 'SHEL.something')."""
    hits = [{"ticker": "SHEL", "exchCode": "US", "isin": "US7802592060"}]
    result = er.pick_best_equity_hit(hits)
    assert result == {"ticker": "SHEL", "exchange": "US", "isin": "US7802592060", "currency": "USD"}


def test_country_match_preferred_over_first_known_exchange(er):
    """When multiple known-exchange hits exist, the one whose ISIN
    country prefix matches the company's own country wins - guards
    against picking a foreign secondary listing just because it happened
    to come first in OpenFIGI's response order."""
    hits = [
        {"ticker": "FOO", "exchCode": "LN", "isin": "GB0000000000"},
        {"ticker": "FOO", "exchCode": "IM", "isin": "IT0000000000"},
    ]
    result = er.pick_best_equity_hit(hits, country_iso2="IT")
    assert result["exchange"] == "IM"
    assert result["ticker"] == "FOO.MI"


class TestTickerVariants:
    """_ticker_variants() (PLAN.md WP4) - the real bug this exists for:
    Essity's actual yfinance ticker is 'ESSITY-B.ST' (hyphenated share
    class), but OpenFIGI's raw ticker field + a naive suffix concat
    produces 'ESSITYB.ST', which 404s on yfinance (verified live).
    resolve_working_ticker() validates each variant against yfinance
    itself (not unit-tested here - needs network), but the pure
    candidate-generation logic is."""

    def test_raw_ticker_is_always_tried_first(self, er):
        variants = list(er._ticker_variants("ESSITYB.ST"))
        assert variants[0] == "ESSITYB.ST"

    def test_share_class_suffix_gets_a_hyphenated_variant(self, er):
        variants = list(er._ticker_variants("ESSITYB.ST"))
        assert "ESSITY-B.ST" in variants

    def test_ticker_with_no_suffix_still_gets_hyphenated_variant(self, er):
        variants = list(er._ticker_variants("ESSITYB"))
        assert "ESSITY-B" in variants

    def test_plain_ticker_with_no_share_class_pattern_gets_no_extra_variant(self, er):
        """A normal ticker like 'OR.PA' shouldn't spuriously get a
        hyphen inserted - only single-trailing-letter share-class-shaped
        tickers should."""
        variants = list(er._ticker_variants("OR.PA"))
        assert variants == ["OR.PA"]

    def test_too_short_a_base_does_not_get_hyphenated(self, er):
        """A 1-character base (nothing before the trailing letter) isn't
        a real share-class pattern - guards the regex's own length check."""
        variants = list(er._ticker_variants("A.PA"))
        assert variants == ["A.PA"]


def test_unknown_exchanges_only_falls_back_to_first_hit_but_still_unresolved(er):
    """All hits have unrecognized exchanges - pool falls back to the raw
    equity_hits list (no known exchange to prefer), but the final ticker
    build step still refuses to guess a suffix for an unmapped exchange."""
    hits = [
        {"ticker": "FOO", "exchCode": "ZZ", "isin": "ZZ0000000000"},
        {"ticker": "FOO", "exchCode": "YY", "isin": "YY0000000000"},
    ]
    assert er.pick_best_equity_hit(hits) is None
