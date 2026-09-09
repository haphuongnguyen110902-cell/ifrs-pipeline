"""
scripts/21_three_statement_model.py

WHAT
----
A linked 3-statement projection (Income Statement, Working Capital, Cash
Flow, Debt Schedule) for one company, N years forward from its latest
actual year. This is the single most commonly-tested IB technical skill
("build me a 3-statement model") and the prerequisite for a real DCF
(Phase 6) - a DCF needs projected Free Cash Flow, and "project FCF"
properly means every line is LINKED, not independently guessed:

    Revenue growth -> EBIT (margin-driven) -> Interest Expense -> EBT
    -> Tax -> Net Income -> CFO (+D&A, -deltaWorkingCapital) -> FCF (-Capex)
    -> Debt paydown/draw -> new Debt balance -> Interest Expense (loop)

That last loop is CIRCULARITY: interest expense depends on the debt
balance, which depends on FCF, which depends on net income, which
depends on interest expense. Solved here the same way Excel's iterative
calculation does it - guess an interest expense, compute everything
downstream, get a new debt balance, recompute interest expense, repeat
until it stops changing. solve_circularity() does this with a plain
fixed-point loop (typically converges in under 10 iterations for any
reasonable interest rate).

WHY the simplifications below are stated, not hidden
------------------------------------------------------
- EBIT is driven directly from Revenue x operating_margin, not from a
  separately-modeled COGS/SG&A/D&A split - matches the granularity
  11_ratio_engine.py already computes across all 11 companies.
- One growth/margin/DSO/DIO/DPO assumption for the WHOLE projection
  horizon (not year-by-year fade curves toward some terminal state).
- A single blended net debt balance (not separate revolver/term loan/
  bond tranches) - the circularity mechanic is identical regardless of
  how many tranches a real company's capital structure actually has.
- Debt is allowed to go negative (net cash) with no revolver floor -
  keeps the debt/interest relationship a clean closed-form-solvable
  linear relationship, which is exactly what makes it possible to
  VERIFY the iterative solver against an algebraic solution (see the
  test file) rather than just trusting that it converged to something.

DEFAULTS (all overridable via CLI)
------------------------------------
- Revenue growth: CAGR from historical revenue (reuses
  16_forecasting.py's cagr_forecast)
- Operating margin, gross margin, tax rate, DSO/DIO/DPO: held flat at
  the latest actual year
- Capex: latest actual year's disclosed capex as a % of revenue
- D&A: latest actual year's _da_total as a % of revenue, held flat
- Interest rate on the net debt balance: 4% (generic investment-grade-
  ish assumption - override with --interest-rate)

EXAMPLE
-------
    python scripts/21_three_statement_model.py --company "L'Oreal"
    python scripts/21_three_statement_model.py --company "L'Oreal" --years 5 --growth 0.05 --interest-rate 0.03

Usage:
    python scripts/21_three_statement_model.py --company "COMPANY NAME"
    python scripts/21_three_statement_model.py --company "COMPANY NAME" --years 5
    python scripts/21_three_statement_model.py --company "COMPANY NAME" --growth 0.03 --interest-rate 0.045
    python scripts/21_three_statement_model.py --company "COMPANY NAME" --no-db
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

_THIS_DIR = Path(__file__).parent
_r11_spec = importlib.util.spec_from_file_location("ratio_engine_11", _THIS_DIR / "11_ratio_engine.py")
r11 = importlib.util.module_from_spec(_r11_spec)
_r11_spec.loader.exec_module(r11)

_f16_spec = importlib.util.spec_from_file_location("forecasting_16", _THIS_DIR / "16_forecasting.py")
f16 = importlib.util.module_from_spec(_f16_spec)
_f16_spec.loader.exec_module(f16)

MODEL_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_three_statement.sql"

CAPEX_CONCEPTS = (
    "cash_outflow_for_total_cash_capital_expenditure",
    "purchase_of_property_plant_and_equipment_and_intangible_asse_etc",
    "purchase_of_property_plant_equipment_and_intangible_assets_o_etc",
    "purchase_of_property_plant_and_equipment_intangible_asset_etc",
    "purchase_of_property_plant_and_equipment_classified_as_inves_etc",
)

DIVIDEND_CONCEPTS = (
    "dividends_paid_classified_as_financing_activities",
    "dividends_paid",
)


def select_base_year_row(ratios: pd.DataFrame):
    """Pick the latest year that actually HAS data, not just the
    numerically latest year - found via a real case: Pernod Ricard's
    "latest" year (2025) was completely empty (every ratio NaN - likely
    an incomplete/early-stage filing given its June 30 fiscal year end,
    see PERNOD_FYE_WARNING in 15_forensics.py), while 2024 had a full,
    real set of ratios. Blindly taking idxmax() on `year` silently picked
    the empty year and reported EVERYTHING as "missing", masking that a
    perfectly good prior year was sitting right there. Falls back to the
    old idxmax() behavior (and an honest "missing required inputs" error
    from the caller) only if NO year has both _revenue and _ebit - a
    company missing those in EVERY year has a real data gap, not a stale-
    year problem, and should still surface that error rather than being
    silently rescued by picking some other empty year."""
    candidates = ratios.sort_values("year", ascending=False)
    has_data = candidates[candidates["_revenue"].notna() & candidates["_ebit"].notna()]
    latest_idx = has_data.index[0] if not has_data.empty else ratios["year"].idxmax()
    return ratios.loc[latest_idx]


def fetch_base_year(engine, company: str) -> dict:
    """Everything needed to project forward, from the company's latest
    actual fiscal year: growth rate (from history), margins, working
    capital days, capex, and starting balances (net debt, receivables/
    inventory/payables) to compute year-1's delta-Working-Capital against."""
    facts = r11.fetch_facts(engine, company)
    wide = r11.pivot_to_wide(facts)
    ratios = r11.compute_ratios(wide)
    if ratios.empty:
        return {}

    latest = select_base_year_row(ratios)
    latest_year = int(latest["year"])

    capex = r11.get_best(wide, *CAPEX_CONCEPTS)
    capex_latest = capex.loc[wide["year"] == latest_year]
    capex_value = abs(capex_latest.iloc[0]) if not capex_latest.empty and pd.notna(capex_latest.iloc[0]) else None

    dividends = r11.get_best(wide, *DIVIDEND_CONCEPTS)
    dividends_latest = dividends.loc[wide["year"] == latest_year]
    dividends_value = abs(dividends_latest.iloc[0]) if not dividends_latest.empty and pd.notna(dividends_latest.iloc[0]) else None

    history = ratios[["year", "_revenue"]].dropna().sort_values("year")

    required = ["_revenue", "_ebit", "_net_debt", "_da_total", "gross_margin",
                "operating_margin", "tax_rate", "dso", "dio", "dpo"]
    missing = [c for c in required if c not in latest.index or pd.isna(latest[c])]
    if missing:
        return {"error": f"missing required inputs for base year: {missing}"}

    # payout ratio: what fraction of net income actually leaves the
    # company as dividends, rather than being available for debt paydown/
    # cash buildup. Without this, EVERY dollar of FCF silently piles up
    # as debt paydown or cash forever - unrealistic for any dividend-
    # paying company (found via a real run: L'Oreal's projected net debt
    # went to -29bn EUR net cash after 5 years with payout_ratio=0,
    # which no real dividend-paying company would actually do).
    payout_ratio = None
    if dividends_value is not None and latest["_net_income"] and latest["_net_income"] > 0:
        payout_ratio = min(dividends_value / latest["_net_income"], 1.0)  # cap at 100%, never extrapolate beyond
    payout_ratio_is_fallback = payout_ratio is None
    if payout_ratio is None:
        payout_ratio = 0.0

    cogs_latest = latest["_revenue"] * (1 - latest["gross_margin"] / 100)
    capex_final = capex_value if capex_value is not None else latest["_revenue"] * 0.03

    # _da_total is EXACTLY 0.0 (not NaN, so the `missing` check above
    # doesn't catch it) when none of 11_ratio_engine.py's D&A tag
    # candidates matched for this company (see that file's comment on
    # the D&A fix found via a real 22_dcf.py run) - no real company's
    # total depreciation & amortisation is actually zero, so silently
    # projecting da=0 forward would understate every year's FCF/FCFF.
    # Fallback: D&A ~= Capex, the standard "steady-state" assumption
    # (a mature company's reinvestment roughly offsets what it
    # depreciates) - more defensible than an arbitrary %-of-revenue
    # guess, and it's exactly the capex figure already computed above
    # (itself flagged if IT is a fallback too).
    da_is_fallback = not latest["_da_total"]
    da_final = latest["_da_total"] if not da_is_fallback else capex_final

    return {
        "company": company, "base_year": latest_year,
        "revenue": latest["_revenue"], "ebit": latest["_ebit"],
        "net_debt": latest["_net_debt"], "da_total": da_final,
        "da_total_is_fallback": da_is_fallback,
        "gross_margin": latest["gross_margin"], "operating_margin": latest["operating_margin"],
        "tax_rate": latest["tax_rate"], "dso": latest["dso"], "dio": latest["dio"],
        "dpo": latest["dpo"],
        "capex": capex_final,
        "capex_is_fallback": capex_value is None,
        "payout_ratio": payout_ratio, "payout_ratio_is_fallback": payout_ratio_is_fallback,
        "receivables": latest["_revenue"] * latest["dso"] / 365,
        "inventory": cogs_latest * latest["dio"] / 365,
        "payables": cogs_latest * latest["dpo"] / 365,
        "history_years": history["year"].tolist(),
        "history_revenue": history["_revenue"].tolist(),
    }


