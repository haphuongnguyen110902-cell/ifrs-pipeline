"""
scripts/29_universe_membership.py

WP7's own first requirement (SCOPE.md §3): a versioned, rule-based
universe of candidate companies - "member of its country's main national
index OR market cap > EUR 2bn", stored with an `as_of` date - not a
hardcoded list. The as_of date is what defends against a survivorship-
bias objection a static list of today's winners cannot answer.

THIS FIRST PASS IMPLEMENTS THE MARKET-CAP HALF OF THE RULE ONLY
------------------------------------------------------------------
The "national index member" half needs verified free index-constituent
data per country (CAC 40, FTSE MIB, AEX, OMX Stockholm 30, BEL 20...) -
a real sourcing task not done yet (tracked in PLAN.md, not guessed at
here). Because the rule is an OR, the market-cap half alone already
produces valid, real candidates - this is a genuine first pass, not a
placeholder waiting on the other half.

PIPELINE (every step reuses an existing, already-verified module - no
new network client written here)
------------------------------------------------------------------------
    26_entity_resolution.py's resolve_company()
        name + country -> LEI, ISIN, ticker, ticker_currency
        (never guessed - UNRESOLVED tickers are simply not written here,
        see NOTE below)
    19_valuation.py's fetch_market_data() + fetch_live_fx_rate()
        ticker -> market cap in its own quote currency -> converted to
        EUR at TODAY's live rate (the same live-vs-historical split
        19_valuation.py's own module docstring already establishes for
        market cap specifically)
    >= EUR 2bn -> written to universe_membership,
        inclusion_rule='market_cap_gt_2bn', as_of=today

NOTE ON "SKIPPED, NOT EXCLUDED": a company whose ticker doesn't resolve,
or whose market cap can't be fetched, gets NO row here - this table only
ever asserts positive inclusion (see sql/schema_universe.sql), so an
absent company means "not yet evaluated", never "does not qualify".
Re-running this script after ticker-resolution coverage improves (e.g.
WP4's own noted next step, an OpenFIGI API key) picks up anyone missed
without disturbing an earlier as_of snapshot.

USAGE
-----
    python scripts/29_universe_membership.py --candidates data/mappings/wp7_candidates.csv
    python scripts/29_universe_membership.py --candidates ... --no-db   # print only, write nothing
"""
import argparse
import csv
import importlib.util
import os
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

_THIS_DIR = Path(__file__).parent
SCHEMA_PATH = _THIS_DIR.parent / "sql" / "schema_universe.sql"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _THIS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


er = _load("entity_resolution_26", "26_entity_resolution.py")
v19 = _load("valuation_19", "19_valuation.py")

MARKET_CAP_THRESHOLD_EUR = 2_000_000_000


def ensure_universe_table(engine):
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def resolve_company_with_retry(name: str, country: str, max_attempts: int = 2) -> dict:
    """Wraps er.resolve_company() with a COMPANY-LEVEL retry, layered on
    top of the HTTP-level retries already inside resolve_company() itself
    (OpenFIGI 429/5xx/timeout, yfinance validation).

    Real, evidenced bug, not assumed: Carrefour came back UNRESOLVED as
    the very first company in four separate full-batch runs across two
    days (no rate-limit warnings logged, no obvious cause), then resolved
    correctly in under 40s when the exact same resolve_company("Carrefour",
    "France") call was made standalone, moments later, same day, same
    LEI/ISIN/ticker as every prior successful run. That rules out both
    "still rate-limited" (it succeeded right after) and "wrong code"
    (identical inputs, identical result once it worked) - what's left is
    some transient failure inside the resolution pipeline (GLEIF
    pagination, OpenFIGI backend inconsistency, or something else not
    yet isolated) that the HTTP-level retries don't happen to catch every
    time.

    max_attempts DELIBERATELY DROPPED FROM 3 TO 2, found necessary
    running this live, not assumed: a full 40-candidate batch with
    max_attempts=3 ran for a full hour and was killed by the timeout
    wrapper without finishing - ~14-16 companies are persistently
    UNRESOLVED (not transient), so every one of them now paid the full
    retry cost for zero benefit (confirmed separately: Carrefour failed
    all 3 attempts, back-to-back, in one run - see PLAN.md). This is a
    real cost/benefit tradeoff, not free insurance: 2 attempts still
    gives a genuinely transient failure one real chance to clear, at
    roughly 2/3 the time penalty 3 attempts cost across every
    persistently-unresolved company in the batch."""
    last = None
    for attempt in range(max_attempts):
        last = er.resolve_company(name, country)
        if last["ticker_source"] != "UNRESOLVED":
            return last
        if attempt + 1 < max_attempts:
            print(f"  {name:25s} [{country}]  UNRESOLVED on attempt {attempt + 1}/{max_attempts} - retrying")
            time.sleep(3)
    return last


