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


class TestOpenfigiApiKey:
    """WP7's noted next step: an OpenFIGI API key raises the anonymous
    25-req/minute limit to 25-req/6s (~10x) and the ISIN cap per LEI
    candidate is raised accordingly - verified live against OpenFIGI's
    own docs before these numbers were trusted (see module docstring).
    Read lazily from the environment, not a frozen constant, since
    load_dotenv() only runs in each caller's own __main__, after this
    module is already imported - these tests set/clear the env var
    directly rather than relying on .env."""

    def test_no_key_uses_anonymous_limits(self, er, monkeypatch):
        monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
        assert er._openfigi_api_key() is None
        assert er._rate_limit_delay() == er.ANONYMOUS_RATE_LIMIT_DELAY_SECONDS
        assert er._max_isins_per_lei() == er.MAX_ISINS_TO_CHECK_PER_LEI_ANONYMOUS

    def test_key_present_uses_faster_limits(self, er, monkeypatch):
        monkeypatch.setenv("OPENFIGI_API_KEY", "test-key-123")
        assert er._openfigi_api_key() == "test-key-123"
        assert er._rate_limit_delay() == er.API_KEY_RATE_LIMIT_DELAY_SECONDS
        assert er._rate_limit_delay() < er.ANONYMOUS_RATE_LIMIT_DELAY_SECONDS
        assert er._max_isins_per_lei() == er.MAX_ISINS_TO_CHECK_PER_LEI_WITH_KEY
        assert er._max_isins_per_lei() > er.MAX_ISINS_TO_CHECK_PER_LEI_ANONYMOUS

    def test_blank_key_treated_as_absent(self, er, monkeypatch):
        """An empty string (e.g. an unset .env placeholder) must not be
        sent as a real API key - falls back to anonymous limits."""
        monkeypatch.setenv("OPENFIGI_API_KEY", "")
        assert er._openfigi_api_key() is None
        assert er._rate_limit_delay() == er.ANONYMOUS_RATE_LIMIT_DELAY_SECONDS


class TestOpenfigiTimeoutRetry:
    """Real bug found running the API-key path live, not assumed: Renault
    resolved cleanly in an isolated single-company test, then came back
    UNRESOLVED in a full 40-company batch run minutes later - the batch
    log showed OpenFIGI read/connect timeouts that _post_openfigi_with_retry
    silently treated as 'no hit', not a transient failure worth retrying.
    Fixed by retrying Timeout/ConnectionError the same way a 429 already
    was - these tests lock that fix in with a monkeypatched requests.post,
    not a real network call (matching this file's own established pattern
    for the pure/mockable parts of this module)."""

    def test_retries_a_timeout_then_succeeds(self, er, monkeypatch):
        import requests as requests_module

        calls = {"n": 0}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return [{"data": []}]

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests_module.exceptions.Timeout("simulated timeout")
            return FakeResponse()

        monkeypatch.setattr(er.requests, "post", fake_post)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)  # don't actually wait in tests
        resp = er._post_openfigi_with_retry("FR0000131906")
        assert calls["n"] == 2
        assert resp.status_code == 200

    def test_retries_a_connection_error_then_succeeds(self, er, monkeypatch):
        import requests as requests_module

        calls = {"n": 0}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return [{"data": []}]

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests_module.exceptions.ConnectionError("simulated connection error")
            return FakeResponse()

        monkeypatch.setattr(er.requests, "post", fake_post)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        resp = er._post_openfigi_with_retry("SE0010468108")
        assert calls["n"] == 2
        assert resp.status_code == 200

    def test_gives_up_after_max_retries_of_persistent_timeouts(self, er, monkeypatch):
        import requests as requests_module

        def always_times_out(*args, **kwargs):
            raise requests_module.exceptions.Timeout("simulated persistent timeout")

        monkeypatch.setattr(er.requests, "post", always_times_out)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        with pytest.raises(RuntimeError):
            er._post_openfigi_with_retry("FR0000000000", max_retries=2)

    def test_retries_a_5xx_server_error_then_succeeds(self, er, monkeypatch):
        """The third bug found chasing the same residual flakiness:
        raise_for_status() on a plain HTTP 5xx was never caught by this
        function at all - it escaped the retry loop entirely and was
        silently treated one level up as 'no hit for this ISIN', which is
        how Thales's and Eni's real equity ISINs (confirmed independently
        to be well within the checked range) could still vanish even
        after the 429/timeout fixes above."""
        calls = {"n": 0}

        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise er.requests.exceptions.HTTPError(f"{self.status_code} error")
            def json(self):
                return [{"data": []}]

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return FakeResponse(503 if calls["n"] == 1 else 200)

        monkeypatch.setattr(er.requests, "post", fake_post)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        resp = er._post_openfigi_with_retry("FR0000121329")
        assert calls["n"] == 2
        assert resp.status_code == 200

    def test_a_4xx_other_than_429_is_not_retried(self, er, monkeypatch):
        """A genuine client error (e.g. 400 malformed ISIN) is not
        transient - retrying it would just waste time re-asking the same
        broken question, so only 429 and 5xx get the backoff-and-retry
        treatment."""
        calls = {"n": 0}

        class FakeResponse:
            status_code = 400
            def raise_for_status(self):
                raise er.requests.exceptions.HTTPError("400 error")
            def json(self):
                return [{"data": []}]

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return FakeResponse()

        monkeypatch.setattr(er.requests, "post", fake_post)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        with pytest.raises(er.requests.exceptions.HTTPError):
            er._post_openfigi_with_retry("BAD_ISIN")
        assert calls["n"] == 1  # not retried