def default_growth_rate(base: dict) -> float:
    """CAGR from historical revenue, reusing 16_forecasting.py's
    cagr_forecast. Falls back to 3% if fewer than 2 years of history or
    CAGR is undefined (non-positive endpoint)."""
    years, values = base["history_years"], base["history_revenue"]
    if len(years) < 2:
        return 0.03
    cagr, _ = f16.cagr_forecast(years, values, horizon=1)
    return cagr if cagr is not None else 0.03


def solve_circularity(prev_debt, ebit, da, delta_wc, capex, tax_rate_pct,
                       interest_rate, payout_ratio=0.0, max_iterations=50, tolerance=1e-6):
    """Fixed-point solve for interest expense <-> debt balance, the same
    way Excel's iterative calculation resolves a circular reference.
    Debt is allowed to go negative (net cash), no revolver floor.

    payout_ratio: fraction of net income paid out as dividends BEFORE
    the remainder goes to debt paydown/cash buildup - without this,
    100% of FCF silently piles up as debt paydown forever, which is
    unrealistic for any dividend-paying company (found via a real run:
    L'Oreal's net debt projected to -29bn EUR net cash after 5 years
    with payout_ratio=0 - the circularity math was correct, the economic
    ASSUMPTION was the gap).

    Returns (interest_expense, new_debt, net_income, cfo, fcf,
    dividends) at convergence."""
    tax_rate = tax_rate_pct / 100
    interest_expense = interest_rate * prev_debt
    net_income = cfo = fcf = new_debt = dividends = None
    for _ in range(max_iterations):
        ebt = ebit - interest_expense
        tax = ebt * tax_rate
        net_income = ebt - tax
        cfo = net_income + da - delta_wc
        fcf = cfo - capex
        dividends = max(net_income, 0) * payout_ratio  # never pay dividends out of a projected loss
        cash_for_debt_paydown = fcf - dividends
        new_debt = prev_debt - cash_for_debt_paydown
        new_interest = interest_rate * (prev_debt + new_debt) / 2
        if abs(new_interest - interest_expense) < tolerance:
            interest_expense = new_interest
            break
        interest_expense = new_interest
    return interest_expense, new_debt, net_income, cfo, fcf, dividends


