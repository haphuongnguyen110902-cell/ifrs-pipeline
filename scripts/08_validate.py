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
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# same fact resolution and same "is this a financial company" rule as the ratios
r11 = _load("ratio_engine_11", "11_ratio_engine.py")

# Tolerance for identity checks. Filings round to a thousand or a million, so
# cross-footing gaps are rounding-sized: at the scale of a balance sheet that is
# well under 0.01%. 0.1% leaves headroom for small companies reporting in
# millions while still catching a missing line (the old 1% let a genuine 0.31%
# gap through - Recordati 2022's held-for-distribution assets - and would let
# a much larger real omission through too).
REL_TOLERANCE = 0.001  # 0.1%

# IFRS 5 assets held for sale / for distribution are presented on their own
# line, neither current nor non-current, so `current + non-current` can
# legitimately fall short of total assets by exactly this amount. Matched by
# prefix because the mapping stores several (truncated, and `_x`-suffixed) names.
HELD_FOR_SALE_PREFIX = "noncurrent_assets_or_disposal_groups_classified_as_held_for"


def fetch_all(engine) -> pd.DataFrame:
    """Every fact, reduced to ONE row per (company, year, concept) by the ratio
    engine's deterministic rule (representative period, then latest filing).
    This used to take whichever row the database returned first, so where two
    filings - or two dates in one year - disagreed the checks could pass or
    fail depending on row order."""
    facts = r11.fetch_facts(engine)
    if facts.empty:
        return facts
    resolved, _ = r11.resolve_fact_conflicts(facts)
    return resolved


def financial_company_names(engine) -> dict:
    """{company name: why} for companies whose statements are not built like an
    industrial's (see 11_ratio_engine.py) - some industrial identities do not
    apply to them."""
    profiles = r11.fetch_company_profiles(engine)
    reasons = r11.financial_company_reasons(profiles, r11.load_reporting_model_overrides())
    names = dict(zip(profiles["company_id"], profiles["name"]))
    return {names[cid]: why for cid, why in reasons.items()}


def get_val(df, company, year, concept):
    """Look up one value, returns None if absent."""
    hit = df[(df["company"] == company) & (df["year"] == year) & (df["normalized_name"] == concept)]
    if hit.empty:
        return None
    return hit.iloc[0]["value"]


def held_for_sale(df, company, year):
    """Total assets classified as held for sale / distribution for one company-
    year (0.0 when there are none). Summed across the several concept names the
    mapping uses for it."""
    hit = df[(df["company"] == company) & (df["year"] == year)
             & df["normalized_name"].str.startswith(HELD_FOR_SALE_PREFIX)]
    return float(hit["value"].sum()) if not hit.empty else 0.0


def check_identities(df, tolerance: float = REL_TOLERANCE, skip_gross_profit=()) -> list:
    """Accounting identities that must hold if the mapping is right. Returns
    (company, year, label, ok, detail) tuples.

    skip_gross_profit: companies for which "Gross Profit = Revenue - Cost of
    Sales" does not apply. A financial-model statement puts other lines between
    revenue and its "gross profit" (Adyen: 'costs incurred from financial
    institutions' and net interest income - the exact 180.4M that the identity
    could not explain), so the identity is inapplicable, not violated."""
    results = []
    skip_gross_profit = set(skip_gross_profit)

    for company in sorted(df["company"].unique()):
        for year in sorted(df[df["company"] == company]["year"].unique()):
            # identity 1: Assets == EquityAndLiabilities
            a = get_val(df, company, year, "assets")
            el = get_val(df, company, year, "equity_and_liabilities")
            if a is not None and el is not None:
                ok = bool(abs(a - el) <= abs(a) * tolerance)
                results.append((company, year, "Assets = Equity + Liabilities", ok, f"{a:,.0f} vs {el:,.0f}"))

            # identity 2: GrossProfit == Revenue - CostOfSales
            gp = get_val(df, company, year, "gross_profit")
            rev = get_val(df, company, year, "revenue")
            cos = get_val(df, company, year, "cost_of_sales")
            if gp is not None and rev is not None and cos is not None and company not in skip_gross_profit:
                # cost of sales may be tagged positive (as a magnitude) or
                # negative (as a signed deduction) - accept whichever matches
                expected_pos = rev - abs(cos)
                ok = bool(abs(gp - expected_pos) <= abs(rev) * tolerance)
                results.append((company, year, "Gross Profit = Revenue - CoS", ok, f"{gp:,.0f} vs {expected_pos:,.0f}"))

            # identity 3: CurrentAssets + NoncurrentAssets == Assets, under EITHER
            # presentation of held-for-sale assets: a separate line (add it) or
            # inside current assets (already counted)
            ca = get_val(df, company, year, "current_assets")
            nca = get_val(df, company, year, "noncurrent_assets")
            if a is not None and ca is not None and nca is not None:
                base = ca + nca
                with_hfs = base + held_for_sale(df, company, year)
                best = min((base, with_hfs), key=lambda x: abs(a - x))
                ok = bool(abs(a - best) <= abs(a) * tolerance)
                results.append((company, year, "Current + Non-current = Total Assets", ok, f"{a:,.0f} vs {best:,.0f}"))

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


