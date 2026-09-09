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
RATE_LIMIT_DELAY_SECONDS = 2.5

# Most large multinationals have far more ISINs than one equity listing -
# L'Oreal alone has 32 (mostly bond issuances). Checking every single one
# against OpenFIGI is both slow (rate-limited) and unnecessary - this
# caps how many are tried per LEI candidate before giving up on it.
MAX_ISINS_TO_CHECK_PER_LEI = 20

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


def resolve_working_ticker(candidate_ticker: str) -> str:
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
    yfinance, or None if neither does - never returns an unvalidated guess."""
    for candidate in _ticker_variants(candidate_ticker):
        try:
            market = v19.fetch_market_data(candidate)
        except Exception:
            continue
        if market and market.get("market_cap"):
            return candidate
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
    """A single OpenFIGI mapping call, retrying on 429 with exponential
    backoff. Found necessary running this live, not assumed: OpenFIGI's
    anonymous rate limit is tighter than a fixed inter-request delay
    alone can reliably stay under, AND its cooldown outlasts a single
    request's own delay - a company with many ISINs (Danone: 60,
    LVMH: dozens) can burn through the budget mid-company, and the
    RIGHT response to a transient 429 is to back off and retry, not
    give up on that ISIN (which was silently understating the real
    match rate before this fix - see PLAN.md WP4's verification notes)."""
    for attempt in range(max_retries):
        resp = requests.post(
            OPENFIGI_API,
            headers={"Content-Type": "application/json"},
            json=[{"idType": "ID_ISIN", "idValue": isin}],
            timeout=30,
        )
        if resp.status_code == 429:
            backoff = RATE_LIMIT_DELAY_SECONDS * (3 ** (attempt + 1))
            print(f"  *** OpenFIGI 429 for {isin} (attempt {attempt + 1}/{max_retries}) - backing off {backoff:.0f}s")
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY_SECONDS)
        return resp
    raise RuntimeError(f"OpenFIGI still rate-limited after {max_retries} attempts for {isin}")


def resolve_ticker_from_isins(isins: list, country_iso2: str = None) -> dict:
    """Calls OpenFIGI per ISIN (capped at MAX_ISINS_TO_CHECK_PER_LEI -
    see module docstring), keeps only common-stock/equity results, and
    delegates the actual pick to pick_best_equity_hit() (the pure,
    unit-tested part)."""
    equity_hits = []
    for isin in isins[:MAX_ISINS_TO_CHECK_PER_LEI]:
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
