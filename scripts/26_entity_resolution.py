"""
scripts/26_entity_resolution.py

WHAT
----
Resolves each company's LEI -> ISIN -> ticker/exchange, replacing
`19_valuation.py`'s hardcoded TICKER_MAP so a new company no longer
needs a source-code edit (PLAN.md WP4). Writes `isin`, `ticker`,
`ticker_exchange`, `ticker_source` (and backfills `lei` if not already
set) on the `company` table.

THE FREE PATH (all verified live, not assumed)
-----------------------------------------------
    company name        -> LEI      via GLEIF's own LEI-search API
                                     (api.gleif.org/api/v1/lei-records)
    LEI                  -> ISIN(s)  via GLEIF's per-LEI ISIN endpoint
                                     (.../lei-records/{lei}/isins) -
                                     NOT the ~1GB+ bulk relationship file
                                     PLAN.md originally specified. Found
                                     while building this: GLEIF exposes
                                     the same mapping per-LEI, live, which
                                     is far more practical at this
                                     project's scale (11 -> low hundreds)
                                     than downloading and indexing a bulk
                                     file for a few hundred lookups.
    ISIN(s)              -> ticker   via OpenFIGI's /v3/mapping (POST,
                            + exch    free, no key required at low
                                     volume - see RATE_LIMIT_DELAY below)

A REAL COMPLICATION FOUND BUILDING THIS, NOT ASSUMED: one LEI maps to
MANY ISINs, not one - L'Oreal's LEI alone has 32 (equity + multiple bond
issuances across currencies/tenors). Filtering OpenFIGI's response to
marketSector == "Equity" narrows this to just the common-stock listings,
but a large multinational is often listed on SEVERAL exchanges
simultaneously (L'Oreal: Paris "FP" as OR, plus several German regional
exchanges as "LOR") - EXCH_TO_YF_SUFFIX below is the (verified, not
guessed) preference order for picking the one this project's yfinance-based
downstream scripts actually want.

NEVER GUESS: a company that doesn't resolve gets ticker=NULL,
ticker_source='UNRESOLVED' - explicit unknown, not a fallback to a fuzzy
name search (yfinance's own search endpoint is undocumented and can
silently return the wrong company - rejected for exactly that reason,
see PLAN.md WP4).

USAGE
-----
    python scripts/26_entity_resolution.py                 # resolve every company missing a ticker
    python scripts/26_entity_resolution.py --company "L'Oreal"
    python scripts/26_entity_resolution.py --verify         # compare against 19_valuation.py's
                                                             # TICKER_MAP instead of writing anything -
                                                             # this is PLAN.md WP4's own required
                                                             # verification step, not a separate script
"""
import argparse
import importlib.util
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

_THIS_DIR = Path(__file__).parent
_v19_spec = importlib.util.spec_from_file_location("valuation_19", _THIS_DIR / "19_valuation.py")
v19 = importlib.util.module_from_spec(_v19_spec)
_v19_spec.loader.exec_module(v19)

GLEIF_API = "https://api.gleif.org/api/v1"
OPENFIGI_API = "https://api.openfigi.com/v3/mapping"

# OpenFIGI requests without an API key are rate-limited far more tightly
# than the commonly-quoted "25 requests/6 seconds" figure suggests -
# found live: even at 1 request/second, Danone's 60 bond ISINs alone
# triggered a wall of 429s. 2.5s keeps this under ~24/minute, verified to
# avoid 429s for a single company's ISIN list at this project's scale.
#
# WP7's own noted next step, now built: verified live against OpenFIGI's
# own documentation (not assumed) that a free API key raises the
# unauthenticated 25-requests-per-MINUTE limit to 25-per-6-SECONDS (~10x)
# and the per-request job batch size from 10 to 100. This script only
# takes the faster-delay half of that win for now (see
# resolve_ticker_from_isins' docstring for why true request batching - one
# POST covering many ISINs at once - is a further, not-yet-built
# optimization, not done here). Read lazily inside the functions below,
# not as a frozen constant, since load_dotenv() runs in each caller's own
# __main__, after this module is imported.
ANONYMOUS_RATE_LIMIT_DELAY_SECONDS = 2.5
API_KEY_RATE_LIMIT_DELAY_SECONDS = 0.3  # 25 req / 6s = ~4.2/s; a small margin under that