def project(base: dict, years: int, growth: float, interest_rate: float) -> pd.DataFrame:
    rows = []
    prev_revenue = base["revenue"]
    prev_receivables, prev_inventory, prev_payables = (
        base["receivables"], base["inventory"], base["payables"])
    prev_debt = base["net_debt"]
    da_pct_of_revenue = base["da_total"] / base["revenue"]
    capex_pct_of_revenue = base["capex"] / base["revenue"]

    for t in range(1, years + 1):
        year = base["base_year"] + t
        revenue = prev_revenue * (1 + growth)
        ebit = revenue * base["operating_margin"] / 100
        cogs = revenue * (1 - base["gross_margin"] / 100)

        receivables = revenue * base["dso"] / 365
        inventory = cogs * base["dio"] / 365
        payables = cogs * base["dpo"] / 365
        delta_wc = (receivables + inventory - payables) - (prev_receivables + prev_inventory - prev_payables)

        da = revenue * da_pct_of_revenue
        capex = revenue * capex_pct_of_revenue

        interest_expense, new_debt, net_income, cfo, fcf, dividends = solve_circularity(
            prev_debt, ebit, da, delta_wc, capex, base["tax_rate"], interest_rate,
            payout_ratio=base.get("payout_ratio", 0.0))

        rows.append({
            "year": year, "revenue": revenue, "ebit": ebit,
            "interest_expense": interest_expense, "ebt": ebit - interest_expense,
            "tax": (ebit - interest_expense) * base["tax_rate"] / 100,
            "net_income": net_income, "da": da, "delta_wc": delta_wc,
            "cfo": cfo, "capex": capex, "fcf": fcf, "dividends": dividends,
            "net_debt_end": new_debt,
        })

        prev_revenue = revenue
        prev_receivables, prev_inventory, prev_payables = receivables, inventory, payables
        prev_debt = new_debt

    return pd.DataFrame(rows)


