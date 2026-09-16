"""
scripts/30_national_index_membership.py

The other half of SCOPE.md §3's target-universe rule ("member of its
country's main national index OR market cap > EUR 2bn") - not built
until now; 29_universe_membership.py only implements the market-cap
half. Because the rule is an OR, this is a genuinely separate, additive
source of qualifying candidates, not a refinement of the other.

SOURCE: Wikipedia's own index-constituent tables, verified live before
writing any code (not assumed) - CAC 40, FTSE MIB, AEX, OMX Stockholm 30
and BEL 20 all carry a real, dated "as of" constituent table that parses
cleanly with plain pandas.read_html(), no LLM/scraping heuristics beyond
matching on column names. Deliberately does NOT touch OpenFIGI, GLEIF or
yfinance - four of five index pages' ticker columns are already
yfinance-ready (e.g. "AC.PA"); only BEL 20's "Euronext Brussels:ABI"
format needs light, explicit parsing (never a guessed exchange suffix -
same rule 26_entity_resolution.py's EXCH_TO_YF_SUFFIX already follows).

A REAL GOTCHA FOUND LIVE, NOT ASSUMED: BEL 20's Wikipedia page has TWO
tables with a ticker-like column - the current 20 constituents, and a
41-row HISTORICAL/former-members table ("Period in BEL 20"). Picking the
first match blindly would silently grab the wrong one. Fixed by ranking
candidate tables by how close their row count is to the index's actual
known size, not just "has a ticker column".

A COMPANY CAN QUALIFY UNDER BOTH RULES (e.g. Carrefour: CAC 40 member
AND > EUR 2bn) - this script never creates a second row for a company
already present today from 29_universe_membership.py's run; it only
adds genuinely new companies. A company's SECOND qualifying reason is
not recorded (a disclosed simplification, not silently lost - see
save_rows()'s own docstring).

USAGE
-----
    python scripts/30_national_index_membership.py --no-db          # all 5 indices, print only
    python scripts/30_national_index_membership.py --index "CAC 40" # one index, write to DB
"""
import argparse
import os
import sys
from datetime import date
from io import StringIO

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

INDEX_SOURCES = {
    "CAC 40": {"url": "https://en.wikipedia.org/wiki/CAC_40", "country": "France", "expected_size": 40},
    "FTSE MIB": {"url": "https://en.wikipedia.org/wiki/FTSE_MIB", "country": "Italy", "expected_size": 40},
    "AEX": {"url": "https://en.wikipedia.org/wiki/AEX_index", "country": "Netherlands", "expected_size": 25},
    "OMX Stockholm 30": {"url": "https://en.wikipedia.org/wiki/OMX_Stockholm_30", "country": "Sweden", "expected_size": 30},
    "BEL 20": {"url": "https://en.wikipedia.org/wiki/BEL_20", "country": "Belgium", "expected_size": 20},
}

# Only needed for an index page whose ticker column isn't already
# yfinance-ready (BEL 20's "Euronext Brussels:XXX" format). Deliberately
# small and explicit - an exchange name not in this dict is left
# unresolved, never guessed, same rule 26_entity_resolution.py's
# EXCH_TO_YF_SUFFIX already follows.
EXCHANGE_NAME_TO_YF_SUFFIX = {
    "euronext brussels": ".BR",
    "euronext paris": ".PA",
    "euronext amsterdam": ".AS",
    "borsa italiana": ".MI",
}


def find_constituent_table(tables, expected_size):
    """Picks the table that has both a company-name and a ticker/symbol
    column, preferring the one whose row count is closest to the index's
    real current size - guards against a historical/former-members table
    on the same page (see module docstring: BEL 20 has exactly this)."""
    candidates = []
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        has_company = any("company" in c for c in cols)
        has_ticker = any("ticker" in c or "symbol" in c for c in cols)
        if has_company and has_ticker:
            candidates.append(t)
    if not candidates:
        return None
    candidates.sort(key=lambda t: abs(len(t) - expected_size))
    return candidates[0]


def normalize_ticker(raw_ticker: str):
    """Returns a yfinance-ready ticker, or None if the format isn't
    recognised - never guesses a suffix for an exchange not in
    EXCHANGE_NAME_TO_YF_SUFFIX."""
    raw = raw_ticker.replace("\xa0", " ").strip()
    if ":" in raw:
        exchange, code = raw.split(":", 1)
        suffix = EXCHANGE_NAME_TO_YF_SUFFIX.get(exchange.strip().lower())
        if not suffix:
            return None
        return code.strip() + suffix
    if "." in raw:  # already yfinance-ready, e.g. "AC.PA"
        return raw
    return None