# Known-good values verified by hand against a published annual report.
# Each: (company, year, normalized_name, expected_value, tolerance_pct). Values
# are in the filing's native units (full EUR, not millions). L'Oreal's are from
# its Document d'Enregistrement Universel 2024. Add more as other companies'
# figures are verified against their reports - a company with no case here is
# checked only by the accounting identities above.
REGRESSION_CASES = [
    ("L'Oreal", 2024, "revenue",        43486800000, 0.5),
    ("L'Oreal", 2024, "gross_profit",   32264600000, 0.5),
    ("L'Oreal", 2024, "profit_loss_from_operating_activities", 8263100000, 0.5),
    ("L'Oreal", 2024, "assets",         56353400000, 0.5),
    ("L'Oreal", 2024, "cash_flows_from_used_in_operating_activities", 8294600000, 1.0),
]


def run_identity_and_regression_checks(df, financial=None, regression_cases=None) -> int:
    """Prints the identity and known-value checks and returns how many FAILED.
    The caller turns a non-zero count into a non-zero exit code: this script
    used to print "FAILURES" and exit 0, so run_pipeline.py's validation gate
    (and CI's `validate` mode) could never actually stop anything.

    financial: {company name: reason} - the gross-profit identity is not
    applied to these (see check_identities)."""
    financial = financial or {}
    regression_cases = REGRESSION_CASES if regression_cases is None else regression_cases
    n_failed = 0

    print("=" * 70)
    print("ACCOUNTING IDENTITIES - a failure usually means a mapping error")
    print("=" * 70)
    results = check_identities(df, skip_gross_profit=set(financial))
    skipped = sorted(set(financial) & set(df["company"].unique()))
    if skipped:
        print("Gross-profit identity not applied (financial reporting model): "
              + ", ".join(f"{c} ({financial[c][:60]}...)" if len(financial[c]) > 60 else f"{c} ({financial[c]})"
                          for c in skipped) + "\n")
    if not results:
        print("No identities could be checked (required concepts not present).")
    else:
        failures = [r for r in results if not r[3]]
        for company, year, label, ok, detail in results:
            print(f"[{'PASS' if ok else 'FAIL'}] {company:20s} {year}  {label:38s} {detail}")
        print(f"\n{len(results) - len(failures)}/{len(results)} checks passed "
              f"(tolerance {REL_TOLERANCE:.1%})")
        if failures:
            n_failed += len(failures)
            print(f"\n*** {len(failures)} FAILURES - investigate these mappings:")
            for company, year, label, ok, detail in failures:
                print(f"      {company} {year}: {label} ({detail})")

    print("\n" + "=" * 70)
    print("REGRESSION TESTS - known-good values that must never silently change")
    print("=" * 70)
    print("Verified by hand against published annual reports (see REGRESSION_CASES).")
    print("A failure means a mapping change broke something that was correct.\n")
    reg_pass = reg_fail = reg_skip = 0
    for company, year, concept, expected, tol_pct in regression_cases:
        actual = get_val(df, company, year, concept)
        if actual is None or pd.isna(actual):
            print(f"[SKIP] {company} {year} {concept}: not in current data")
            reg_skip += 1
            continue
        diff_pct = abs(float(actual) - expected) / abs(expected) * 100
        ok = diff_pct <= tol_pct
        reg_pass += ok
        reg_fail += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {company} {year} {concept}")
        if not ok:
            print(f"       expected={expected:,.0f}  actual={float(actual):,.0f}  diff={diff_pct:.2f}%")
    print(f"\n{reg_pass} passed, {reg_fail} failed, {reg_skip} skipped")
    if reg_fail:
        n_failed += reg_fail
        print("*** REGRESSION FAILURES - a mapping change broke known-good values.")
        print("*** Check git diff data/mappings/ifrs_concepts_v0.yaml for recent changes.")
    return n_failed


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

    n_failed = run_identity_and_regression_checks(df, financial=financial_company_names(engine))
    if n_failed:
        print(f"\nVALIDATION FAILED: {n_failed} check(s) did not pass.")
        sys.exit(1)