# Most large multinationals have far more ISINs than one equity listing -
# L'Oreal alone has 32 (mostly bond issuances). Checking every single one
# against OpenFIGI is both slow (rate-limited) and unnecessary - this
# caps how many are tried per LEI candidate before giving up on it.
# Raised when an API key is present (see above) - checking more ISINs is
# now cheap, so there's less reason to give up early on a candidate that
# genuinely has its equity ISIN buried deep in a long bond-issuance list.
MAX_ISINS_TO_CHECK_PER_LEI_ANONYMOUS = 20
MAX_ISINS_TO_CHECK_PER_LEI_WITH_KEY = 60


def _openfigi_api_key() -> str | None:
    return os.environ.get("OPENFIGI_API_KEY") or None


def _rate_limit_delay() -> float:
    return API_KEY_RATE_LIMIT_DELAY_SECONDS if _openfigi_api_key() else ANONYMOUS_RATE_LIMIT_DELAY_SECONDS


def _max_isins_per_lei() -> int:
    return MAX_ISINS_TO_CHECK_PER_LEI_WITH_KEY if _openfigi_api_key() else MAX_ISINS_TO_CHECK_PER_LEI_ANONYMOUS

COUNTRY_NAME_TO_ISO2 = {
    "France": "FR", "Italy": "IT", "Spain": "ES", "Sweden": "SE",
    "United Kingdom": "GB", "Germany": "DE", "Netherlands": "NL",
    "Belgium": "BE", "Denmark": "DK", "Finland": "FI", "Norway": "NO",
}

# Bloomberg-style exchange code (OpenFIGI's `exchCode`) -> yfinance ticker
# suffix. Built and verified empirically against this project's own 11
# companies' real OpenFIGI responses, not copied from memory - e.g. "FP"
# (Euronext Paris) is what L'Oreal's/LVMH's/etc. own primary listing
# actually returned, verified against 19_valuation.py's hand-checked
# TICKER_MAP. Deliberately small and explicit: an exchCode not in this
# table is left unresolved rather than guessed at, per PLAN.md WP4's
# "never guess" rule - extend it only after verifying a new exchange the
# same way, not by assumption.
EXCH_TO_YF_SUFFIX = {
    "FP": ".PA",   # Euronext Paris
    "IM": ".MI",   # Borsa Italiana (Milan)
    "SM": ".MC",   # Bolsa de Madrid
    "SS": ".ST",   # Nasdaq Stockholm
    "LN": ".L",    # London Stock Exchange
    "NA": ".AS",   # Euronext Amsterdam
    "BB": ".BR",   # Euronext Brussels
    "DC": ".CO",   # Nasdaq Copenhagen
    "FH": ".HE",   # Nasdaq Helsinki
    "NO": ".OL",   # Oslo Bors
}

# Same exchange codes -> the currency that listing actually quotes in.
# An exchange's quote currency is a fixed, well-known fact (not a
# per-company guess) - what TICKER_MAP's second element encoded in
# 19_valuation.py/22_dcf.py/23_market_risk.py before this script existed
# (see sql/migration_003_ticker_currency.sql). NOTE the LSE ("LN") entry:
# London-listed shares often quote in GBX (pence, 1/100 GBP), not GBP -
# the same complication 19_valuation.py's own module docstring already
# flags as the reason it deliberately prices Shell via its USD listing
# instead of "SHEL.L" - not solved here, just not silently mis-tagged as
# plain GBP.
EXCH_TO_CURRENCY = {
    "FP": "EUR", "IM": "EUR", "SM": "EUR", "NA": "EUR", "BB": "EUR", "FH": "EUR",
    "SS": "SEK", "DC": "DKK", "NO": "NOK",
    "LN": "GBX",
}

# Countries whose primary listing is NOT expected to carry a yfinance
# suffix at all (a bare US-style ticker) - Shell's case, deliberately: its
# TICKER_MAP entry is the USD-quoted "SHEL" (NYSE-style), not "SHEL.L"
# (LSE, quoted in GBX pence) - see 19_valuation.py's module docstring.
BARE_TICKER_EXCHANGES = {"US": "USD"}


