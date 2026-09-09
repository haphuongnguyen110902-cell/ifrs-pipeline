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

_f16_spec = importlib.util.spec_from_file_location("forecasting_16", _THIS_DIR / "16_forecasting.py")
f16 = importlib.util.module_from_spec(_f16_spec)
_f16_spec.loader.exec_module(f16)

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
    latest = df.loc[latest_idx].reset_index(drop=True)

    # sector comes from the `company` table (wired through in V2.6) -
    # single source of truth rather than duplicating it in this script.
    # sector_std (PLAN.md WP3a) is a separate, machine-assigned, coarser
    # grouping (see populate_sector_std()) - `sector` itself is left
    # completely alone (still the detail-level free text shown in the
    # company header/sidebar filter elsewhere), we just ALSO pull the
    # standardized field peer-grouping actually needs below.
    sectors = pd.read_sql(
        text("SELECT name AS company, sector AS sector_detail, sector_std FROM company"), engine)
    return latest.merge(sectors, on="company", how="left")


def fetch_fundamentals_history(engine, company_filter=None) -> pd.DataFrame:
    """Like fetch_latest_fundamentals but keeps EVERY year, not just the
    latest - needed to CAGR-project forward revenue/EBITDA for forward
    multiples (see compute_forward_multiples)."""
    facts = r11.fetch_facts(engine, company_filter)
    wide = r11.pivot_to_wide(facts)
    ratios = r11.compute_ratios(wide)
    keep = ["company", "company_id", "year", "_revenue", "_ebitda"]
    df = ratios[keep].dropna(subset=["_revenue", "_ebitda"], how="any")
    return df.sort_values(["company", "year"])


# ---------------------------------------------------------------- sector_std

def populate_sector_std(engine, force: bool = False) -> int:
    """Backfills company.sector_std from yfinance's own `sector` field
    (PLAN.md WP3a), using TICKER_MAP for the ticker - the same pre-WP4
    source of truth this script already relies on for market data.

    WHY THIS FIELD EXISTS: company.sector is free text - 9 distinct
    strings across the current 11 companies (see PLAN.md WP3a) - and
    add_peer_stats()/add_implied_valuation() group peers on it, which is
    why the live app used to print "Fewer than 2 sector peers" for
    almost every company. yfinance's sector field is coarser and
    consistent: verified live, it collapses the current 11 into
    Consumer Defensive (5), Consumer Cyclical (3), Healthcare (2) and
    Energy (1, correctly alone) - three real peer groups where there
    used to be none.

    Only fills companies with a NULL sector_std, unless force=True -
    cheap to skip re-querying yfinance for companies already resolved.
    Never guesses: a ticker with no sector in its yfinance info is left
    NULL, not defaulted to something plausible-looking.
    """
    with engine.begin() as conn:
        if force:
            targets = list(TICKER_MAP.keys())
        else:
            rows = conn.execute(text(
                "SELECT name FROM company WHERE sector_std IS NULL"
            )).fetchall()
            targets = [r[0] for r in rows if r[0] in TICKER_MAP]

        n_updated = 0
        for company in targets:
            ticker, _ = TICKER_MAP[company]
            try:
                sector_std = yf.Ticker(ticker).info.get("sector")
            except Exception as e:
                print(f"  *** yfinance sector lookup failed for {company} ({ticker}): {e}")
                continue
            if not sector_std:
                print(f"  *** yfinance returned no sector for {company} ({ticker}) - left NULL, not guessed")
                continue
            conn.execute(text(
                "UPDATE company SET sector_std = :s, sector_source = 'yfinance' WHERE name = :n"
            ), {"s": sector_std, "n": company})
            n_updated += 1

    return n_updated


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
            # "sector" here is deliberately the STANDARDIZED sector_std,
            # not the free-text detail (see fetch_latest_fundamentals and
            # PLAN.md WP3a) - this is what add_peer_stats()/
            # add_implied_valuation() group on below, and it's what gets
            # persisted to valuation.sector, so the peer count shown to a
            # user and the field actually used to compute it always agree.
            # sector_detail is carried alongside for anyone who wants the
            # original, more precise per-company text.
            "company": company, "sector": f.get("sector_std"), "sector_detail": f.get("sector_detail"),
            "year": year, "ticker": ticker,
            "market_cap_eur": market_cap_eur, "net_debt_eur": net_debt_eur,
            "ev_eur": ev_eur, "revenue_eur": revenue_eur, "ebitda_eur": ebitda_eur,
            "net_income_eur": net_income_eur,
            "ev_ebitda": ev_eur / ebitda_eur if ebitda_eur and ebitda_eur > 0 else None,
            "ev_sales": ev_eur / revenue_eur if revenue_eur and revenue_eur > 0 else None,
            "pe": market_cap_eur / net_income_eur if net_income_eur and net_income_eur > 0 else None,
            "note": "" if (net_income_eur is None or net_income_eur > 0) else "P/E n/a - negative net income",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- peer comps (Phase 4)

