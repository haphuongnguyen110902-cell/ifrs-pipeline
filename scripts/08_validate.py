"""
Data validation / sanity checks.

Goal: catch mapping errors and comparability problems BEFORE they quietly
corrupt downstream analysis. Runs three families of checks:

  1. ACCOUNTING IDENTITIES - things that must be true if the mapping is
     correct. Assets must equal Equity+Liabilities. Gross Profit must
     equal Revenue minus Cost of Sales. A failure here usually means a
     tag got mapped to the wrong concept.

  2. CURRENCY - which currency does each company report in? Comparing
     raw values across companies reporting in SEK vs EUR vs USD is
     meaningless, so this flags mixed currencies loudly.

  3. COVERAGE - how many concepts/periods actually loaded per company,
     to spot a company that silently loaded almost nothing.

Usage:
    python scripts/08_validate.py
    python scripts/08_validate.py --company "L'Oreal"    # just one
"""
import argparse
import os
import sys
from datetime import timedelta

import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# tolerance for identity checks - filings round to the nearest 100k or so,
# and cross-footing differences of a few units are normal rounding, not errors
REL_TOLERANCE = 0.01  # 1%


def fetch_all(engine) -> pd.DataFrame:
    query = """
        SELECT
            c.name AS company,
            ic.statement,
            ic.normalized_name,
            ic.display_label,
            p.period_type,
            p.start_date,
            p.end_date,
            fv.value,
            fv.currency
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p ON fv.period_id = p.period_id
        JOIN filing fil ON fv.filing_id = fil.filing_id
        JOIN company c ON fil.company_id = c.company_id
    """
    df = pd.read_sql(query, engine)
    if df.empty:
        return df

    def get_year(row):
        if row["period_type"] == "instant":
            return (row["end_date"] - timedelta(days=1)).year
        return row["start_date"].year

    df["year"] = df.apply(get_year, axis=1)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def get_val(df, company, year, concept):
    """Look up one value, returns None if absent."""
    hit = df[(df["company"] == company) & (df["year"] == year) & (df["normalized_name"] == concept)]
    if hit.empty:
        return None
    return hit.iloc[0]["value"]


def check_identities(df) -> list:
    """Accounting identities that must hold if the mapping is right."""
    results = []
    identities = [
        # (label, target_concept, [component concepts to sum])
        ("Assets = Equity + Liabilities", "assets", ["equity_and_liabilities"]),
        ("Gross Profit = Revenue - Cost of Sales", "gross_profit", ["revenue", "cost_of_sales"]),
        ("Current + Non-current Assets = Total Assets", "assets", ["current_assets", "noncurrent_assets"]),
    ]

    for company in sorted(df["company"].unique()):
        for year in sorted(df[df["company"] == company]["year"].unique()):
            # identity 1: Assets == EquityAndLiabilities
            a = get_val(df, company, year, "assets")
            el = get_val(df, company, year, "equity_and_liabilities")
            if a is not None and el is not None:
                diff = abs(a - el)
                ok = diff <= abs(a) * REL_TOLERANCE
                results.append((company, year, "Assets = Equity + Liabilities", ok, f"{a:,.0f} vs {el:,.0f}"))

            # identity 2: GrossProfit == Revenue - CostOfSales
            gp = get_val(df, company, year, "gross_profit")
            rev = get_val(df, company, year, "revenue")
            cos = get_val(df, company, year, "cost_of_sales")
            if gp is not None and rev is not None and cos is not None:
                # cost of sales may be tagged positive (as a magnitude) or
                # negative (as a signed deduction) - accept whichever matches
                expected_pos = rev - abs(cos)
                diff = abs(gp - expected_pos)
                ok = diff <= abs(rev) * REL_TOLERANCE
                results.append((company, year, "Gross Profit = Revenue - CoS", ok, f"{gp:,.0f} vs {expected_pos:,.0f}"))

            # identity 3: CurrentAssets + NoncurrentAssets == Assets
            ca = get_val(df, company, year, "current_assets")
            nca = get_val(df, company, year, "noncurrent_assets")
            if a is not None and ca is not None and nca is not None:
                expected = ca + nca
                diff = abs(a - expected)
                ok = diff <= abs(a) * REL_TOLERANCE
                results.append((company, year, "Current + Non-current = Total Assets", ok, f"{a:,.0f} vs {expected:,.0f}"))

    return results


def check_currency(df) -> pd.DataFrame:
    """Which currency does each company report in?
    Filters out non-currency units like 'shares', 'pure', 'EUR/shares'
    which are units for EPS and ratio facts, not monetary currencies."""
    # ISO 4217 currencies are 3 uppercase letters only
    # anything else (shares, pure, EUR/shares, etc.) is a unit, not a currency
    import re
    iso_pattern = re.compile(r'^[A-Z]{3}$')

    def real_currencies(s):
        return sorted(set(
            x for x in s
            if x and iso_pattern.match(str(x))
        ))

    cur = df.groupby("company")["currency"].agg(real_currencies)
    return cur