def resolve_lei_candidates(company_name: str, country_iso2: str = None) -> list:
    """Returns every ACTIVE, jurisdiction-matching LEI candidate for
    company_name - large multinationals have LEIs registered for dozens
    of unrelated subsidiaries/treasury vehicles/employee-shareholding
    plans under similar names (found live: LVMH alone returned 9 ACTIVE
    French candidates - 'ACTIONS LVMH', 'LVMH Group Treasury', 'LVMH
    LUXURY VENTURES FUND I', ..., and the real parent, 'LVMH MOET
    HENNESSY LOUIS VUITTON' - no name-text heuristic reliably tells them
    apart). Jurisdiction narrows the field but doesn't resolve it alone -
    resolve_company() below disambiguates by checking which candidate
    actually has a real, exchange-listed equity security, not by
    guessing from the name."""
    resp = requests.get(
        f"{GLEIF_API}/lei-records",
        params={"filter[entity.legalName]": company_name},
        timeout=30,
    )
    resp.raise_for_status()
    records = resp.json().get("data", [])

    candidates = []
    for rec in records:
        attrs = rec["attributes"]
        entity = attrs["entity"]
        if entity["status"] != "ACTIVE":
            continue
        if country_iso2 and entity.get("jurisdiction") != country_iso2:
            continue
        candidates.append({
            "lei": attrs["lei"],
            "legal_name": entity["legalName"]["name"],
            "jurisdiction": entity.get("jurisdiction"),
        })
    return candidates


def fetch_isins_for_lei(lei: str) -> list:
    """Paginates GLEIF's per-LEI ISIN endpoint. Returns a plain list of
    ISIN strings - deliberately NOT the ~1GB+ bulk ISIN-to-LEI
    relationship file PLAN.md originally specified (see module
    docstring) - this per-LEI endpoint is the same underlying mapping,
    queried live instead of downloaded and indexed."""
    isins = []
    url = f"{GLEIF_API}/lei-records/{lei}/isins"
    params = {"page[size]": 100}
    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        isins.extend(rec["attributes"]["isin"] for rec in payload["data"])
        url = payload.get("links", {}).get("next")
        params = None  # `next` link already carries its own query params
    return isins


def pick_best_equity_hit(equity_hits: list, country_iso2: str = None) -> dict:
    """Pure ranking logic, separated from the OpenFIGI network loop below
    specifically so it's unit-testable without mocking HTTP calls (see
    tests/test_entity_resolution.py). Takes already-fetched OpenFIGI rows
    (each already filtered to marketSector == 'Equity' and securityType2
    == 'Common Stock', with its source ISIN attached), returns the best
    one or None.

    Prefers a known, mappable exchange (EXCH_TO_YF_SUFFIX); among those,
    prefers the one matching the company's own country if known."""
    if not equity_hits:
        return None

    known = [h for h in equity_hits if h["exchCode"] in EXCH_TO_YF_SUFFIX]
    pool = known or equity_hits

    if country_iso2:
        # Rough country-code overlap check (e.g. "FP" for France isn't a
        # literal match on "FR" - EXCH_TO_YF_SUFFIX's keys are Bloomberg
        # exchange codes, not ISO country codes, so this is best-effort,
        # not authoritative - the isin's own country prefix is a better
        # signal and used first).
        same_country_isin = [h for h in pool if h["isin"][:2] == country_iso2]
        if same_country_isin:
            pool = same_country_isin

    if not pool:
        return None

    best = pool[0]
    exch = best["exchCode"]
    if exch in EXCH_TO_YF_SUFFIX:
        ticker = best["ticker"] + EXCH_TO_YF_SUFFIX[exch]
        currency = EXCH_TO_CURRENCY.get(exch)
    elif exch in BARE_TICKER_EXCHANGES:
        ticker = best["ticker"]
        currency = BARE_TICKER_EXCHANGES[exch]
    else:
        return None  # unknown exchange - do not guess a suffix

    return {"ticker": ticker, "exchange": exch, "isin": best["isin"], "currency": currency}