def add_peer_stats(comps: pd.DataFrame) -> pd.DataFrame:
    """Sector-grouped min/median/max per multiple - this is what makes it
    real 'comps' rather than a flat multiples calculator: Shell (energy,
    EV/EBITDA typically 4-6x) must never be benchmarked against Moncler
    (luxury, typically 12-20x) as if they were peers. n_peers_in_sector
    is included so a 1-company sector (nothing to compare Shell against
    in this 11-company universe) is visible rather than silently
    producing a 'median' that's really just that one company's own number."""
    comps = comps.copy()
    if "sector" not in comps.columns or comps.empty:
        return comps
    for metric in ("ev_ebitda", "ev_sales", "pe"):
        if metric not in comps.columns:
            continue
        grp = comps.groupby("sector")[metric]
        comps[f"{metric}_sector_median"] = comps["sector"].map(grp.median())
        comps[f"{metric}_sector_min"] = comps["sector"].map(grp.min())
        comps[f"{metric}_sector_max"] = comps["sector"].map(grp.max())
    comps["n_peers_in_sector"] = comps["sector"].map(comps.groupby("sector").size())
    return comps


def add_implied_valuation(comps: pd.DataFrame) -> pd.DataFrame:
    """Apply the PEER median multiple (excluding the company itself) to
    the company's own EBITDA/Revenue to get an implied EV, then an
    implied equity value - compared against the company's actual market
    cap, this is the entire point of trading comps: is this company
    trading above or below where its sector says it should. Requires
    >=2 peers in the sector (excluding self) - with only 1, 'median of
    peers excluding self' is undefined, not just noisy."""
    comps = comps.copy()
    implied_ev_ebitda, implied_premium_pct = [], []
    for idx, row in comps.iterrows():
        peers = comps[(comps["sector"] == row["sector"]) & (comps.index != idx)]
        if len(peers) < 2 or pd.isna(row.get("ebitda_eur")) or row.get("ebitda_eur", 0) <= 0:
            implied_ev_ebitda.append(None)
            implied_premium_pct.append(None)
            continue
        peer_median_multiple = peers["ev_ebitda"].median()
        if pd.isna(peer_median_multiple):
            implied_ev_ebitda.append(None)
            implied_premium_pct.append(None)
            continue
        implied_ev = peer_median_multiple * row["ebitda_eur"]
        implied_equity = implied_ev - row["net_debt_eur"]
        implied_ev_ebitda.append(implied_ev)
        if implied_equity and implied_equity > 0:
            premium = (row["market_cap_eur"] / implied_equity - 1) * 100
            implied_premium_pct.append(premium)
        else:
            implied_premium_pct.append(None)
    comps["implied_ev_from_peers"] = implied_ev_ebitda
    comps["premium_vs_peers_pct"] = implied_premium_pct
    return comps


