"""
scripts/19_valuation.py

WHAT
----
Trading comps across the company universe: EV/EBITDA, EV/Sales, and P/E,
in EUR, so Essity (SEK) and Shell (USD) sit in the same comps table as
L'Oreal (EUR) without the multiples being distorted by currency.

Enterprise Value = Market Cap (today, live) + Net Debt (latest fiscal
year-end, from the pipeline's own data).

WHY
---
Every ratio built before this (margins, ROIC/ROE, DSO/DIO/DPO/CCC) is
currency-neutral by construction - numerator and denominator share a
currency, so it cancels out. Trading multiples are the first thing in
this pipeline that genuinely needs FX conversion, because Market Cap
(quoted in the stock's local currency) has to be compared against
Net Debt and EBITDA (reported in the filing's currency) as ONE number.

TWO DIFFERENT KINDS OF FX RATE ARE USED HERE ON PURPOSE - this is not
a shortcut, it reflects what the two numbers actually are:
  - Net Debt, EBITDA, Revenue, Net Income are HISTORICAL facts from a
    specific fiscal year -> converted using 18_fx_convert.py's stored
    ECB average/closing rates for THAT year (immutable, never changes).
  - Market Cap is TODAY's value (price x shares, right now) -> converted
    using a LIVE rate fetched at run time. Using a 2023 average rate to
    convert today's market cap would silently mix a live number with a
    stale one and call it "today's EV", which is wrong the same way it
    would be wrong to use today's rate to restate 2023 revenue (see the
    live-vs-historical discussion this script grew out of).

WHERE ELSE this pattern applies
--------------------------------
Any valuation number that combines "today's market data" with "latest
reported financials" needs this same two-rate split - this is standard
in real trading comps, not specific to this pipeline.

LIMITATIONS - stated up front
------------------------------
- EBITDA is NOT an IFRS-tagged concept (it's non-IFRS/non-GAAP), so it's
  reconstructed here as EBIT + D&A add-back using whatever D&A line items
  are tagged (see 11_ratio_engine.py's _ebitda). This is the standard
  approximation, not the company's own disclosed "adjusted EBITDA" (which
  often excludes more items, like Essity's own EBITA-excl-IAC measure).
- Uses each company's MOST RECENT fiscal year with complete data - for
  Pernod Ricard (June 30 FYE) this mixes a non-calendar fiscal year with
  a calendar-year market cap snapshot, same caveat as PERNOD_FYE_WARNING
  in 15_forensics.py.
- Shell is priced via its NYSE-style USD listing (ticker "SHEL", not
  "SHEL.L") specifically to avoid a THIRD currency complication: the
  London listing quotes in pence (GBX, 1/100 GBP), which would need a
  GBP rate this pipeline doesn't otherwise need. Deliberate simplification,
  not an oversight.
- The yfinance calls in this script were written against its documented
  API shape but could not be executed from the sandbox this was built in
  (no network access to Yahoo Finance from there) - test on your machine
  before trusting the output; if yfinance's response shape differs from
  what's coded here, that's the first thing to check.

EXAMPLE
-------
    python scripts/19_valuation.py
    python scripts/19_valuation.py --company "L'Oreal"

Usage:
    python scripts/19_valuation.py
    python scripts/19_valuation.py --company "L'Oreal"
    python scripts/19_valuation.py --no-db
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------- reuse existing modules
_THIS_DIR = Path(__file__).parent
_r11_spec = importlib.util.spec_from_file_location("ratio_engine_11", _THIS_DIR / "11_ratio_engine.py")
r11 = importlib.util.module_from_spec(_r11_spec)
_r11_spec.loader.exec_module(r11)

_fx_spec = importlib.util.spec_from_file_location("fx_convert_18", _THIS_DIR / "18_fx_convert.py")
fx18 = importlib.util.module_from_spec(_fx_spec)
_fx_spec.loader.exec_module(fx18)

VALUATION_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_valuation.sql"

# company name (as stored in the `company` table) -> (Yahoo Finance ticker, quote currency)
# Quote currency is what yfinance reports market cap in for that ticker -
# NOT necessarily the company's reporting currency (e.g. Essity reports in
# SEK and its Stockholm listing also quotes in SEK, so they match here, but
# this won't always be true for every company/exchange combination).
TICKER_MAP = {
    "L'Oreal":           ("OR.PA",       "EUR"),
    "LVMH":              ("MC.PA",       "EUR"),
    "Kering":            ("KER.PA",      "EUR"),
    "EssilorLuxottica":  ("EL.PA",       "EUR"),
    "Danone":            ("BN.PA",       "EUR"),
    "Pernod Ricard":     ("RI.PA",       "EUR"),
    "Essity":            ("ESSITY-B.ST", "SEK"),
    "Moncler":           ("MONC.MI",     "EUR"),
    "Amplifon":          ("AMP.MI",      "EUR"),
    "Puig Brands":       ("PUIG.MC",     "EUR"),
    # Shell: deliberately the USD-quoted listing, not SHEL.L (LSE, quoted in
    # GBX pence) - see module docstring LIMITATIONS.
    "Shell":             ("SHEL",        "USD"),
}


# ---------------------------------------------------------------- market data (live)

def fetch_market_data(ticker: str) -> dict:
    """Fetch current market cap and share price via yfinance.
    UNTESTED against a live call from this sandbox - see module docstring."""
    t = yf.Ticker(ticker)
    try:
        fi = t.fast_info
        market_cap = fi.get("market_cap") or fi.get("marketCap")
        price = fi.get("last_price") or fi.get("lastPrice")
        currency = fi.get("currency")
    except Exception:
        market_cap = price = currency = None

    if market_cap is None:
        info = t.info
        market_cap = info.get("marketCap")
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        currency = info.get("currency")

    if market_cap is None:
        raise ValueError(f"Could not get market cap for {ticker} from yfinance")

    return {"market_cap": float(market_cap), "price": price, "currency": currency}


def fetch_live_fx_rate(currency: str) -> float:
    """Live EUR-based rate for converting TODAY's market cap - deliberately
    separate from 18_fx_convert.py's stored historical rates, which must
    stay frozen to the fiscal year they describe (see module docstring)."""
    if currency == "EUR":
        return 1.0
    pair = yf.Ticker(f"EUR{currency}=X")
    fi = pair.fast_info
    rate = fi.get("last_price") or fi.get("lastPrice")
    if rate is None:
        info = pair.info
        rate = info.get("regularMarketPrice")
    if rate is None:
        raise ValueError(f"Could not get live EUR/{currency} rate from yfinance")
    return float(rate)


# ---------------------------------------------------------------- fundamentals (historical, from the pipeline's own DB)

def fetch_latest_fundamentals(engine, company_filter=None) -> pd.DataFrame:
    """Reuses 11_ratio_engine.py's own fact-fetching and ratio computation
    (same fallback chains for revenue/EBIT/net debt etc.) rather than
    re-deriving these figures with different logic - one source of truth
    for 'what is this company's revenue', whether you're computing a
    margin or a trading multiple from it."""
    facts = r11.fetch_facts(engine, company_filter)
    wide = r11.pivot_to_wide(facts)
    ratios = r11.compute_ratios(wide)

    keep = ["company", "company_id", "year", "_revenue", "_ebitda", "_net_debt", "_net_income"]
    df = ratios[keep].copy()
    # need at least revenue, ebitda, net_debt for EV/EBITDA and EV/Sales;
    # net_income can be missing (P/E just won't compute for that company)
    df = df.dropna(subset=["_revenue", "_ebitda", "_net_debt"], how="any")
    if df.empty:
        return df
    latest_idx = df.groupby("company")["year"].idxmax()
    return df.loc[latest_idx].reset_index(drop=True)


# ---------------------------------------------------------------- comps

def build_comps(fundamentals: pd.DataFrame, fx_lookup: dict) -> pd.DataFrame:
    rows = []
    for _, f in fundamentals.iterrows():
        company = f["company"]
        if company not in TICKER_MAP:
            rows.append({"company": company, "year": f["year"], "note": "no ticker mapped - skipped"})
            continue
        ticker, quote_ccy = TICKER_MAP[company]

        try:
            market = fetch_market_data(ticker)
        except Exception as e:
            rows.append({"company": company, "year": f["year"], "note": f"market data failed: {e}"})
            continue

        try:
            live_rate = fetch_live_fx_rate(quote_ccy)
            market_cap_eur = market["market_cap"] / live_rate if quote_ccy != "EUR" else market["market_cap"]
        except Exception as e:
            rows.append({"company": company, "year": f["year"], "note": f"live FX failed: {e}"})
            continue

        year = int(f["year"])
        # NOTE: fx_lookup is keyed by the FILING's reporting currency, not
        # the stock's quote currency - for every company here today they're
        # the same, but that won't always be true (see TICKER_MAP comment).
        filing_ccy = quote_ccy  # true for the current 11-company universe
        revenue_eur = fx18.to_eur(f["_revenue"], filing_ccy, year, fx_lookup, "avg")
        ebitda_eur = fx18.to_eur(f["_ebitda"], filing_ccy, year, fx_lookup, "avg")
        net_debt_eur = fx18.to_eur(f["_net_debt"], filing_ccy, year, fx_lookup, "closing")
        net_income_eur = fx18.to_eur(f["_net_income"], filing_ccy, year, fx_lookup, "avg")

        ev_eur = market_cap_eur + net_debt_eur

        rows.append({
            "company": company, "year": year, "ticker": ticker,
            "market_cap_eur": market_cap_eur, "net_debt_eur": net_debt_eur,
            "ev_eur": ev_eur, "revenue_eur": revenue_eur, "ebitda_eur": ebitda_eur,
            "net_income_eur": net_income_eur,
            "ev_ebitda": ev_eur / ebitda_eur if ebitda_eur and ebitda_eur > 0 else None,
            "ev_sales": ev_eur / revenue_eur if revenue_eur and revenue_eur > 0 else None,
            "pe": market_cap_eur / net_income_eur if net_income_eur and net_income_eur > 0 else None,
            "note": "" if (net_income_eur is None or net_income_eur > 0) else "P/E n/a - negative net income",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- persistence

def ensure_valuation_table(engine):
    ddl = VALUATION_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, comps: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in comps.iterrows():
            if "ticker" not in r or pd.isna(r.get("ev_eur")):
                continue
            conn.execute(text("""
                INSERT INTO valuation
                    (company, year, ticker, market_cap_eur, net_debt_eur, ev_eur,
                     revenue_eur, ebitda_eur, net_income_eur, ev_ebitda, ev_sales, pe, computed_at)
                VALUES
                    (:company, :year, :ticker, :mc, :nd, :ev, :rev, :ebitda, :ni, :evebitda, :evsales, :pe, now())
                ON CONFLICT (company, year)
                DO UPDATE SET ticker = EXCLUDED.ticker, market_cap_eur = EXCLUDED.market_cap_eur,
                              net_debt_eur = EXCLUDED.net_debt_eur, ev_eur = EXCLUDED.ev_eur,
                              revenue_eur = EXCLUDED.revenue_eur, ebitda_eur = EXCLUDED.ebitda_eur,
                              net_income_eur = EXCLUDED.net_income_eur, ev_ebitda = EXCLUDED.ev_ebitda,
                              ev_sales = EXCLUDED.ev_sales, pe = EXCLUDED.pe, computed_at = now()
            """), {
                "company": r["company"], "year": int(r["year"]), "ticker": r["ticker"],
                "mc": r["market_cap_eur"], "nd": r["net_debt_eur"], "ev": r["ev_eur"],
                "rev": r["revenue_eur"], "ebitda": r["ebitda_eur"], "ni": r["net_income_eur"],
                "evebitda": r["ev_ebitda"], "evsales": r["ev_sales"], "pe": r["pe"],
            })
            rows_written += 1
    return rows_written


def save_to_excel(comps: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    comps.to_excel(out_path, index=False)
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only value this one company")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/valuation_comps.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    print("Fetching fundamentals (most recent complete fiscal year per company)...")
    fundamentals = fetch_latest_fundamentals(engine, args.company)
    if fundamentals.empty:
        print("No company has complete revenue/EBITDA/net debt data - nothing to value.")
        sys.exit(1)

    print("Loading historical FX rates (18_fx_convert.py)...")
    fx_lookup = fx18.load_fx_lookup(engine)

    print("Fetching live market data (yfinance) and building comps...\n")
    comps = build_comps(fundamentals, fx_lookup)

    print(f"{'Company':18s} {'Yr':>5s} {'EV (EURm)':>11s} {'EV/EBITDA':>10s} {'EV/Sales':>9s} {'P/E':>7s}  Note")
    for _, r in comps.iterrows():
        if "ev_eur" not in r or pd.isna(r.get("ev_eur")):
            print(f"{r['company']:18s} {int(r['year']):5d}  {'--- ' + str(r.get('note', ''))}")
            continue
        ev_str = f"{r['ev_eur']/1e6:,.0f}"
        evebitda_str = f"{r['ev_ebitda']:.1f}x" if pd.notna(r.get("ev_ebitda")) else "n/a"
        evsales_str = f"{r['ev_sales']:.1f}x" if pd.notna(r.get("ev_sales")) else "n/a"
        pe_str = f"{r['pe']:.1f}x" if pd.notna(r.get("pe")) else "n/a"
        print(f"{r['company']:18s} {int(r['year']):5d} {ev_str:>11s} {evebitda_str:>10s} "
              f"{evsales_str:>9s} {pe_str:>7s}  {r.get('note', '')}")

    if not args.no_db:
        ensure_valuation_table(engine)
        rows = save_to_db(engine, comps)
        print(f"\nWrote {rows} valuation rows to database")

    save_to_excel(comps, args.out)