def resolve_working_ticker(candidate_ticker: str, max_retries: int = 3) -> str:
    """Validates a candidate ticker actually resolves on yfinance before
    trusting it - a real bug found running this live, not assumed: Essity's
    real yfinance ticker is 'ESSITY-B.ST' (hyphenated share class), but
    OpenFIGI's raw `ticker` field + EXCH_TO_YF_SUFFIX naively concatenated
    to 'ESSITYB.ST' - which 404s on yfinance. Rather than silently persist
    a plausible-looking-but-broken ticker (this project's own "never guess"
    principle - see CLAUDE.md), this tries the raw candidate first, then
    ONE well-justified normalization (insert a hyphen before a trailing
    single-letter share-class suffix, e.g. 'ESSITYB' -> 'ESSITY-B') and
    validates THAT too. Returns the first form that actually resolves on
    yfinance, or None if neither does - never returns an unvalidated guess.

    A SECOND REAL BUG, found the same way the OpenFIGI timeout one was:
    Renault resolved cleanly (real ticker RNO.PA, verified 3/3 in
    isolation) but came back UNRESOLVED twice running the full
    40-company WP7 batch, with no OpenFIGI errors either time - this
    function's own bare `except Exception: continue` was the actual
    gap, not the OpenFIGI path already fixed. A single transient
    yfinance hiccup under the load of many companies' worth of back-to-
    back validation calls silently killed the whole candidate with no
    retry and no log line - exactly the "give up on a transient failure
    instead of retrying" mistake this project already fixed once for
    OpenFIGI 429s, just not here too. Fixed with the same retry-with-
    backoff shape, scoped to this function since it's the one place
    that validates a ticker under real batch load, not a change to
    19_valuation.py's fetch_market_data() itself."""
    for candidate in _ticker_variants(candidate_ticker):
        for attempt in range(max_retries):
            try:
                market = v19.fetch_market_data(candidate)
            except Exception as e:
                if attempt + 1 < max_retries:
                    backoff = 2 * (attempt + 1)
                    print(f"  *** yfinance validation failed for {candidate} ({e.__class__.__name__}, "
                          f"attempt {attempt + 1}/{max_retries}) - retrying in {backoff}s")
                    time.sleep(backoff)
                    continue
                print(f"  *** yfinance validation failed for {candidate} after {max_retries} attempts: {e}")
                break
            if market and market.get("market_cap"):
                return candidate
            break  # a clean response with no market_cap - not transient, try the next variant
    return None


def _ticker_variants(ticker: str):
    """Yields the raw ticker first, then a hyphen-before-trailing-letter
    variant if the base part (before any yfinance suffix like '.ST') ends
    in a single uppercase letter following other letters/digits - the
    share-class pattern found in the Essity case above."""
    yield ticker
    base, _, suffix = ticker.partition(".")
    m = re.match(r"^(.+[A-Za-z0-9])([A-Z])$", base)
    if m and len(m.group(1)) >= 2:
        yield f"{m.group(1)}-{m.group(2)}" + (f".{suffix}" if suffix else "")


