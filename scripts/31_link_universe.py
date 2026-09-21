"""
scripts/31_link_universe.py

WHAT
----
Connects the two halves of the database that nothing connected before:

  * `universe_membership` (scripts 29/30) - who is in the universe, with the
    LEI / ticker / quote currency found by the market-cap scan; and
  * `company` - the companies whose filings are actually LOADED.

Found by the full review: `universe_membership.company_id` was NULL on all 217
rows, and the 5 companies loaded from the universe (ASM International, Adyen,
Heineken, Recordati, Schneider Electric) had no ticker / LEI / currency, so they
sat on the dashboard's landing screener with Sector "None" and blank valuation
columns, and nothing downstream (valuation, DCF, credit, market risk) could
price them.

WHAT IT WRITES (only ever NULL -> value; it never overwrites)
--------------------------------------------------------------
  * universe_membership.company_id   for the matched entity (all snapshots)
  * company.lei / ticker / ticker_currency / ticker_source ('universe_membership')

HOW A MATCH IS DECIDED (deterministic, no fuzzy matching, no guessing)
----------------------------------------------------------------------
  1. company.lei == universe.entity_identifier            -> matched by 'lei'
  2. else the normalised names are equal AND the country is equal
                                                          -> matched by 'name+country'
  Zero matches, or matches to more than one entity, are reported and skipped.
  (Normalising = strip accents/punctuation/legal-form words: "L'Oreal" ==
  "L'Oreal SA"; "Heineken" == "Heineken N.V.".)

A TICKER IS ONLY WRITTEN AFTER AN INDEPENDENT CONFIRMATION
----------------------------------------------------------
The ticker comes from the universe row; before it is written, yfinance must
report an instrument whose name CONTAINS all of the company's name words and a
currency that agrees with the universe's. A ticker that cannot be confirmed
(network down, different company, currency conflict) is not written and the
reason is printed. This exists because the entity resolver is known to be
imperfect (Essity resolves to its A-share ticker while the loaded facts are
B-share - see PLAN.md), so one source alone is not trusted.

USAGE
-----
    python scripts/31_link_universe.py                    # dry run: print the plan, write nothing
    python scripts/31_link_universe.py --apply            # write it
    python scripts/31_link_universe.py --company Heineken # just one
Afterwards `python scripts/19_valuation.py` fills sector_std from the DB ticker
and refreshes comps; then 24_credit / 23_market_risk / 22_dcf as needed.
"""
import argparse
import os
import re
import sys
import unicodedata

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# words that carry no identity ("Heineken" vs "Heineken N.V.")
LEGAL_FORM_WORDS = {"se", "sa", "nv", "ag", "plc", "spa", "ab", "abp", "oyj", "asa", "ltd",
                    "limited", "co", "corp", "corporation", "inc", "the"}
NO_LEI_PREFIX = "NO_LEI:"
TICKER_SOURCE = "universe_membership"


# ---------------------------------------------------------------- matching (pure)

