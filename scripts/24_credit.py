"""
scripts/24_credit.py

WHAT
----
A simple credit-profile classification per company, built on the Net
Debt / EBITDA TRAJECTORY across all loaded years - not a single-year
snapshot. Buckets each year into a fixed leverage band and labels the
year-over-year trend (improving/stable/deteriorating). Most of the
underlying data already existed (`_net_debt`/`_ebitda` from
11_ratio_engine.py) - this is a classification/trend layer on top of
what's already computed, low cost relative to value (see ROADMAP.md
Phase 8).

A REAL BUG FOUND WHILE BUILDING THIS, NOT ASSUMED
------------------------------------------------------
11_ratio_engine.py already has a column literally named
"net_debt_ebitda_proxy" - reusing it seemed like the obvious shortcut
for this script. It isn't: that column is actually Net Debt / EBIT
(its own display label, "Net Debt vs Op. Profit", says so honestly -
only the COLUMN NAME is misleading). Real-world credit thresholds are
calibrated to EBITDA, and EBIT understates EBITDA by the D&A add-back,
so reusing that column would have systematically OVERSTATED every
company's leverage. This script computes Net Debt / `_ebitda` (EBIT +
D&A, already computed in 11_ratio_engine.py for exactly this purpose)
directly instead - see compute_credit_metrics() below.

WHY is_da_fallback MATTERS HERE SPECIFICALLY
-----------------------------------------------
L'Oreal, LVMH, EssilorLuxottica and Essity still show `_da_total = 0`
for at least some years (see 11_ratio_engine.py's own comment on the
D&A tag gap - ambiguous tags, deliberately not guessed at). For those
years, `_ebitda` silently collapses to `_ebit`, which means THIS
script's Net Debt/EBITDA would be overstated in exactly the way the bug
above was fixing. Rather than let that happen invisibly a second time,
every row is flagged `is_da_fallback` when `_da_total == 0`, and the
CLI output prints an explicit warning for any such year - never a
silently-too-high leverage multiple.

METHODOLOGY - and why it's deliberately simple
----------------------------------------------------
- Bands are fixed, sector-agnostic Net-Debt/EBITDA cutoffs (see
  LEVERAGE_BANDS below) - a rough, commonly-cited heuristic, explicitly
  NOT a real agency rating (no industry mix, interest coverage, or
  qualitative factors). Same "sanity-checked against the current
  11-company dataset, not statistically derived, revisit once the
  universe is sector-diverse enough" caveat 15_forensics.py's thresholds
  already carry - Shell (energy, capital-intensive) and Puig Brands
  (luxury, asset-light) should not really share one thresholds set, but
  11 companies isn't enough data to derive sector-relative ones yet.
- Trend compares the latest two available years' ratios: a change of
  more than +/-0.3x is "deteriorating"/"improving", anything smaller is
  "stable" - a deliberately loose band so small noise-level moves don't
  get over-interpreted as a real credit trend.
- Net cash (negative net debt) is its own band, not "very low leverage" -
  a net-cash company isn't just "low leverage", it's a qualitatively
  different balance sheet (see NEGATIVE_NET_DEBT in 15_forensics.py,
  which makes the same distinction for the same reason).

EXAMPLE
-------
    python scripts/24_credit.py --company "L'Oreal"
    python scripts/24_credit.py

Usage:
    python scripts/24_credit.py --company "COMPANY NAME"
    python scripts/24_credit.py
    python scripts/24_credit.py --no-db
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

CREDIT_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_credit.sql"

# Fixed, sector-agnostic Net Debt/EBITDA bands - see module docstring for
# why these are a rough heuristic, not a real agency methodology.
LEVERAGE_BANDS = [
    (0.0, "Net cash"),        # net_debt < 0, handled separately below
    (1.0, "Very low leverage"),
    (2.0, "Low leverage"),
    (3.0, "Moderate leverage"),
    (4.5, "Elevated leverage"),
    (6.0, "High leverage"),
]
TREND_STABLE_THRESHOLD = 0.3  # x turns - see module docstring


def classify_band(net_debt_ebitda: float) -> str:
    if pd.isna(net_debt_ebitda):
        return "n/a"
    if net_debt_ebitda < 0:
        return "Net cash"
    for cutoff, label in LEVERAGE_BANDS[1:]:
        if net_debt_ebitda < cutoff:
            return label
    return "Very high leverage"


def classify_trend(yoy_change: float) -> str:
    if pd.isna(yoy_change):
        return "n/a"
    if yoy_change > TREND_STABLE_THRESHOLD:
        return "Deteriorating"
    if yoy_change < -TREND_STABLE_THRESHOLD:
        return "Improving"
    return "Stable"


def build_credit_profile(ratios: pd.DataFrame, company: str) -> pd.DataFrame:
    """Pure transformation from an already-computed ratios DataFrame (as
    returned by 11_ratio_engine.py's compute_ratios()) to a per-year
    credit profile - factored out from compute_credit_metrics() so the
    actual classification logic is unit-testable without a database
    connection, same pattern as 21_three_statement_model.py's
    select_base_year_row(). Returns one row per year, sorted ascending -
    NOT the misleading net_debt_ebitda_proxy column (see module
    docstring)."""
    if ratios.empty:
        return pd.DataFrame()

    df = ratios[["year", "_net_debt", "_ebitda", "_da_total"]].dropna(
        subset=["_net_debt", "_ebitda"]).sort_values("year").reset_index(drop=True)
    if df.empty:
        return df

    df["net_debt_ebitda"] = df["_net_debt"] / df["_ebitda"]
    df["is_da_fallback"] = df["_da_total"] == 0
    df["band"] = df["net_debt_ebitda"].apply(classify_band)
    df["yoy_change"] = df["net_debt_ebitda"].diff()
    df["trend"] = df["yoy_change"].apply(classify_trend)
    df["company"] = company
    return df[["company", "year", "_net_debt", "_ebitda", "net_debt_ebitda",
               "is_da_fallback", "band", "yoy_change", "trend"]].rename(
        columns={"_net_debt": "net_debt", "_ebitda": "ebitda"})


def compute_credit_metrics(company: str, engine) -> pd.DataFrame:
    """DB-backed wrapper: fetch facts for `company`, compute ratios, then
    build_credit_profile() does the actual (unit-tested) transformation."""
    facts = r11.fetch_facts(engine, company)
    wide = r11.pivot_to_wide(facts)
    ratios = r11.compute_ratios(wide)
    return build_credit_profile(ratios, company)


# ---------------------------------------------------------------- persistence

def ensure_credit_table(engine):
    ddl = CREDIT_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _company_id_map(conn) -> dict:
    """company name -> company_id. See PLAN.md WP1 - credit_profile now
    has a real company_id FK alongside the legacy `company` TEXT column."""
    rows = conn.execute(text("SELECT company_id, name FROM company")).fetchall()
    return {name: cid for cid, name in rows}


def save_to_db(engine, df: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        company_ids = _company_id_map(conn)
        for _, r in df.iterrows():
            company_id = company_ids.get(r["company"])
            if company_id is None:
                print(f"  *** no company_id found for '{r['company']}' - skipped (run the loader first)")
                continue
            conn.execute(text("""
                INSERT INTO credit_profile
                    (company, company_id, year, net_debt, ebitda, net_debt_ebitda, is_da_fallback,
                     band, yoy_change, trend, computed_at)
                VALUES
                    (:company, :company_id, :year, :nd, :ebitda, :nde, :fallback, :band, :yoy, :trend, now())
                ON CONFLICT (company_id, year)
                DO UPDATE SET company = EXCLUDED.company, net_debt = EXCLUDED.net_debt, ebitda = EXCLUDED.ebitda,
                              net_debt_ebitda = EXCLUDED.net_debt_ebitda,
                              is_da_fallback = EXCLUDED.is_da_fallback,
                              band = EXCLUDED.band, yoy_change = EXCLUDED.yoy_change,
                              trend = EXCLUDED.trend, computed_at = now()
            """), {
                "company": r["company"], "company_id": company_id, "year": int(r["year"]),
                "nd": float(r["net_debt"]), "ebitda": float(r["ebitda"]),
                "nde": float(r["net_debt_ebitda"]) if pd.notna(r["net_debt_ebitda"]) else None,
                "fallback": bool(r["is_da_fallback"]), "band": r["band"],
                "yoy": float(r["yoy_change"]) if pd.notna(r["yoy_change"]) else None,
                "trend": r["trend"],
            })
            rows_written += 1
    return rows_written


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only compute for this company (default: all 11)")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/credit_profile.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    if args.company:
        companies = [args.company]
    else:
        companies = pd.read_sql(text("SELECT name FROM company ORDER BY name"), engine)["name"].tolist()

    all_results = []
    for company in companies:
        df = compute_credit_metrics(company, engine)
        if df.empty:
            print(f"{company}: no net debt/EBITDA data available - skipping")
            continue
        all_results.append(df)

        print(f"\n{company}")
        for _, r in df.iterrows():
            fallback_note = " [D&A fallback - EBITDA=EBIT, leverage likely OVERSTATED]" if r["is_da_fallback"] else ""
            print(f"  {int(r['year'])}  Net Debt/EBITDA: {r['net_debt_ebitda']:6.2f}x  "
                  f"{r['band']:20s}  {r['trend']}{fallback_note}")

    if not all_results:
        print("\nNo companies had enough data to compute a credit profile.")
        sys.exit(1)

    combined = pd.concat(all_results, ignore_index=True)

    if not args.no_db:
        ensure_credit_table(engine)
        rows = save_to_db(engine, combined)
        print(f"\nWrote {rows} credit profile rows to database")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    combined.to_excel(args.out, index=False)
    print(f"Saved to {args.out}")
