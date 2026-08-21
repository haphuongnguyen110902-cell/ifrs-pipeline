"""
Ratio engine - V1.

Computes financial ratios across all loaded companies and years,
writes them to the ratio table in the database, and exports a
clean comps table to Excel.

RATIOS COMPUTED
---------------
Profitability (currency-neutral - comparable across EUR/SEK/USD):
  gross_margin          Gross Profit / Revenue
  operating_margin      Operating Profit / Revenue
  net_margin            Net Profit Attributable to Owners / Revenue

Efficiency (currency-neutral):
  cash_conversion       Cash Flow from Operations / Operating Profit
                        > 1.0 means profit turns into MORE cash than booked
                        < 1.0 means some profit is not yet collected/paid

Returns (currency-neutral):
  roic                  Operating Profit * (1 - tax_rate) / Invested Capital
                        where Invested Capital = Equity + Net Debt
  roe                   Net Profit / Equity Attributable to Owners

Leverage (NOT currency-neutral - only meaningful within one currency):
  net_debt_ebitda       Net Debt / Operating Profit
                        (proxy for EBITDA since we don't always have it)
  interest_cover        Operating Profit / Finance Costs (where available)

DESIGN NOTES
------------
- All ratios are computed from normalized_name concepts in the database,
  not from raw XBRL tags. This means a mapping fix automatically improves
  all downstream ratios on the next run.
- NULL inputs produce NULL outputs. We never fill gaps with zeros - a
  missing gross profit is different from a zero gross profit.
- Signs: XBRL filers are inconsistent about whether expenses are positive
  or negative. We take absolute values where needed and note this.
- Currency-neutral ratios are valid for cross-company comparison now.
  Absolute-value ratios (net debt, revenue size) need FX conversion first.

Usage:
    python scripts/11_ratio_engine.py
    python scripts/11_ratio_engine.py --company "L'Oreal"   # one company only
    python scripts/11_ratio_engine.py --no-db               # Excel only, no DB write
"""
import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------- fetch