def evaluate_candidate(name: str, country: str) -> dict | None:
    """Returns a row dict ready for universe_membership, or None if this
    candidate can't be evaluated yet (ticker unresolved, or market cap
    unavailable) - never a guessed row. `country` is the full country
    name (e.g. "France"), matching resolve_company()'s own expectation."""
    resolution = resolve_company_with_retry(name, country)
    if resolution["ticker_source"] == "UNRESOLVED":
        print(f"  {name:25s} [{country}]  ticker UNRESOLVED - skipped, not excluded")
        return None

    ticker = resolution["ticker"]
    try:
        market = v19.fetch_market_data(ticker)
    except Exception as e:
        print(f"  {name:25s} [{country}]  market data fetch failed for {ticker}: {e}")
        return None

    quote_ccy = market.get("currency") or resolution.get("ticker_currency") or "EUR"
    try:
        live_rate = v19.fetch_live_fx_rate(quote_ccy)
        market_cap_eur = market["market_cap"] / live_rate if quote_ccy != "EUR" else market["market_cap"]
    except Exception as e:
        print(f"  {name:25s} [{country}]  FX conversion failed for {quote_ccy}: {e}")
        return None

    qualifies = market_cap_eur >= MARKET_CAP_THRESHOLD_EUR
    status = "QUALIFIES" if qualifies else f"below threshold ({market_cap_eur/1e9:.1f}bn)"
    print(f"  {name:25s} [{country}]  {ticker:12s} EUR {market_cap_eur/1e9:6.1f}bn  [{status}]")

    if not qualifies:
        return None

    return {
        "entity_identifier": resolution["lei"] or f"NO_LEI:{name}",
        "name": name,
        "country": country,
        "inclusion_rule": "market_cap_gt_2bn",
        "inclusion_detail": f"market cap EUR {market_cap_eur/1e9:.1f}bn (yfinance {ticker}, {date.today().isoformat()})",
        "ticker": ticker,
        "ticker_currency": quote_ccy,
        "as_of": date.today().isoformat(),
    }


def save_rows(engine, rows: list) -> int:
    added = 0
    with engine.begin() as conn:
        for row in rows:
            result = conn.execute(text("""
                INSERT INTO universe_membership
                    (entity_identifier, name, country, inclusion_rule, inclusion_detail,
                     ticker, ticker_currency, as_of)
                VALUES (:entity_identifier, :name, :country, :inclusion_rule, :inclusion_detail,
                        :ticker, :ticker_currency, :as_of)
                ON CONFLICT (entity_identifier, as_of) DO UPDATE SET
                    inclusion_detail = EXCLUDED.inclusion_detail,
                    ticker = EXCLUDED.ticker,
                    ticker_currency = EXCLUDED.ticker_currency
            """), row)
            added += result.rowcount
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True,
                     help="CSV with columns: name,country (country = full name, e.g. France)")
    ap.add_argument("--no-db", action="store_true", help="Evaluate and print only, write nothing")
    args = ap.parse_args()

    with open(args.candidates, encoding="utf-8") as f:
        candidates = [(row["name"], row["country"]) for row in csv.DictReader(f)]
    print(f"{len(candidates)} candidates to evaluate\n")

    qualifying = []
    for name, country in candidates:
        row = evaluate_candidate(name, country)
        if row:
            qualifying.append(row)

    print(f"\n{'='*60}")
    print(f"{len(qualifying)}/{len(candidates)} qualify (market cap >= EUR {MARKET_CAP_THRESHOLD_EUR/1e9:.0f}bn)")

    if args.no_db:
        print("\n--no-db: nothing written.")
        for row in qualifying:
            print(f"  {row['name']}: {row['inclusion_detail']}")
        sys.exit(0)

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found - cannot write. Re-run with --no-db to just print.")
        sys.exit(1)
    engine = create_engine(db_url)
    ensure_universe_table(engine)
    added = save_rows(engine, qualifying)
    print(f"Wrote/updated {added} rows in universe_membership (as_of={date.today().isoformat()})")