def compute_forward_multiples(comps: pd.DataFrame, history: pd.DataFrame, fx_lookup: dict) -> pd.DataFrame:
    """NTM-style forward EV/EBITDA and EV/Sales, using CAGR-projected
    revenue/EBITDA one year ahead (reuses 16_forecasting.py's
    cagr_forecast - same math, same 'undefined if base/endpoint is
    non-positive' guard, not a separate implementation to keep in sync).

    FX caveat: a forward year has no ECB rate yet (rates are only
    computed for years that have already happened). Uses the MOST
    RECENT available rate as an approximation rather than leaving
    forward multiples undefined for non-EUR companies - documented here
    rather than silently assumed."""
    comps = comps.copy()
    fwd_ev_ebitda, fwd_ev_sales = [], []
    for _, row in comps.iterrows():
        company = row["company"]
        h = history[history["company"] == company].sort_values("year")
        if len(h) < 3:
            fwd_ev_ebitda.append(None)
            fwd_ev_sales.append(None)
            continue
        years, rev, ebitda = h["year"].tolist(), h["_revenue"].tolist(), h["_ebitda"].tolist()
        _, rev_fc = f16.cagr_forecast(years, rev, horizon=1)
        _, ebitda_fc = f16.cagr_forecast(years, ebitda, horizon=1)
        next_year = years[-1] + 1
        if next_year not in rev_fc or next_year not in ebitda_fc:
            fwd_ev_ebitda.append(None)
            fwd_ev_sales.append(None)
            continue

        ticker, quote_ccy = TICKER_MAP.get(company, (None, "EUR"))
        if quote_ccy == "EUR":
            rev_eur, ebitda_eur = rev_fc[next_year], ebitda_fc[next_year]
        else:
            available_years = [y for (c, y) in fx_lookup if c == quote_ccy]
            if not available_years:
                fwd_ev_ebitda.append(None)
                fwd_ev_sales.append(None)
                continue
            latest_fx_year = max(available_years)
            rate = fx_lookup[(quote_ccy, latest_fx_year)]["avg_rate"]
            rev_eur, ebitda_eur = rev_fc[next_year] / rate, ebitda_fc[next_year] / rate

        ev = row.get("ev_eur")
        fwd_ev_ebitda.append(ev / ebitda_eur if ev and ebitda_eur and ebitda_eur > 0 else None)
        fwd_ev_sales.append(ev / rev_eur if ev and rev_eur and rev_eur > 0 else None)
    comps["fwd_ev_ebitda"] = fwd_ev_ebitda
    comps["fwd_ev_sales"] = fwd_ev_sales
    return comps


# ---------------------------------------------------------------- persistence

def ensure_valuation_table(engine):
    ddl = VALUATION_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _company_id_map(conn) -> dict:
    """company name -> company_id, fetched once per run.

    Added for the WP1 migration (see PLAN.md) - `valuation` now has a real
    company_id FK alongside the legacy `company` TEXT column. Looked up by
    name here rather than joined in SQL because comps is a plain DataFrame
    built upstream, not itself a query result.
    """
    rows = conn.execute(text("SELECT company_id, name FROM company")).fetchall()
    return {name: cid for cid, name in rows}