def _post_openfigi_with_retry(isin: str, max_retries: int = 3):
    """A single OpenFIGI mapping call, retrying on 429 AND on a transient
    network failure, both with exponential backoff. The 429 case was
    found necessary running this live, not assumed: OpenFIGI's anonymous
    rate limit is tighter than a fixed inter-request delay alone can
    reliably stay under, AND its cooldown outlasts a single request's own
    delay - a company with many ISINs (Danone: 60, LVMH: dozens) can burn
    through the budget mid-company, and the RIGHT response to a transient
    429 is to back off and retry, not give up on that ISIN (which was
    silently understating the real match rate before this fix - see
    PLAN.md WP4's verification notes).

    THE NETWORK-TIMEOUT CASE WAS A SECOND, LATER BUG FOUND THE SAME WAY:
    Renault resolved cleanly in an isolated single-company test, then
    failed ("none had a resolvable equity listing") in a full 40-company
    batch run minutes later - genuinely non-deterministic behavior for
    the same input, which is never supposed to happen in this pipeline.
    The batch run's own log showed two OpenFIGI read/connect timeouts
    that this function silently treated as "no equity hit for that ISIN"
    (caught by resolve_ticker_from_isins' own except-and-continue) rather
    than a transient failure worth retrying - if the timed-out ISIN
    happened to be the real equity listing, the whole candidate wrongly
    came back UNRESOLVED. Fixed by retrying requests.exceptions.Timeout/
    ConnectionError the same way a 429 already was, instead of only
    guarding the HTTP status code.

    A THIRD case, found chasing the same residual flakiness after the
    two fixes above: Thales and Eni resolved cleanly in one full-batch
    run, then came back UNRESOLVED in the next, with no logged network
    error or yfinance failure either time - and Thales's real equity
    ISIN (FR0000121329) was independently confirmed to sit well inside
    the range actually being checked (index 22 of 55, cap 60), so it
    should have been found. The remaining gap: this function retried
    connection-level exceptions and 429, but a plain HTTP 5xx from
    `resp.raise_for_status()` was never caught here at all - it escaped
    straight past this function's retry loop and was silently caught
    one level up, in resolve_ticker_from_isins' per-ISIN except-and-
    continue, as "no equity hit for that ISIN" - indistinguishable from
    a genuine miss, with zero retry and zero visibility that a server
    error, not an empty result, was the real cause. Fixed by treating a
    5xx status the same as 429 - back off and retry, don't silently
    treat a transient server error as a real answer.

    Sends the X-OPENFIGI-APIKEY header when OPENFIGI_API_KEY is set in
    the environment (verified live against OpenFIGI's own docs before
    trusting the header name/rate-limit numbers - see the module-level
    constants above) - anonymous requests are unaffected, same headers
    as before."""
    delay = _rate_limit_delay()
    headers = {"Content-Type": "application/json"}
    api_key = _openfigi_api_key()
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                OPENFIGI_API,
                headers=headers,
                json=[{"idType": "ID_ISIN", "idValue": isin}],
                timeout=30,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            backoff = delay * (3 ** (attempt + 1))
            print(f"  *** OpenFIGI network error for {isin} ({e.__class__.__name__}, attempt {attempt + 1}/{max_retries}) - retrying in {backoff:.0f}s")
            time.sleep(backoff)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            backoff = delay * (3 ** (attempt + 1))
            print(f"  *** OpenFIGI {resp.status_code} for {isin} (attempt {attempt + 1}/{max_retries}) - backing off {backoff:.0f}s")
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        time.sleep(delay)
        return resp
    raise RuntimeError(f"OpenFIGI still failing after {max_retries} attempts for {isin}")


def resolve_ticker_from_isins(isins: list, country_iso2: str = None) -> dict:
    """Calls OpenFIGI per ISIN (capped at _max_isins_per_lei() - 20
    anonymous, 60 with an API key present, since checking more is cheap
    once the rate limit isn't the bottleneck - see module docstring),
    keeps only common-stock/equity results, and delegates the actual
    pick to pick_best_equity_hit() (the pure, unit-tested part).

    NOT YET BUILT: true request batching - OpenFIGI's /v3/mapping accepts
    up to 100 jobs per POST with an API key, so many ISINs could be
    checked in ONE request instead of one-per-request. This function
    still does one ISIN per call; the rate-limit-delay reduction above is
    the win taken so far. Batching is a further, real optimization for
    whoever picks this up next, not done here - it changes the response-
    parsing shape (one result array per request instead of per ISIN) and
    deserves its own verification pass before being trusted."""
    equity_hits = []
    for isin in isins[:_max_isins_per_lei()]:
        try:
            resp = _post_openfigi_with_retry(isin)
        except Exception as e:
            print(f"  *** OpenFIGI lookup failed for {isin}: {e}")
            continue

        result = resp.json()[0]
        for row in result.get("data", []):
            if row.get("marketSector") == "Equity" and row.get("securityType2") == "Common Stock":
                equity_hits.append({**row, "isin": isin})

    return pick_best_equity_hit(equity_hits, country_iso2)