def ensure_model_table(engine):
    ddl = MODEL_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, company: str, base: dict, growth: float, interest_rate: float,
                projection: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in projection.iterrows():
            conn.execute(text("""
                INSERT INTO three_statement_projection
                    (company, base_year, forecast_year, growth_assumption, interest_rate_assumption,
                     revenue, ebit, interest_expense, net_income, dividends, payout_ratio_assumption,
                     fcf, net_debt_end, computed_at)
                VALUES
                    (:company, :base_year, :year, :growth, :ir, :rev, :ebit, :ie, :ni, :div, :payout,
                     :fcf, :nd, now())
                ON CONFLICT (company, base_year, forecast_year)
                DO UPDATE SET growth_assumption = EXCLUDED.growth_assumption,
                              interest_rate_assumption = EXCLUDED.interest_rate_assumption,
                              revenue = EXCLUDED.revenue, ebit = EXCLUDED.ebit,
                              interest_expense = EXCLUDED.interest_expense,
                              net_income = EXCLUDED.net_income, dividends = EXCLUDED.dividends,
                              payout_ratio_assumption = EXCLUDED.payout_ratio_assumption,
                              fcf = EXCLUDED.fcf,
                              net_debt_end = EXCLUDED.net_debt_end, computed_at = now()
            """), {
                "company": company, "base_year": base["base_year"], "year": int(r["year"]),
                "growth": float(growth), "ir": float(interest_rate), "rev": float(r["revenue"]),
                "ebit": float(r["ebit"]), "ie": float(r["interest_expense"]),
                "ni": float(r["net_income"]), "div": float(r["dividends"]),
                "payout": float(base.get("payout_ratio", 0.0)),
                "fcf": float(r["fcf"]), "nd": float(r["net_debt_end"]),
            })
            rows_written += 1
    return rows_written


def save_to_excel(base: dict, projection: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        pd.DataFrame([base]).drop(columns=["history_years", "history_revenue"], errors="ignore") \
            .to_excel(writer, sheet_name="Assumptions", index=False)
        projection.to_excel(writer, sheet_name="Projection", index=False)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--growth", type=float, default=None,
                     help="Annual revenue growth rate as a decimal (e.g. 0.05 = 5%%). "
                          "Default: historical CAGR.")
    ap.add_argument("--interest-rate", type=float, default=0.04,
                     help="Annual interest rate on net debt balance (default 4%%)")
    ap.add_argument("--payout-ratio", type=float, default=None,
                     help="Dividend payout ratio as a decimal (e.g. 0.5 = 50%% of net income). "
                          "Default: computed from the base year's disclosed dividends/net income.")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/three_statement_model.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    print(f"Fetching base-year inputs for {args.company}...")
    base = fetch_base_year(engine, args.company)
    if not base or "error" in base:
        print(f"Cannot build a model: {base.get('error', 'no data found')}")
        sys.exit(1)
    if args.payout_ratio is not None:
        base["payout_ratio"] = args.payout_ratio
        base["payout_ratio_is_fallback"] = False

    growth = args.growth if args.growth is not None else default_growth_rate(base)
    print(f"Base year: {base['base_year']} | Growth: {growth:.1%} "
          f"({'CAGR from history' if args.growth is None else 'user override'}) | "
          f"Interest rate: {args.interest_rate:.1%} | Payout ratio: {base['payout_ratio']:.1%}")
    if base["capex_is_fallback"]:
        print("*** No capex figure found in filings - using generic 3% of revenue fallback. "
              "Treat FCF/DCF outputs as illustrative only for this company.")
    if base["da_total_is_fallback"]:
        print("*** No D&A figure found in filings (no matching tag for this company - see "
              "11_ratio_engine.py's _da_total comment) - assuming D&A = Capex as a steady-state "
              "fallback. Treat FCF/DCF/EBITDA outputs as illustrative only for this company.")
    if base["payout_ratio_is_fallback"]:
        print("*** No dividend figure found in filings - assuming 0% payout (100% of FCF "
              "goes to debt paydown/cash buildup). For a real dividend-paying company this "
              "will overstate cash accumulation - override with --payout-ratio if known.")

    projection = project(base, args.years, growth, args.interest_rate)

    print(f"\n{'Year':>6s} {'Revenue':>12s} {'EBIT':>10s} {'Interest':>10s} "
          f"{'Net Income':>11s} {'Dividends':>10s} {'FCF':>10s} {'Net Debt':>10s}")
    for _, r in projection.iterrows():
        print(f"{int(r['year']):6d} {r['revenue']:12,.0f} {r['ebit']:10,.0f} "
              f"{r['interest_expense']:10,.0f} {r['net_income']:11,.0f} "
              f"{r['dividends']:10,.0f} {r['fcf']:10,.0f} {r['net_debt_end']:10,.0f}")

    if not args.no_db:
        ensure_model_table(engine)
        rows = save_to_db(engine, args.company, base, growth, args.interest_rate, projection)
        print(f"\nWrote {rows} projection row(s) to database")

    save_to_excel(base, projection, args.out)