def save_to_db(engine, comps: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        company_ids = _company_id_map(conn)
        for _, r in comps.iterrows():
            if "ticker" not in r or pd.isna(r.get("ev_eur")):
                continue
            company_id = company_ids.get(r["company"])
            if company_id is None:
                # Same "never guess, skip and say why" convention as the
                # missing-ticker case above - a company with no row in
                # `company` yet (e.g. 09_batch_load.py hasn't run for it)
                # should not silently write a NULL company_id, which the
                # NOT NULL constraint added in WP1 would reject anyway.
                print(f"  *** no company_id found for '{r['company']}' - skipped (run the loader first)")
                continue
            conn.execute(text("""
                INSERT INTO valuation
                    (company, company_id, sector, year, ticker, market_cap_eur, net_debt_eur, ev_eur,
                     revenue_eur, ebitda_eur, net_income_eur, ev_ebitda, ev_sales, pe,
                     ev_ebitda_sector_median, n_peers_in_sector, implied_ev_from_peers,
                     premium_vs_peers_pct, fwd_ev_ebitda, fwd_ev_sales, computed_at)
                VALUES
                    (:company, :company_id, :sector, :year, :ticker, :mc, :nd, :ev, :rev, :ebitda, :ni,
                     :evebitda, :evsales, :pe, :sectmed, :npeers, :impliedev, :premium,
                     :fwdevebitda, :fwdevsales, now())
                ON CONFLICT (company_id, year)
                DO UPDATE SET company = EXCLUDED.company, ticker = EXCLUDED.ticker, sector = EXCLUDED.sector,
                              market_cap_eur = EXCLUDED.market_cap_eur,
                              net_debt_eur = EXCLUDED.net_debt_eur, ev_eur = EXCLUDED.ev_eur,
                              revenue_eur = EXCLUDED.revenue_eur, ebitda_eur = EXCLUDED.ebitda_eur,
                              net_income_eur = EXCLUDED.net_income_eur, ev_ebitda = EXCLUDED.ev_ebitda,
                              ev_sales = EXCLUDED.ev_sales, pe = EXCLUDED.pe,
                              ev_ebitda_sector_median = EXCLUDED.ev_ebitda_sector_median,
                              n_peers_in_sector = EXCLUDED.n_peers_in_sector,
                              implied_ev_from_peers = EXCLUDED.implied_ev_from_peers,
                              premium_vs_peers_pct = EXCLUDED.premium_vs_peers_pct,
                              fwd_ev_ebitda = EXCLUDED.fwd_ev_ebitda, fwd_ev_sales = EXCLUDED.fwd_ev_sales,
                              computed_at = now()
            """), {
                "company": r["company"], "company_id": company_id, "sector": r.get("sector"), "year": int(r["year"]),
                "ticker": r["ticker"], "mc": r["market_cap_eur"], "nd": r["net_debt_eur"],
                "ev": r["ev_eur"], "rev": r["revenue_eur"], "ebitda": r["ebitda_eur"],
                "ni": r["net_income_eur"], "evebitda": r["ev_ebitda"], "evsales": r["ev_sales"],
                "pe": r["pe"],
                "sectmed": r.get("ev_ebitda_sector_median"),
                "npeers": int(r["n_peers_in_sector"]) if pd.notna(r.get("n_peers_in_sector")) else None,
                "impliedev": r.get("implied_ev_from_peers"), "premium": r.get("premium_vs_peers_pct"),
                "fwdevebitda": r.get("fwd_ev_ebitda"), "fwdevsales": r.get("fwd_ev_sales"),
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

    print("Backfilling standardized sector (sector_std) for peer grouping...")
    n_sector = populate_sector_std(engine)
    if n_sector:
        print(f"  resolved sector_std for {n_sector} companies via yfinance")

    print("Fetching fundamentals (most recent complete fiscal year per company)...")
    fundamentals = fetch_latest_fundamentals(engine, args.company)
    if fundamentals.empty:
        print("No company has complete revenue/EBITDA/net debt data - nothing to value.")
        sys.exit(1)

    print("Loading historical FX rates (18_fx_convert.py)...")
    fx_lookup = fx18.load_fx_lookup(engine)

    print("Fetching live market data (yfinance) and building comps...\n")
    comps = build_comps(fundamentals, fx_lookup)
    comps = add_peer_stats(comps)
    comps = add_implied_valuation(comps)

    print("Computing forward multiples (CAGR-projected NTM revenue/EBITDA)...")
    history = fetch_fundamentals_history(engine, args.company)
    comps = compute_forward_multiples(comps, history, fx_lookup)

    print(f"\n{'Company':18s} {'Sector':22s} {'EV/EBITDA':>10s} {'Peers':>6s} "
          f"{'Fwd EV/EBITDA':>14s} {'vs Peers':>10s}")
    for _, r in comps.sort_values(["sector", "company"]).iterrows():
        if "ev_eur" not in r or pd.isna(r.get("ev_eur")):
            print(f"{r['company']:18s}  --- {r.get('note', '')}")
            continue
        evebitda_str = f"{r['ev_ebitda']:.1f}x" if pd.notna(r.get("ev_ebitda")) else "n/a"
        n_peers = int(r["n_peers_in_sector"]) - 1 if pd.notna(r.get("n_peers_in_sector")) else 0
        fwd_str = f"{r['fwd_ev_ebitda']:.1f}x" if pd.notna(r.get("fwd_ev_ebitda")) else "n/a"
        prem_str = (f"{r['premium_vs_peers_pct']:+.0f}%"
                    if pd.notna(r.get("premium_vs_peers_pct")) else "n/a")
        print(f"{r['company']:18s} {str(r.get('sector', '')):22s} {evebitda_str:>10s} "
              f"{n_peers:>6d} {fwd_str:>14s} {prem_str:>10s}")

    n_no_peers = (comps["n_peers_in_sector"] <= 1).sum() if "n_peers_in_sector" in comps.columns else 0
    if n_no_peers:
        print(f"\n{n_no_peers} compan(y/ies) have no sector peers in this universe - "
              f"'vs Peers' is n/a for them, not a data error.")

    if not args.no_db:
        ensure_valuation_table(engine)
        rows = save_to_db(engine, comps)
        print(f"\nWrote {rows} valuation rows to database")

    save_to_excel(comps, args.out)