def check_coverage(df) -> pd.DataFrame:
    """How much actually loaded per company?"""
    cov = df.groupby("company").agg(
        facts=("value", "size"),
        concepts=("normalized_name", "nunique"),
        years=("year", "nunique"),
        year_range=("year", lambda s: f"{min(s)}-{max(s)}"),
    )
    return cov


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", help="Only validate this company")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)

    engine = create_engine(db_url)
    df = fetch_all(engine)

    if df.empty:
        print("No data in the database yet.")
        sys.exit(0)

    if args.company:
        df = df[df["company"] == args.company]
        if df.empty:
            print(f"No data for company '{args.company}'.")
            sys.exit(1)

    print("=" * 70)
    print("COVERAGE - how much loaded per company")
    print("=" * 70)
    print(check_coverage(df).to_string())

    print("\n" + "=" * 70)
    print("CURRENCY - values are NOT comparable across different currencies")
    print("=" * 70)
    cur = check_currency(df)
    print(cur.to_string())
    all_currencies = set()
    for currencies in cur:
        all_currencies.update(currencies)
    if len(all_currencies) > 1:
        print(f"\n*** WARNING: {len(all_currencies)} different currencies present: {sorted(all_currencies)}")
        print("*** Cross-company comparisons of absolute values are INVALID until converted.")
        print("*** Ratios (margins, ROIC) are still fine - they're currency-neutral.")
    else:
        print(f"\nAll companies report in {sorted(all_currencies)[0]} - absolute values are comparable.")

    print("\n" + "=" * 70)
    print("ACCOUNTING IDENTITIES - a failure usually means a mapping error")
    print("=" * 70)
    results = check_identities(df)
    if not results:
        print("No identities could be checked (required concepts not present).")
    else:
        failures = [r for r in results if not r[3]]
        for company, year, label, ok, detail in results:
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {company:20s} {year}  {label:38s} {detail}")
        print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
        if failures:
            print(f"\n*** {len(failures)} FAILURES - investigate these mappings:")
            for company, year, label, ok, detail in failures:
                print(f"      {company} {year}: {label} ({detail})")

    print("\n" + "=" * 70)
    print("REGRESSION TESTS - known-good values that must never silently change")
    print("=" * 70)
    print("These are verified against L'Oreal's published 2024 annual report.")
    print("A failure means a mapping change broke something that was correct.\n")

    # Each tuple: (company, year, normalized_name, expected_value, tolerance_pct)
    # Values are in the filing's native units (full EUR, not millions)
    # Verified against L'Oreal Document d'Enregistrement Universel 2024
    REGRESSION_CASES = [
        ("L'Oreal", 2024, "revenue",        43486800000, 0.5),
        ("L'Oreal", 2024, "gross_profit",   32264600000, 0.5),
        ("L'Oreal", 2024, "profit_loss_from_operating_activities", 8263100000, 0.5),
        ("L'Oreal", 2024, "assets",         56353400000, 0.5),
        ("L'Oreal", 2024, "cash_flows_from_used_in_operating_activities", 8294600000, 1.0),
        # Add more as you manually verify other companies
    ]

    def get_fact(df, company, year, concept):
        mask = (
            (df["company"] == company) &
            (df["normalized_name"] == concept)
        )
        subset = df[mask].copy()
        if subset.empty:
            return None
        subset["year"] = subset.apply(
            lambda r: (r["end_date"] - timedelta(days=1)).year
            if r["period_type"] == "instant"
            else r["start_date"].year,
            axis=1,
        )
        year_subset = subset[subset["year"] == year]
        if year_subset.empty:
            return None
        vals = pd.to_numeric(year_subset["value"], errors="coerce").dropna()
        return vals.iloc[0] if not vals.empty else None

    reg_pass = reg_fail = reg_skip = 0
    for company, year, concept, expected, tol_pct in REGRESSION_CASES:
        actual = get_fact(df, company, year, concept)
        if actual is None:
            print(f"[SKIP] {company} {year} {concept}: not in current data")
            reg_skip += 1
            continue
        diff_pct = abs(float(actual) - expected) / abs(expected) * 100
        ok = diff_pct <= tol_pct
        status = "PASS" if ok else "FAIL"
        if ok:
            reg_pass += 1
        else:
            reg_fail += 1
        print(f"[{status}] {company} {year} {concept}")
        if not ok:
            print(f"       expected={expected:,.0f}  actual={float(actual):,.0f}  diff={diff_pct:.2f}%")

    print(f"\n{reg_pass} passed, {reg_fail} failed, {reg_skip} skipped")
    if reg_fail > 0:
        print("*** REGRESSION FAILURES - a mapping change broke known-good values.")
        print("*** Check git diff data/mappings/ifrs_concepts_v0.yaml for recent changes.")
