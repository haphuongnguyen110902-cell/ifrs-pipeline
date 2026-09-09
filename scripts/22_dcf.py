"""
scripts/22_dcf.py

WHAT
----
A DCF valuation: WACC built up via CAPM, unlevered Free Cash Flow (FCFF)
projected forward, terminal value (Gordon growth), all discounted back
to today's Enterprise Value, then Equity Value = EV - today's net debt.
Cross-checked against Phase 4's trading comps and precedent transaction
ranges - three methods converging (or clearly not, and why) is the
actual valuation deliverable, not any one method alone.

WHY THIS REUSES PHASE 5'S PROJECTION BUT NOT ITS "fcf" COLUMN
-----------------------------------------------------------------
This is a real trap worth naming explicitly: 21_three_statement_model.py's
"fcf" column is LEVERED - it's net income (already net of interest
expense) plus D&A minus working capital minus capex, i.e. cash flow
AFTER the cost of debt has already been paid. A DCF that discounts at
WACC is a DCF that has ALREADY priced in the cost of debt through the
discount rate - so discounting Phase 5's interest-expense-reduced FCF
at WACC would count the cost of debt TWICE (once in the discount rate,
once in the cash flow itself). The standard, correct DCF cash flow is
UNLEVERED (FCFF): EBIT*(1-tax) + D&A - Capex - deltaWorkingCapital -
computed directly from EBIT, before any interest expense or debt
schedule is even considered. This script reuses Phase 5's
fetch_base_year() and the underlying revenue/EBIT/D&A/Capex/WorkingCapital
projection logic (single source of truth for those assumptions) but
computes its OWN cash flow line from EBIT, never touching Phase 5's
interest/debt/dividend circularity at all - that machinery exists to
answer a different question (what does the company's balance sheet look
like), not this one (what is the company worth).

WACC ASSUMPTIONS (sourced, dated, overridable)
--------------------------------------------------
- Risk-free rate: 3.4% - German 10-year Bund yield, ~3.35-3.38% as of
  early September 2026 (Trading Economics, Investing.com, FRED). This
  WILL go stale - override with --risk-free-rate for a fresher figure.
- Equity risk premium: 4.2% - Damodaran's mature-market implied ERP,
  July 2026 update (4.17%, rounded). Override with --erp.
- Beta: fetched live from yfinance per company.
- Cost of debt: same --interest-rate assumption as Phase 5 (default 4%),
  tax-shielded at the company's own effective tax rate.
- Capital structure weights: today's market cap (equity) vs. today's net
  debt - if net debt is negative (net cash, e.g. L'Oreal), the debt
  weight is floored at 0 (no meaningful "cost of negative debt" - this
  becomes an all-equity WACC, which is the standard convention for a
  net-cash company).

EXAMPLE
-------
    python scripts/22_dcf.py --company "L'Oreal"
    python scripts/22_dcf.py --company "L'Oreal" --terminal-growth 0.02

Usage:
    python scripts/22_dcf.py --company "COMPANY NAME"
    python scripts/22_dcf.py --company "COMPANY NAME" --years 5 --terminal-growth 0.02
    python scripts/22_dcf.py --company "COMPANY NAME" --risk-free-rate 0.035 --erp 0.045
    python scripts/22_dcf.py --company "COMPANY NAME" --no-db
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

_THIS_DIR = Path(__file__).parent
_tsm_spec = importlib.util.spec_from_file_location("three_statement_21", _THIS_DIR / "21_three_statement_model.py")
tsm = importlib.util.module_from_spec(_tsm_spec)
_tsm_spec.loader.exec_module(tsm)

_val_spec = importlib.util.spec_from_file_location("valuation_19", _THIS_DIR / "19_valuation.py")
val19 = importlib.util.module_from_spec(_val_spec)
_val_spec.loader.exec_module(val19)

_fx_spec = importlib.util.spec_from_file_location("fx_convert_18", _THIS_DIR / "18_fx_convert.py")
fx18 = importlib.util.module_from_spec(_fx_spec)
_fx_spec.loader.exec_module(fx18)

DCF_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_dcf.sql"

# Sourced defaults - see module docstring for citations. Both go stale;
# override via CLI for a fresher estimate.
DEFAULT_RISK_FREE_RATE = 0.034   # German 10Y Bund, ~Sept 2026
DEFAULT_ERP = 0.042              # Damodaran mature-market implied ERP, July 2026


# ---------------------------------------------------------------- FX (native currency -> EUR)

def convert_base_to_eur(base: dict, currency: str, fx_lookup: dict) -> dict:
    """This script is the first place in the pipeline that combines a
    company's OWN reported financials (native reporting currency - SEK
    for Essity, USD for Shell) with something already in EUR (live
    market cap, needed for the WACC capital-structure weights). Every
    absolute monetary field in `base` must be converted to EUR BEFORE
    that combination happens - found via a real run: Essity's DCF
    printed a "EUR 884bn" Enterprise Value, actually its real figure IN
    SEK mislabeled as EUR (Essity's true EV is roughly EUR 20bn). WACC
    itself was silently corrupted too - averaging an EUR market-cap
    weight against a native-currency net-debt weight as if they were the
    same unit. Uses 18_fx_convert.py's stored historical rates (average
    for P&L/flow items, closing for balance-sheet items - same IAS 21
    split 19_valuation.py already established), never a live "today"
    rate for a historical fact.

    `currency` is the quote currency from 19_valuation.py's TICKER_MAP,
    used as a stand-in for the filing's reporting currency - the same
    simplification that script's own code already documents and relies
    on (true for every company in this 11-company universe today).

    Ratios (margins, DSO/DIO/DPO, tax_rate, payout_ratio) are currency-
    neutral by construction and deliberately left untouched - only
    absolute monetary fields need conversion."""
    year = base["base_year"]
    b = dict(base)
    for field, rate_type in [
        ("revenue", "avg"), ("ebit", "avg"), ("capex", "avg"), ("da_total", "avg"),
        ("net_debt", "closing"), ("receivables", "closing"),
        ("inventory", "closing"), ("payables", "closing"),
    ]:
        b[field] = fx18.to_eur(base[field], currency, year, fx_lookup, rate_type)
    b["history_revenue"] = [
        fx18.to_eur(v, currency, y, fx_lookup, "avg")
        for y, v in zip(base["history_years"], base["history_revenue"])
    ]
    return b


# ---------------------------------------------------------------- unlevered FCF

def compute_fcff(projection: pd.DataFrame, tax_rate_pct: float) -> pd.DataFrame:
    """Unlevered Free Cash Flow to the Firm, computed directly from
    EBIT - NOT from Phase 5's 'fcf' column (that's levered - see module
    docstring for why mixing them would double-count the cost of debt)."""
    t = tax_rate_pct / 100
    df = projection.copy()
    df["fcff"] = df["ebit"] * (1 - t) + df["da"] - df["delta_wc"] - df["capex"]
    return df


# ---------------------------------------------------------------- WACC

def fetch_beta(ticker: str) -> float:
    """UNTESTED against a live yfinance call from this sandbox - same
    caveat as 19_valuation.py's market data calls."""
    info = yf.Ticker(ticker).info
    beta = info.get("beta")
    if beta is None:
        raise ValueError(f"No beta available for {ticker}")
    return float(beta)