class TestResolveWorkingTickerRetry:
    """The second real bug from the same live run as TestOpenfigiTimeoutRetry
    above: resolve_working_ticker() had a bare except-and-continue with no
    retry on its yfinance validation call - a single transient hiccup
    under batch load silently killed a candidate that was genuinely
    correct (Renault/RNO.PA, verified separately to actually work).
    Fixed with the same retry-with-backoff shape as the OpenFIGI path;
    these tests lock it in with a monkeypatched v19.fetch_market_data,
    not a real yfinance call."""

    def test_retries_a_transient_failure_then_succeeds(self, er, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(ticker):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated transient yfinance error")
            return {"market_cap": 8_000_000_000, "price": 50, "currency": "EUR"}

        monkeypatch.setattr(er.v19, "fetch_market_data", fake_fetch)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        result = er.resolve_working_ticker("RNO.PA")
        assert result == "RNO.PA"
        assert calls["n"] == 2

    def test_gives_up_after_max_retries_and_tries_next_variant(self, er, monkeypatch):
        """A ticker that persistently fails validation (not transient -
        genuinely broken) still falls through to the hyphenated
        share-class variant, same as before this fix."""
        calls = []

        def fake_fetch(ticker):
            calls.append(ticker)
            if ticker == "ESSITYB.ST":
                raise RuntimeError("persistently broken")
            return {"market_cap": 5_000_000_000, "price": 20, "currency": "SEK"}

        monkeypatch.setattr(er.v19, "fetch_market_data", fake_fetch)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        result = er.resolve_working_ticker("ESSITYB.ST", max_retries=2)
        assert result == "ESSITY-B.ST"
        assert calls.count("ESSITYB.ST") == 2  # retried once, then moved to the next variant

    def test_a_clean_response_with_no_market_cap_is_not_retried(self, er, monkeypatch):
        """Not every failure is transient - a response that comes back
        clean but with no market_cap is a real 'no data' answer, not
        something retrying would fix, so it should move on immediately
        rather than burning retries on it. Uses "ZZ.PA" specifically
        because its base is too short for _ticker_variants to add a
        second, hyphenated variant (see TestTickerVariants' own
        "too short a base" case) - so exactly one call is expected."""
        calls = {"n": 0}

        def fake_fetch(ticker):
            calls["n"] += 1
            return {"market_cap": None, "price": None, "currency": None}

        monkeypatch.setattr(er.v19, "fetch_market_data", fake_fetch)
        monkeypatch.setattr(er.time, "sleep", lambda s: None)
        result = er.resolve_working_ticker("ZZ.PA")
        assert result is None
        assert calls["n"] == 1  # not retried 3x for a clean-but-empty response