def fetch_index_constituents(index_name: str) -> list:
    """Returns [{company, ticker}] for one index - real HTTP + pandas
    table parsing, no LLM involved, reproducible by re-running this."""
    spec = INDEX_SOURCES[index_name]
    resp = requests.get(spec["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    table = find_constituent_table(tables, spec["expected_size"])
    if table is None:
        raise RuntimeError(f"Could not find a constituent table on {spec['url']}")

    company_col = next(c for c in table.columns if "company" in str(c).lower())
    ticker_col = next(c for c in table.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower())

    rows = []
    for _, row in table.iterrows():
        ticker = normalize_ticker(str(row[ticker_col]))
        if not ticker:
            print(f"  *** {index_name}: could not normalize ticker {row[ticker_col]!r} for {row[company_col]!r} - skipped")
            continue
        rows.append({"company": str(row[company_col]).strip(), "ticker": ticker})
    return rows


def save_rows(engine, index_name: str, country: str, rows: list) -> tuple:
    """Inserts a national_index row for each company NOT already present
    today (from either this or 29_universe_membership.py's run) - never
    creates a duplicate row for a company that also qualifies on market
    cap. Disclosed simplification: a company's second qualifying reason
    (e.g. Carrefour is both a CAC 40 member AND > EUR 2bn) is not
    recorded - the row it already has stays as-is, not silently
    overwritten and not silently duplicated either. Returns
    (added, skipped_already_present)."""
    added = skipped = 0
    with engine.begin() as conn:
        for r in rows:
            existing = conn.execute(text(
                "SELECT 1 FROM universe_membership WHERE lower(name) = lower(:name) AND as_of = :as_of"
            ), {"name": r["company"], "as_of": date.today().isoformat()}).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(text("""
                INSERT INTO universe_membership
                    (entity_identifier, name, country, inclusion_rule, inclusion_detail, ticker, as_of)
                VALUES (:entity_identifier, :name, :country, 'national_index', :detail, :ticker, :as_of)
                ON CONFLICT (entity_identifier, as_of) DO NOTHING
            """), {
                "entity_identifier": f"NO_LEI:{r['company']}",
                "name": r["company"],
                "country": country,
                "detail": f"{index_name} constituent (Wikipedia, {date.today().isoformat()})",
                "ticker": r["ticker"],
                "as_of": date.today().isoformat(),
            })
            added += 1
    return added, skipped


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="append", choices=list(INDEX_SOURCES),
                     help="Repeat for multiple indices; default: all")
    ap.add_argument("--no-db", action="store_true", help="Fetch and print only, write nothing")
    args = ap.parse_args()

    indices = args.index or list(INDEX_SOURCES)
    all_rows = {}
    for idx_name in indices:
        spec = INDEX_SOURCES[idx_name]
        print(f"Fetching {idx_name} ({spec['country']})...")
        try:
            rows = fetch_index_constituents(idx_name)
        except Exception as e:
            print(f"  *** FAILED: {e}")
            continue
        print(f"  {len(rows)} constituents found")
        all_rows[idx_name] = rows

    total = sum(len(r) for r in all_rows.values())
    print(f"\n{total} total index-constituent rows across {len(all_rows)} indices")

    if args.no_db:
        print("\n--no-db: nothing written.")
        for idx_name, rows in all_rows.items():
            for r in rows[:3]:
                print(f"  [{idx_name}] {r['company']}: {r['ticker']}")
            if len(rows) > 3:
                print(f"  [{idx_name}] ... and {len(rows) - 3} more")
        sys.exit(0)

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found - cannot write. Re-run with --no-db to just print.")
        sys.exit(1)
    engine = create_engine(db_url)
    total_added = total_skipped = 0
    for idx_name, rows in all_rows.items():
        spec = INDEX_SOURCES[idx_name]
        added, skipped = save_rows(engine, idx_name, spec["country"], rows)
        print(f"{idx_name}: added {added}, skipped {skipped} (already present today)")
        total_added += added
        total_skipped += skipped
    print(f"\nWrote {total_added} new rows, skipped {total_skipped} already-present companies "
          f"(as_of={date.today().isoformat()})")
