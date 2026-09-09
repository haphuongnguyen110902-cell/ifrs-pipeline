"""
scripts/25_scenario.py

WHAT
----
Perturb one or more base-year assumptions (revenue, operating margin,
revenue growth, cost of debt, capex) and recompute the downstream
3-statement projection, unlevered FCFF, discounted Enterprise Value, and
Net Debt/EBITDA leverage band - side by side against the unperturbed
base case. This is the actual day-to-day tool of FP&A (budget variance,
sensitivity analysis), and ROADMAP.md calls it the single most
Controlling-relevant piece of this project's forward roadmap.

REUSES PHASE 5/6/8's MACHINERY DIRECTLY, NOT A SEPARATE IMPLEMENTATION
--------------------------------------------------------------------
ROADMAP.md said this explicitly before a line of this script was
written: "Shares its perturb-and-recompute core with Phase 6's DCF
sensitivity table, so build once, use in both places." This script
imports 21_three_statement_model.py's project() and 22_dcf.py's
compute_fcff()/discount_cash_flows() directly, and 24_credit.py's
classify_band() for the leverage read-out - a shock's effect on
Enterprise Value or leverage must be computed the EXACT same way those
scripts compute it standalone, or the numbers wouldn't be comparable to
what the DCF/Credit Profile tabs already show for the unshocked case.

WACC IS HELD CONSTANT ACROSS SCENARIOS, DELIBERATELY
--------------------------------------------------------------------
A revenue/margin/growth/capex shock is an OPERATING assumption; WACC is
a CAPITAL STRUCTURE / market assumption (today's market cap, beta, cost
of debt). Re-deriving WACC under an operating shock would conflate two
different questions ("what if the business performs differently" vs.
"what if financing conditions change") - this script answers only the
first, fetching WACC once from live market data and holding it fixed
across both the base and shocked scenarios, so the entire EV delta
shown is attributable to the operating shock alone, not a moving
discount rate.

SHOCKS SUPPORTED (combine any number in one run)
--------------------------------------------------
--revenue-shock PCT       Multiplicative shock to base-year revenue
                           (e.g. -0.10 = "revenue starts 10% lower")
--margin-shock PP         Additive shock to operating margin, in
                           percentage points (e.g. -2 = "margin -2pp")
--growth-shock PP         Additive shock to the projection's revenue
                           growth rate, in percentage points
--interest-rate-shock PP  Additive shock to the cost-of-debt assumption
--capex-shock PCT         Multiplicative shock to base-year capex

A real budget-variance scenario is rarely just one input moving - all
five can be combined in a single run.

LIMITATION STATED UP FRONT: capex intensity drifts under a revenue-only
shock
--------------------------------------------------------------------
21_three_statement_model.py's project() derives capex for every
projected year as a FIXED percentage of that year's revenue
(capex_pct_of_revenue = base capex / base revenue, computed once). A
--revenue-shock with no matching --capex-shock therefore mechanically
shifts capex intensity too (revenue down 10% with capex unchanged in
absolute terms means capex/revenue rises) - this is not a bug this
script introduces, it is how project() already behaves for any change
to base revenue, reused here rather than re-implemented. Combine
--revenue-shock with an explicit --capex-shock when the scenario is
meant to hold capex intensity constant instead.

WHY NO DATABASE TABLE OR DASHBOARD TAB
--------------------------------------------------
Every other analysis phase in this project computes ONE canonical fact
per company (a DCF, a credit trajectory, a set of trading comps) worth
persisting and showing on the dashboard by default. A scenario is
parameterized by whatever shock the user just typed on the command
line - there is no single "the" scenario for a company to show. This
script is deliberately a CLI-only analysis tool (prints a comparison,
saves an Excel workbook), not a persisted fact - consistent with
20_precedents.py also not needing deep dashboard interactivity, and
with the "database is for facts, not for every ad-hoc query" spirit
the rest of this pipeline already follows.

EXAMPLE
-------
    python scripts/25_scenario.py --company "L'Oreal" --margin-shock -2
    python scripts/25_scenario.py --company "L'Oreal" --revenue-shock -0.10 --capex-shock -0.10

Usage:
    python scripts/25_scenario.py --company "COMPANY NAME" [shock flags...]
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
import os

_THIS_DIR = Path(__file__).parent
_tsm_spec = importlib.util.spec_from_file_location("three_statement_21", _THIS_DIR / "21_three_statement_model.py")
tsm = importlib.util.module_from_spec(_tsm_spec)
_tsm_spec.loader.exec_module(tsm)

_dcf_spec = importlib.util.spec_from_file_location("dcf_22", _THIS_DIR / "22_dcf.py")
dcf22 = importlib.util.module_from_spec(_dcf_spec)
_dcf_spec.loader.exec_module(dcf22)

_credit_spec = importlib.util.spec_from_file_location("credit_24", _THIS_DIR / "24_credit.py")
credit24 = importlib.util.module_from_spec(_credit_spec)
_credit_spec.loader.exec_module(credit24)


# ---------------------------------------------------------------- shocks

def apply_shocks(base: dict, revenue_shock: float = 0.0, margin_shock: float = 0.0,
                  capex_shock: float = 0.0) -> dict:
    """Pure function: returns a NEW base dict with shocks applied, never
    mutates the input. Growth and interest-rate shocks are NOT applied
    here - they're passed straight to project() as the growth/
    interest_rate arguments, since those were never part of the base
    dict to begin with (see run_scenario())."""
    shocked = dict(base)
    shocked["revenue"] = base["revenue"] * (1 + revenue_shock)
    shocked["operating_margin"] = base["operating_margin"] + margin_shock
    shocked["capex"] = base["capex"] * (1 + capex_shock)
    return shocked


# ---------------------------------------------------------------- one scenario

def run_scenario(base: dict, growth: float, interest_rate: float, years: int,
                  wacc: float, terminal_growth: float) -> dict:
    """Project + discount ONE scenario (base or shocked) using Phase 5's
    project() and Phase 6's compute_fcff()/discount_cash_flows(), then
    read the final projected year's leverage band via Phase 8's
    classify_band(). Returns a flat dict comparable across scenarios."""
    projection = tsm.project(base, years, growth, interest_rate)
    fcff_df = dcf22.compute_fcff(projection, base["tax_rate"])
    dcf_result = dcf22.discount_cash_flows(
        fcff_df["year"].tolist(), fcff_df["fcff"].tolist(),
        wacc, terminal_growth, base["base_year"])

    final = projection.iloc[-1]
    ebitda_final = final["ebit"] + final["da"]
    net_debt_ebitda = final["net_debt_end"] / ebitda_final if ebitda_final else float("nan")

    return {
        "final_year": int(final["year"]),
        "revenue": float(final["revenue"]),
        "ebit": float(final["ebit"]),
        "fcff": float(fcff_df["fcff"].iloc[-1]),
        "net_debt_end": float(final["net_debt_end"]),
        "enterprise_value": float(dcf_result["enterprise_value"]),
        "net_debt_ebitda": float(net_debt_ebitda) if pd.notna(net_debt_ebitda) else float("nan"),
        "leverage_band": credit24.classify_band(net_debt_ebitda),
    }


def format_delta_pct(base: float, delta: float) -> str:
    """A "%change" is only meaningful when BOTH the base and shocked
    value are strictly positive - e.g. Net Debt going from -12bn to
    -10.65bn (a company holding LESS net cash than before, i.e. leverage
    moving the WRONG way) is arithmetically a "-11.9%" change (dividing
    by a negative base), which reads as "improved" when it didn't. This
    isn't just a sign-flip edge case - dividing by ANY negative base
    produces a confusing percentage even when the sign never flips
    (found via a real run on L'Oreal: net debt stayed negative
    throughout, and the naive same-sign check still let a misleading
    percentage through). Never show one for a non-positive base or
    result - omit it entirely, same "prefer an explicit absence over a
    wrong-looking number" principle as everywhere else in this project."""
    if base <= 0 or (base + delta) <= 0:
        return ""
    return f" ({delta / base:+.1%})"