def resolve_company(name: str, country: str = None, max_lei_candidates_to_try: int = 10) -> dict:
    """Full name -> {lei, isin, ticker, ticker_exchange, ticker_currency,
    ticker_source} pipeline for one company. ticker_source is
    'gleif+openfigi' on success, 'UNRESOLVED' if nothing checked out -
    never a guess in between.

    WHEN MULTIPLE LEI CANDIDATES SURVIVE THE JURISDICTION FILTER (the
    common case - see resolve_lei_candidates' docstring): tries each in
    turn (bounded by max_lei_candidates_to_try) and takes the FIRST one
    that actually has a real, exchange-listed common-stock security. This
    is a verification, not a guess: a subsidiary/treasury/foundation LEI
    does not itself have separately listed common stock, so the
    candidate whose ISINs resolve to one IS the parent/listed entity,
    definitionally - confirmed against real market data (OpenFIGI),
    not inferred from which name "looks" most like a parent company."""
    country_iso2 = COUNTRY_NAME_TO_ISO2.get(country) if country else None

    candidates = resolve_lei_candidates(name, country_iso2)
    if not candidates:
        return {"lei": None, "isin": None, "ticker": None,
                "ticker_exchange": None, "ticker_currency": None, "ticker_source": "UNRESOLVED"}

    for candidate in candidates[:max_lei_candidates_to_try]:
        isins = fetch_isins_for_lei(candidate["lei"])
        if not isins:
            continue
        ticker_info = resolve_ticker_from_isins(isins, country_iso2)
        if ticker_info:
            working_ticker = resolve_working_ticker(ticker_info["ticker"])
            if not working_ticker:
                print(f"  *** '{ticker_info['ticker']}' (from ISIN {ticker_info['isin']}) doesn't "
                      f"resolve on yfinance even after the share-class hyphen retry - not trusted")
                continue
            return {
                "lei": candidate["lei"],
                "isin": ticker_info["isin"],
                "ticker": working_ticker,
                "ticker_exchange": ticker_info["exchange"],
                "ticker_currency": ticker_info["currency"],
                "ticker_source": "gleif+openfigi",
            }

    if len(candidates) > 1:
        print(f"  *** {len(candidates)} ACTIVE candidates for '{name}' in "
              f"{country_iso2 or 'any country'}, none had a resolvable equity listing "
              f"in the first {max_lei_candidates_to_try} tried: "
              f"{[c['legal_name'] for c in candidates[:max_lei_candidates_to_try]]}")

    return {"lei": candidates[0]["lei"] if len(candidates) == 1 else None,
            "isin": None, "ticker": None, "ticker_exchange": None,
            "ticker_currency": None, "ticker_source": "UNRESOLVED"}


# ---------------------------------------------------------------- persistence

def save_to_db(engine, company_name: str, resolution: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE company
            SET lei = COALESCE(lei, :lei),
                isin = :isin, ticker = :ticker,
                ticker_exchange = :ticker_exchange, ticker_currency = :ticker_currency,
                ticker_source = :ticker_source
            WHERE name = :name
        """), {**resolution, "name": company_name})


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only resolve this one company")
    ap.add_argument("--verify", action="store_true",
                     help="Compare against 19_valuation.py's TICKER_MAP instead of writing to the DB "
                          "(PLAN.md WP4's required verification step)")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found.")
        sys.exit(1)
    engine = create_engine(db_url)

    with engine.connect() as conn:
        if args.company:
            rows = conn.execute(text("SELECT name, country FROM company WHERE name = :n"),
                                 {"n": args.company}).fetchall()
        elif args.verify:
            # verification mode checks every TICKER_MAP company, regardless
            # of whether it already has a ticker resolved
            rows = conn.execute(text("SELECT name, country FROM company")).fetchall()
        else:
            rows = conn.execute(text("SELECT name, country FROM company WHERE ticker IS NULL")).fetchall()

    if not rows:
        print("Nothing to resolve.")
        sys.exit(0)

    n_matched, n_mismatched, n_unresolved = 0, 0, 0
    for name, country in rows:
        resolution = resolve_company(name, country)
        status = "OK" if resolution["ticker_source"] != "UNRESOLVED" else "UNRESOLVED"
        print(f"{name:20s} -> ticker={resolution['ticker']!r:16s} "
              f"exchange={resolution['ticker_exchange']!r:6s} isin={resolution['isin']!r:16s} [{status}]")

        if args.verify:
            known = v19.TICKER_MAP.get(name)
            if known is None:
                continue
            known_ticker = known[0]
            if resolution["ticker"] == known_ticker:
                n_matched += 1
            elif resolution["ticker"] is None:
                n_unresolved += 1
            else:
                n_mismatched += 1
                print(f"  *** MISMATCH: TICKER_MAP says {known_ticker!r}, resolver says {resolution['ticker']!r}")
        else:
            save_to_db(engine, name, resolution)

    if args.verify:
        total = n_matched + n_mismatched + n_unresolved
        print(f"\n{'─'*40}")
        print(f"  Matched TICKER_MAP  : {n_matched}/{total}")
        print(f"  Mismatched          : {n_mismatched}/{total}")
        print(f"  Unresolved          : {n_unresolved}/{total}")