def compute_wacc(market_cap: float, net_debt: float, beta: float, tax_rate_pct: float,
                  risk_free_rate: float, erp: float, cost_of_debt: float) -> dict:
    cost_of_equity = risk_free_rate + beta * erp
    debt_for_weights = max(net_debt, 0.0)  # net-cash company -> 0 debt weight, not negative
    total_capital = market_cap + debt_for_weights
    weight_equity = market_cap / total_capital if total_capital > 0 else 1.0
    weight_debt = debt_for_weights / total_capital if total_capital > 0 else 0.0
    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate_pct / 100)
    wacc = weight_equity * cost_of_equity + weight_debt * after_tax_cost_of_debt
    return {
        "cost_of_equity": cost_of_equity, "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "weight_equity": weight_equity, "weight_debt": weight_debt, "wacc": wacc,
    }


# ---------------------------------------------------------------- DCF math

def discount_cash_flows(fcff_years: list, fcff_values: list, wacc: float,
                         terminal_growth: float, base_year: int) -> dict:
    """Standard mid-nothing (end-of-year) discounting. Terminal value via
    Gordon growth on the LAST projected year's FCFF, discounted back the
    same number of years as the last explicit forecast year."""
    if terminal_growth >= wacc:
        raise ValueError(
            f"terminal_growth ({terminal_growth:.1%}) must be < WACC ({wacc:.1%}) - "
            f"otherwise the terminal value formula divides by a non-positive number "
            f"and the 'valuation' would be infinite/negative, not a real answer."
        )
    pv_explicit = []
    for i, (year, fcff) in enumerate(zip(fcff_years, fcff_values), start=1):
        pv = fcff / (1 + wacc) ** i
        pv_explicit.append(pv)

    n = len(fcff_years)
    terminal_value = fcff_values[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** n

    enterprise_value = sum(pv_explicit) + pv_terminal
    return {
        "pv_explicit_fcff": sum(pv_explicit), "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal, "enterprise_value": enterprise_value,
        "pct_of_ev_from_terminal": pv_terminal / enterprise_value if enterprise_value else None,
    }


def sensitivity_table(fcff_values: list, base_wacc: float, base_terminal_growth: float,
                       wacc_range: float = 0.01, growth_range: float = 0.01, steps: int = 3) -> pd.DataFrame:
    """WACC x terminal growth grid of implied Enterprise Value - the
    standard DCF output format, since a DCF's precision is illusory (the
    terminal value routinely drives 60-80%+ of EV, so showing a RANGE is
    more honest than a single point estimate)."""
    wacc_points = [base_wacc + wacc_range * (i - steps // 2) for i in range(steps)]
    growth_points = [base_terminal_growth + growth_range * (i - steps // 2) for i in range(steps)]
    rows = []
    for w in wacc_points:
        row = {"wacc": w}
        for g in growth_points:
            if g >= w:
                row[f"g={g:.1%}"] = None
                continue
            n = len(fcff_values)
            pv_explicit = sum(fcff / (1 + w) ** i for i, fcff in enumerate(fcff_values, start=1))
            tv = fcff_values[-1] * (1 + g) / (w - g)
            pv_tv = tv / (1 + w) ** n
            row[f"g={g:.1%}"] = pv_explicit + pv_tv
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- persistence

def ensure_dcf_table(engine):
    ddl = DCF_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _get_company_id(conn, company: str):
    """See PLAN.md WP1 - dcf_valuation now has a real company_id FK
    alongside the legacy `company` TEXT column."""
    row = conn.execute(text("SELECT company_id FROM company WHERE name = :n"), {"n": company}).fetchone()
    return row[0] if row else None


def save_to_db(engine, company, base_year, wacc_info, dcf_result, equity_value, share_price_implied) -> None:
    with engine.begin() as conn:
        company_id = _get_company_id(conn, company)
        if company_id is None:
            print(f"  *** no company_id found for '{company}' - not saved (run the loader first)")
            return
        conn.execute(text("""
            INSERT INTO dcf_valuation
                (company, company_id, base_year, wacc, cost_of_equity, after_tax_cost_of_debt,
                 enterprise_value, equity_value, pct_ev_from_terminal, computed_at)
            VALUES
                (:company, :company_id, :base_year, :wacc, :coe, :cod, :ev, :eq, :pct, now())
            ON CONFLICT (company_id, base_year)
            DO UPDATE SET company = EXCLUDED.company, wacc = EXCLUDED.wacc, cost_of_equity = EXCLUDED.cost_of_equity,
                          after_tax_cost_of_debt = EXCLUDED.after_tax_cost_of_debt,
                          enterprise_value = EXCLUDED.enterprise_value,
                          equity_value = EXCLUDED.equity_value,
                          pct_ev_from_terminal = EXCLUDED.pct_ev_from_terminal, computed_at = now()
        """), {
            "company": company, "company_id": company_id, "base_year": base_year, "wacc": float(wacc_info["wacc"]),
            "coe": float(wacc_info["cost_of_equity"]), "cod": float(wacc_info["after_tax_cost_of_debt"]),
            "ev": float(dcf_result["enterprise_value"]), "eq": float(equity_value),
            "pct": float(dcf_result["pct_of_ev_from_terminal"]),
        })


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--growth", type=float, default=None, help="Revenue growth (default: historical CAGR)")
    ap.add_argument("--terminal-growth", type=float, default=0.02,
                     help="Perpetual terminal growth rate (default 2%%, roughly long-run inflation)")
    ap.add_argument("--interest-rate", type=float, default=0.04, help="Cost of debt (default 4%%)")
    ap.add_argument("--risk-free-rate", type=float, default=DEFAULT_RISK_FREE_RATE)
    ap.add_argument("--erp", type=float, default=DEFAULT_ERP)
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/dcf_output.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    print(f"Fetching base-year inputs for {args.company}...")
    base = tsm.fetch_base_year(engine, args.company)
    if not base or "error" in base:
        print(f"Cannot build a model: {base.get('error', 'no data found')}")
        sys.exit(1)
    if base.get("da_total_is_fallback"):
        print("*** No D&A figure found in filings for this company - FCFF below uses "
              "D&A = Capex as a steady-state fallback (see 21_three_statement_model.py's "
              "fetch_base_year). Treat this DCF as illustrative only until a human confirms "
              "the right D&A tag in the filing.")
    if base.get("capex_is_fallback"):
        print("*** No capex figure found in filings - FCFF below uses a generic 3% of "
              "revenue fallback. Treat this DCF as illustrative only for this company.")

    # Ticker/currency lookup moved up here (was after the FCFF projection)
    # because base's native-currency figures must be converted to EUR
    # BEFORE projecting - see convert_base_to_eur()'s docstring for the
    # real bug (Essity's DCF silently mixing SEK financials with EUR
    # market cap) this fixes.
    # PLAN.md WP4: prefer TICKER_MAP (hand-verified) but fall back to the
    # DB-resolved ticker (26_entity_resolution.py) for anything not in it
    # - see 19_valuation.py's resolve_ticker_currency() docstring.
    with engine.connect() as _conn:
        db_row = _conn.execute(text(
            "SELECT ticker, ticker_currency FROM company WHERE name = :n"
        ), {"n": args.company}).fetchone()
    db_ticker, db_currency = (db_row.ticker, db_row.ticker_currency) if db_row else (None, None)
    ticker, quote_ccy = val19.resolve_ticker_currency(args.company, db_ticker, db_currency)
    if not ticker:
        print(f"\nNo ticker mapped for {args.company} (checked 19_valuation.py's TICKER_MAP and "
              f"company.ticker) - cannot fetch market cap/beta. Run scripts/26_entity_resolution.py "
              f"or add it to TICKER_MAP first.")
        sys.exit(1)

    if quote_ccy != "EUR":
        print(f"\n{args.company} reports in {quote_ccy} - converting base-year financials to EUR "
              f"using 18_fx_convert.py's stored historical rates before projecting.")
        fx_lookup = fx18.load_fx_lookup(engine)
        base = convert_base_to_eur(base, quote_ccy, fx_lookup)

    growth = args.growth if args.growth is not None else tsm.default_growth_rate(base)
    projection = tsm.project(base, args.years, growth, args.interest_rate)
    fcff_df = compute_fcff(projection, base["tax_rate"])

    print(f"\nUnlevered FCF (FCFF), in EUR - NOT the same as Phase 5's levered FCF:")
    print(fcff_df[["year", "ebit", "da", "delta_wc", "capex", "fcff"]].to_string(index=False))

    print(f"\nFetching live market data for {ticker}...")
    market = val19.fetch_market_data(ticker)
    live_rate = val19.fetch_live_fx_rate(quote_ccy)
    market_cap_eur = market["market_cap"] / live_rate if quote_ccy != "EUR" else market["market_cap"]
    beta = fetch_beta(ticker)

    wacc_info = compute_wacc(market_cap_eur, base["net_debt"], beta, base["tax_rate"],
                              args.risk_free_rate, args.erp, args.interest_rate)
    print(f"\nBeta: {beta:.2f} | Cost of equity: {wacc_info['cost_of_equity']:.1%} | "
          f"After-tax cost of debt: {wacc_info['after_tax_cost_of_debt']:.1%}")
    print(f"Weights: {wacc_info['weight_equity']:.0%} equity / {wacc_info['weight_debt']:.0%} debt "
          f"{'(net-cash company, all-equity WACC)' if wacc_info['weight_debt'] == 0 else ''}")
    print(f"WACC: {wacc_info['wacc']:.2%}")

    dcf_result = discount_cash_flows(
        fcff_df["year"].tolist(), fcff_df["fcff"].tolist(),
        wacc_info["wacc"], args.terminal_growth, base["base_year"])

    equity_value = dcf_result["enterprise_value"] - base["net_debt"]

    print(f"\nEnterprise Value: EUR {dcf_result['enterprise_value']:,.0f}")
    print(f"  (of which {dcf_result['pct_of_ev_from_terminal']:.0%} from terminal value - "
          f"a DCF is mostly a bet on the terminal assumption, not the explicit forecast)")
    print(f"Less: Net Debt today (EUR {base['net_debt']:,.0f})")
    print(f"Equity Value: EUR {equity_value:,.0f}")

    print(f"\nCross-check against Phase 4 trading comps / precedents: "
          f"see data/raw/valuation_comps.xlsx and data/raw/precedent_transactions.xlsx "
          f"for EV ranges to compare this DCF output against.")

    print(f"\nSensitivity (Enterprise Value, EUR millions):")
    sens = sensitivity_table(fcff_df["fcff"].tolist(), wacc_info["wacc"], args.terminal_growth)
    sens_display = sens.copy()
    sens_display["wacc"] = sens_display["wacc"].apply(lambda w: f"{w:.1%}")
    for col in sens_display.columns[1:]:
        sens_display[col] = sens_display[col].apply(lambda v: f"{v/1e6:,.0f}" if pd.notna(v) else "n/a (g>=WACC)")
    print(sens_display.to_string(index=False))

    if not args.no_db:
        ensure_dcf_table(engine)
        save_to_db(engine, args.company, base["base_year"], wacc_info, dcf_result, equity_value, None)
        print(f"\nWrote DCF result to database")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(args.out, engine="openpyxl") as writer:
        fcff_df.to_excel(writer, sheet_name="FCFF Projection", index=False)
        sens.to_excel(writer, sheet_name="Sensitivity", index=False)
    print(f"Saved to {args.out}")