def name_words(name) -> list:
    """Lower-case identity words of a company name: accents, punctuation and
    legal-form words removed. "L'Oréal S.A." -> ['loreal']; "Heineken N.V." -> ['heineken']."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return []
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[.'`’]", "", s.lower())                # "n.v." -> "nv", "l'oreal" -> "loreal"
    words = re.findall(r"[a-z0-9]+", s)
    return [w for w in words if w not in LEGAL_FORM_WORDS]


def normalise_name(name) -> str:
    return " ".join(name_words(name))


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def match_universe(company: dict, universe: list):
    """The universe entity a company corresponds to. `universe` holds one row per
    entity (its latest snapshot). Returns (row, matched_by, reason): row is None
    when there is no single unambiguous match."""
    lei = company.get("lei")
    if not _blank(lei):
        hits = [u for u in universe if u["entity_identifier"] == lei]
        if len(hits) == 1:
            return hits[0], "lei", None
    key = normalise_name(company.get("name"))
    country = company.get("country")
    hits = [u for u in universe
            if key and normalise_name(u.get("name")) == key and u.get("country") == country]
    if len(hits) == 1:
        return hits[0], "name+country", None
    if not hits:
        return None, None, "no universe entity with this LEI, or with this name in this country"
    return None, None, f"{len(hits)} universe entities match this name: {[h['entity_identifier'] for h in hits]}"


def confirm_instrument(company_name: str, info: dict, universe_currency):
    """Independent check of a universe ticker against what yfinance says the
    instrument is. `info` is {'name', 'currency'} or None. Returns
    (currency, None) when confirmed, or (None, reason)."""
    if not info or _blank(info.get("name")):
        return None, "could not confirm the ticker on yfinance (no answer)"
    have = set(name_words(info["name"]))
    need = name_words(company_name)
    if not need or not set(need) <= have:
        return None, f"yfinance calls the ticker '{info['name']}', which does not contain '{company_name}'"
    yf_ccy = None if _blank(info.get("currency")) else str(info["currency"]).upper()
    uni_ccy = None if _blank(universe_currency) else str(universe_currency).upper()
    if yf_ccy and uni_ccy and yf_ccy != uni_ccy:
        return None, f"currency conflict: universe says {uni_ccy}, yfinance says {yf_ccy}"
    ccy = uni_ccy or yf_ccy
    if not ccy:
        return None, "quote currency unknown from both sources"
    return ccy, None


def plan_company(company: dict, universe: list, confirm) -> dict:
    """What to do for one company. `confirm(ticker)` -> {'name','currency'} or None
    (injected so the plan is testable without a network). Only NULL columns are
    ever planned for filling."""
    plan = {"company_id": company["company_id"], "name": company["name"], "link": None,
            "matched_by": None, "updates": {}, "notes": []}
    row, how, reason = match_universe(company, universe)
    if row is None:
        plan["notes"].append(f"not linked: {reason}")
        return plan
    plan["link"] = row["entity_identifier"]
    plan["matched_by"] = how

    if not _blank(row["entity_identifier"]) and not row["entity_identifier"].startswith(NO_LEI_PREFIX) \
            and _blank(company.get("lei")):
        plan["updates"]["lei"] = row["entity_identifier"]

    if not _blank(company.get("ticker")):
        plan["notes"].append(f"ticker already set ({company['ticker']}) - left alone")
        return plan
    ticker = row.get("ticker")
    if _blank(ticker):
        plan["notes"].append("universe row has no ticker")
        return plan
    ccy, why = confirm_instrument(company["name"], confirm(ticker), row.get("ticker_currency"))
    if ccy is None:
        plan["notes"].append(f"ticker {ticker} NOT written: {why}")
        return plan
    plan["updates"].update({"ticker": ticker, "ticker_currency": ccy, "ticker_source": TICKER_SOURCE})
    return plan


def plan_all(companies: list, universe: list, confirm) -> list:
    return [plan_company(c, universe, confirm) for c in companies]


# ---------------------------------------------------------------- yfinance confirmation

def yfinance_confirm(ticker: str):
    """{'name', 'currency'} from yfinance for a ticker, or None on any failure."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {"name": info.get("longName") or info.get("shortName"), "currency": info.get("currency")}
    except Exception as e:
        print(f"  yfinance lookup failed for {ticker}: {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- database

def fetch_companies(engine, only=None) -> list:
    df = pd.read_sql(text("SELECT company_id, name, country, lei, ticker, ticker_currency FROM company "
                          "ORDER BY company_id"), engine)
    if only:
        df = df[df["name"].str.lower() == only.lower()]
    return df.astype(object).where(df.notna(), None).to_dict("records")


def fetch_universe(engine) -> list:
    """One row per entity: its latest snapshot."""
    df = pd.read_sql(text("""
        SELECT DISTINCT ON (entity_identifier)
               entity_identifier, name, country, ticker, ticker_currency, as_of
        FROM universe_membership ORDER BY entity_identifier, as_of DESC
    """), engine)
    return df.astype(object).where(df.notna(), None).to_dict("records")


def apply_plans(engine, plans: list) -> dict:
    """Writes the plans. Filling is COALESCE(existing, new): an existing value is
    never replaced. Returns counts."""
    n_linked = n_filled = 0
    with engine.begin() as conn:
        for p in plans:
            if p["link"] is None:
                continue
            res = conn.execute(text(
                "UPDATE universe_membership SET company_id = :cid "
                "WHERE entity_identifier = :eid AND company_id IS NULL"),
                {"cid": p["company_id"], "eid": p["link"]})
            n_linked += res.rowcount or 0
            up = p["updates"]
            touched = 0
            if "ticker" in up:
                # ticker, currency and source are one fact: written together, and only while
                # the ticker is still NULL. A company marked ticker_source='UNRESOLVED' (ticker
                # NULL) therefore gets a consistent source instead of keeping the stale marker.
                res = conn.execute(text(
                    "UPDATE company SET ticker = :ticker, ticker_currency = :ticker_currency, "
                    "ticker_source = :ticker_source WHERE company_id = :cid AND ticker IS NULL"),
                    {"ticker": up["ticker"], "ticker_currency": up["ticker_currency"],
                     "ticker_source": up["ticker_source"], "cid": p["company_id"]})
                touched += res.rowcount or 0
            if "lei" in up:
                res = conn.execute(text("UPDATE company SET lei = :lei WHERE company_id = :cid AND lei IS NULL"),
                                   {"lei": up["lei"], "cid": p["company_id"]})
                touched += res.rowcount or 0
            n_filled += 1 if touched else 0
    return {"universe_rows_linked": n_linked, "companies_filled": n_filled}


def print_plans(plans: list) -> None:
    for p in plans:
        head = f"{p['name']:24s}"
        if p["link"] is None:
            print(f"{head} - {'; '.join(p['notes'])}")
            continue
        fills = ", ".join(f"{k}={v}" for k, v in p["updates"].items()) or "nothing to fill"
        extra = f"  [{'; '.join(p['notes'])}]" if p["notes"] else ""
        print(f"{head} link -> {p['link']} (by {p['matched_by']}); {fills}{extra}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the plan (default: dry run)")
    ap.add_argument("--company", help="only this company (exact name)")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url, pool_pre_ping=True)

    companies = fetch_companies(engine, args.company)
    if not companies:
        print("No such company." if args.company else "No companies in the database.")
        sys.exit(1)
    plans = plan_all(companies, fetch_universe(engine), yfinance_confirm)
    print_plans(plans)

    todo = [p for p in plans if p["link"] or p["updates"]]
    if not args.apply:
        print(f"\nDRY RUN - nothing written ({len(todo)} companies would be touched). Re-run with --apply.")
        sys.exit(0)
    print("\nApplied:", apply_plans(engine, plans))