def print_comparison(base_result: dict, shocked_result: dict, shock_description: str):
    print(f"\nScenario: {shock_description}")
    print(f"{'Metric':<22s} {'Base':>18s} {'Shocked':>18s} {'Delta':>18s}")
    print("-" * 78)
    rows = [
        ("Revenue", "revenue"),
        ("EBIT", "ebit"),
        ("FCFF", "fcff"),
        ("Net Debt (end)", "net_debt_end"),
        ("Enterprise Value", "enterprise_value"),
    ]
    for label, key in rows:
        b, s = base_result[key], shocked_result[key]
        delta = s - b
        pct = format_delta_pct(b, delta)
        print(f"{label:<22s} {b:>18,.0f} {s:>18,.0f} {delta:>+18,.0f}{pct}")
    print(f"{'Net Debt/EBITDA':<22s} {base_result['net_debt_ebitda']:>17.2f}x "
          f"{shocked_result['net_debt_ebitda']:>17.2f}x "
          f"{shocked_result['net_debt_ebitda'] - base_result['net_debt_ebitda']:>+17.2f}x")
    print(f"{'Leverage band':<22s} {base_result['leverage_band']:>18s} "
          f"{shocked_result['leverage_band']:>18s}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--growth", type=float, default=None, help="Base-case revenue growth (default: historical CAGR)")
    ap.add_argument("--interest-rate", type=float, default=0.04, help="Base-case cost of debt (default 4%%)")
    ap.add_argument("--terminal-growth", type=float, default=0.02)
    ap.add_argument("--revenue-shock", type=float, default=0.0, help="Multiplicative, e.g. -0.10 for -10%%")
    ap.add_argument("--margin-shock", type=float, default=0.0, help="Additive, in percentage points, e.g. -2")
    ap.add_argument("--growth-shock", type=float, default=0.0, help="Additive to growth rate, in percentage points (as a decimal, e.g. -0.02)")
    ap.add_argument("--interest-rate-shock", type=float, default=0.0, help="Additive to cost of debt, as a decimal, e.g. 0.01")
    ap.add_argument("--capex-shock", type=float, default=0.0, help="Multiplicative, e.g. -0.10 for -10%%")
    ap.add_argument("--out", default="data/raw/scenario_output.xlsx")
    args = ap.parse_args()

    if not any([args.revenue_shock, args.margin_shock, args.growth_shock,
                args.interest_rate_shock, args.capex_shock]):
        print("No shock specified - pass at least one of --revenue-shock/--margin-shock/"
              "--growth-shock/--interest-rate-shock/--capex-shock. Use --help for details.")
        sys.exit(1)

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
    if base.get("da_total_is_fallback") or base.get("capex_is_fallback"):
        print("*** This company has at least one fallback assumption (D&A and/or capex) - "
              "see 21_three_statement_model.py's fetch_base_year(). Treat this scenario as "
              "illustrative only.")

    if args.company not in dcf22.val19.TICKER_MAP:
        print(f"\nNo ticker mapped for {args.company} - cannot fetch market data for WACC.")
        sys.exit(1)
    ticker, quote_ccy = dcf22.val19.TICKER_MAP[args.company]

    if quote_ccy != "EUR":
        print(f"{args.company} reports in {quote_ccy} - converting to EUR before scenario analysis "
              f"(see 22_dcf.py's convert_base_to_eur).")
        fx_lookup = dcf22.fx18.load_fx_lookup(engine)
        base = dcf22.convert_base_to_eur(base, quote_ccy, fx_lookup)

    print(f"Fetching live market data for {ticker} (WACC held constant across both scenarios)...")
    market = dcf22.val19.fetch_market_data(ticker)
    live_rate = dcf22.val19.fetch_live_fx_rate(quote_ccy)
    market_cap_eur = market["market_cap"] / live_rate if quote_ccy != "EUR" else market["market_cap"]
    beta = dcf22.fetch_beta(ticker)
    wacc_info = dcf22.compute_wacc(market_cap_eur, base["net_debt"], beta, base["tax_rate"],
                                    dcf22.DEFAULT_RISK_FREE_RATE, dcf22.DEFAULT_ERP, args.interest_rate)
    wacc = wacc_info["wacc"]
    print(f"WACC: {wacc:.2%} (fixed for both scenarios)")

    base_growth = args.growth if args.growth is not None else tsm.default_growth_rate(base)
    shocked_base = apply_shocks(base, args.revenue_shock, args.margin_shock, args.capex_shock)
    shocked_growth = base_growth + args.growth_shock
    shocked_interest_rate = args.interest_rate + args.interest_rate_shock

    base_result = run_scenario(base, base_growth, args.interest_rate, args.years, wacc, args.terminal_growth)
    shocked_result = run_scenario(shocked_base, shocked_growth, shocked_interest_rate,
                                   args.years, wacc, args.terminal_growth)

    shock_parts = []
    if args.revenue_shock:
        shock_parts.append(f"revenue {args.revenue_shock:+.1%}")
    if args.margin_shock:
        shock_parts.append(f"margin {args.margin_shock:+.1f}pp")
    if args.growth_shock:
        shock_parts.append(f"growth {args.growth_shock:+.1%}")
    if args.interest_rate_shock:
        shock_parts.append(f"cost of debt {args.interest_rate_shock:+.1%}")
    if args.capex_shock:
        shock_parts.append(f"capex {args.capex_shock:+.1%}")

    print_comparison(base_result, shocked_result, ", ".join(shock_parts))
    print(f"\n(Comparison is for final projected year {base_result['final_year']}, "
          f"EUR unless noted otherwise)")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"scenario": "Base", **base_result},
        {"scenario": "Shocked", **shocked_result},
    ]).to_excel(args.out, index=False)
    print(f"Saved to {args.out}")