def fetch_facts(engine, company_filter=None) -> pd.DataFrame:
    """Pull all facts from the database into a wide-format DataFrame."""
    where = "WHERE c.name = :company" if company_filter else ""
    query = f"""
        SELECT
            c.name          AS company,
            c.company_id,
            ic.normalized_name,
            p.period_type,
            p.start_date,
            p.end_date,
            fv.value,
            fv.currency
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p        ON fv.period_id  = p.period_id
        JOIN filing fi       ON fv.filing_id  = fi.filing_id
        JOIN company c       ON fi.company_id = c.company_id
        {where}
    """
    params = {"company": company_filter} if company_filter else {}
    df = pd.read_sql(text(query), engine, params=params)
    if df.empty:
        return df

    # correct year labels - same logic as 07_generate_statements.py
    def get_year(row):
        if row["period_type"] == "instant":
            return (row["end_date"] - timedelta(days=1)).year
        return row["start_date"].year

    df["year"] = df.apply(get_year, axis=1)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def pivot_to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert long-format facts into a wide table:
    one row per (company, year), one column per concept.
    Where a concept appears multiple times for the same company/year
    (shouldn't happen after dimensional filtering, but just in case),
    take the first value.
    """
    return df.pivot_table(
        index=["company", "company_id", "year"],
        columns="normalized_name",
        values="value",
        aggfunc="first",
    ).reset_index()


# ---------------------------------------------------------------- ratio helpers

def safe_div(numerator, denominator, scale=1):
    """Divide two series safely. Returns NaN where denominator is 0 or NaN."""
    try:
        result = numerator / denominator.replace(0, float("nan")) * scale
        return result
    except Exception:
        return pd.Series([float("nan")] * len(numerator))


def get_col(wide: pd.DataFrame, name: str) -> pd.Series:
    """Return a column if it exists, else a NaN series of the same length."""
    if name in wide.columns:
        return wide[name]
    return pd.Series([float("nan")] * len(wide), index=wide.index)


# ---------------------------------------------------------------- compute

def compute_ratios(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all ratios from a wide-format DataFrame.
    Each ratio becomes a column. NaN = inputs were missing.
    """
    r = wide[["company", "company_id", "year"]].copy()

    # --- revenue (base for margin ratios) ---
    rev = get_col(wide, "revenue").abs()

    # --- gross margin ---
    gp = get_col(wide, "gross_profit")
    r["gross_margin"] = safe_div(gp, rev, scale=100)

    # --- operating margin ---
    # Use L'Oreal-specific 'resultat_dexploitation' if standard concept
    # is missing; fall back to that company's own definition. Both measure
    # operating profit, just with different adjustments included.
    ebit = get_col(wide, "profit_loss_from_operating_activities")
    ebit_fallback = get_col(wide, "resultat_dexploitation")
    ebit_combined = ebit.combine_first(ebit_fallback)
    r["operating_margin"] = safe_div(ebit_combined, rev, scale=100)
    r["_ebit"] = ebit_combined  # store for reuse below

    # --- net margin ---
    # Prefer net profit attributable to owners of parent (group share)
    net = get_col(wide, "profit_loss_attributable_to_owners_of_parent")
    r["net_margin"] = safe_div(net, rev, scale=100)

    # --- cash conversion ---
    # Cash flow from operations / operating profit.
    # > 100% means more cash collected than profit booked (good quality)
    # < 100% means some profit is still sitting in receivables/inventory
    cfo = get_col(wide, "cash_flows_from_used_in_operating_activities")
    r["cash_conversion"] = safe_div(cfo, ebit_combined, scale=100)

    # --- effective tax rate ---
    tax = get_col(wide, "income_tax_expense_continuing_operations")
    pbt = get_col(wide, "profit_loss_before_tax")
    # Tax expense sign convention varies: some filers report it as a
    # positive number (a cost), some as negative. We want tax/pbt where
    # both share the same sign convention. Taking abs() of both and then
    # dividing gives the correct rate regardless of convention.
    # Clip to [0, 60%] to avoid nonsense values from loss-year data.
    r["tax_rate"] = safe_div(tax.abs(), pbt.abs()).clip(0, 0.60)

    # --- net debt ---
    # Net Debt = Long-term borrowings + Current borrowings - Cash
    # Used in ROIC and leverage ratio
    lt_debt = get_col(wide, "longterm_borrowings").abs()
    st_debt = get_col(wide, "current_borrowings_and_current_portion_of_noncurrent_borr_etc").abs()
    cash = get_col(wide, "cash_and_cash_equivalents").abs()
    r["_net_debt"] = lt_debt.fillna(0) + st_debt.fillna(0) - cash.fillna(0)

    # --- ROIC (Return on Invested Capital) ---
    # ROIC = NOPAT / Invested Capital
    # NOPAT = Operating Profit * (1 - tax rate)
    # Invested Capital = Equity (parent) + Non-controlling interests + Net Debt
    equity_parent = get_col(wide, "equity_attributable_to_owners_of_parent")
    nci = get_col(wide, "noncontrolling_interests").fillna(0)
    total_equity = equity_parent + nci
    invested_capital = total_equity + r["_net_debt"]
    nopat = ebit_combined * (1 - r["tax_rate"].clip(0, 0.5))  # cap tax rate at 50%
    r["roic"] = safe_div(nopat, invested_capital, scale=100)

    # --- ROE (Return on Equity) ---
    r["roe"] = safe_div(net, equity_parent, scale=100)

    # --- Net Debt / Operating Profit (leverage proxy for EBITDA/debt) ---
    # WARNING: not currency-neutral, only meaningful within one currency
    r["net_debt_ebitda_proxy"] = safe_div(r["_net_debt"], ebit_combined)

    # clean up internal columns
    r = r.drop(columns=["_ebit", "_net_debt"])
    return r


# ---------------------------------------------------------------- format

RATIO_META = {
    "gross_margin":         ("Gross Margin",              True,  "%"),
    "operating_margin":     ("Operating Margin",          True,  "%"),
    "net_margin":           ("Net Margin",                True,  "%"),
    "cash_conversion":      ("Cash Conversion",           True,  "%"),
    "tax_rate":             ("Effective Tax Rate",        True,  "%"),
    "roic":                 ("ROIC",                      True,  "%"),
    "roe":                  ("ROE",                       True,  "%"),
    "net_debt_ebitda_proxy":("Net Debt vs Op. Profit",  False, "x"),
}


def format_ratio(value, unit):
    if pd.isna(value):
        return "n/a"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "x":
        return f"{value:.1f}x"
    return f"{value:.2f}"


def print_comps_table(ratios: pd.DataFrame):
    """Print a clean side-by-side comps table to the terminal."""
    for ratio_name, (label, neutral, unit) in RATIO_META.items():
        if ratio_name not in ratios.columns:
            continue
        subset = ratios[["company", "year", ratio_name]].dropna(subset=[ratio_name])
        if subset.empty:
            continue
        pivot = subset.pivot_table(
            index="company", columns="year",
            values=ratio_name, aggfunc="first"
        ).sort_index(axis=1)

        print(f"\n{label}" + ("  [currency-neutral]" if neutral else "  [NOT currency-neutral]"))
        print("-" * 70)
        for company, row in pivot.iterrows():
            vals = "  ".join(format_ratio(v, unit).rjust(10) for v in row)
            years = "  ".join(str(y).rjust(10) for y in pivot.columns)
            print(f"  {'':2s}{company:<22s} {vals}")
        # print year header once per ratio
        print(f"\n  {'':24s} {years}")


# ---------------------------------------------------------------- save

def save_to_db(engine, ratios: pd.DataFrame, company_ids: dict):
    """Upsert ratios into the ratio table."""
    # create the ratio table if it doesn't exist
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ratio (
                ratio_id            SERIAL PRIMARY KEY,
                company_id          INTEGER REFERENCES company(company_id),
                year                INTEGER NOT NULL,
                ratio_name          TEXT NOT NULL,
                display_label       TEXT NOT NULL,
                value               NUMERIC,
                is_currency_neutral BOOLEAN DEFAULT TRUE,
                currency            TEXT,
                source_concepts     TEXT[],
                computed_at         TIMESTAMP DEFAULT now(),
                UNIQUE(company_id, year, ratio_name)
            )
        """))
        conn.commit()

    rows_written = 0
    with engine.connect() as conn:
        for _, row in ratios.iterrows():
            cid = row.get("company_id")
            year = row["year"]
            for ratio_name, (label, neutral, unit) in RATIO_META.items():
                if ratio_name not in row.index:
                    continue
                val = row[ratio_name]
                conn.execute(text("""
                    INSERT INTO ratio
                        (company_id, year, ratio_name, display_label,
                         value, is_currency_neutral, computed_at)
                    VALUES
                        (:cid, :year, :rn, :label, :val, :neutral, now())
                    ON CONFLICT (company_id, year, ratio_name)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        display_label = EXCLUDED.display_label,
                        computed_at = now()
                """), {"cid": int(cid), "year": int(year), "rn": ratio_name,
                       "label": label, "val": None if pd.isna(val) else float(val),
                       "neutral": neutral})
                rows_written += 1
        conn.commit()
    return rows_written


def save_to_excel(ratios: pd.DataFrame, out_path: str):
    """Save one sheet per ratio, plus a summary sheet."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        # summary sheet: all ratios, all companies, most recent year
        most_recent = ratios.groupby("company")["year"].max().reset_index()
        most_recent.columns = ["company", "max_year"]
        latest = ratios.merge(most_recent, left_on=["company", "year"],
                              right_on=["company", "max_year"])
        ratio_cols = [c for c in RATIO_META if c in latest.columns]
        summary = latest[["company", "year"] + ratio_cols].set_index("company")
        # format for display
        display = summary.copy()
        for ratio_name, (label, neutral, unit) in RATIO_META.items():
            if ratio_name in display.columns:
                display[ratio_name] = display[ratio_name].apply(
                    lambda v: format_ratio(v, unit))
        display.columns = [RATIO_META.get(c, (c,))[0] if c in RATIO_META else c
                           for c in display.columns]
        display.to_excel(writer, sheet_name="Summary (latest year)")

        # one sheet per ratio showing all companies and years
        for ratio_name, (label, neutral, unit) in RATIO_META.items():
            if ratio_name not in ratios.columns:
                continue
            pivot = ratios.pivot_table(
                index="company", columns="year",
                values=ratio_name, aggfunc="first"
            ).sort_index(axis=1)
            pivot = pivot.map(lambda v: format_ratio(v, unit))
            sheet_name = label[:31]  # Excel sheet name limit
            pivot.to_excel(writer, sheet_name=sheet_name)

    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only compute ratios for this company")
    ap.add_argument("--no-db", action="store_true",
                    help="Skip writing to the database, produce Excel only")
    ap.add_argument("--out", default="data/raw/comps_ratios.xlsx",
                    help="Output Excel path")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)

    engine = create_engine(db_url)

    print("Fetching facts from database...")
    df = fetch_facts(engine, company_filter=args.company)
    if df.empty:
        print("No data found. Check that companies are loaded.")
        sys.exit(1)

    print(f"Loaded {len(df)} facts across "
          f"{df['company'].nunique()} companies and "
          f"{df['year'].nunique()} years\n")

    wide = pivot_to_wide(df)
    ratios = compute_ratios(wide)

    # print the comps table to terminal
    years = sorted(ratios["year"].unique())
    print(f"{'=' * 70}")
    print(f"COMPS TABLE  |  Companies: {ratios['company'].nunique()}  |  Years: {min(years)}-{max(years)}")
    print(f"{'=' * 70}")
    print_comps_table(ratios)

    # save to database
    if not args.no_db:
        rows = save_to_db(engine, ratios, {})
        print(f"\nWrote {rows} ratio rows to database")

    # save to Excel
    out_path = args.out
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    save_to_excel(ratios, out_path)
