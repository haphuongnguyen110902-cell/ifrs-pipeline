"""
Financial forensics module.

Automatically flags earnings quality issues and financial anomalies
across all loaded companies. Not fraud detection - these are signals
that warrant further investigation by an analyst.

Flags computed:
  1. LOW_CASH_CONVERSION     CFO / Operating Profit < 80%
  2. CASH_CONVERSION_DROP    YoY drop in cash conversion > 20pp (downgraded to
                              low severity if either year's Operating Profit
                              was thin - see THIN_DENOMINATOR - since the
                              delta is then measuring a distorted baseline,
                              not real deterioration)
  3. MARGIN_COMPRESSION      Operating margin YoY drop > 3pp
  4. REVENUE_ACCELERATION    Revenue growth > 20% YoY (unusual for mature cos)
  5. REVENUE_DECELERATION    Revenue growth < -10% YoY
  6. HIGH_LEVERAGE           Net Debt / Operating Profit > 4x
  7. NEGATIVE_NET_DEBT       Net cash position (positive signal)
  8. TAX_RATE_ANOMALY        Effective tax rate < 10% or > 50%
  9. THIN_DENOMINATOR        Operating margin < 5pp AND cash_conversion/leverage
                              look extreme - the extreme reading is likely an
                              artifact of a near-zero Operating Profit, not a
                              genuine earnings-quality or leverage signal

Output:
  - Terminal: summary table of all flags
  - Excel: data/raw/forensics_report.xlsx with detail + flag explanation
  - DB: forensics_flag table (see sql/schema_forensics.sql), one row per
    flag actually triggered. Added in PLAN.md WP2 - flags used to be
    recomputed from the `ratio` table on every run and never persisted
    ("cheap enough at this dataset size" was true, but it coupled
    webapp/app.py to importlib-loading this script per page render, and
    made a screener query like "every company with >= 2 HIGH flags"
    impossible with nothing stored).

    save_to_db() DELETES the existing rows for whichever companies are in
    scope for this run before inserting the freshly computed set, rather
    than upserting - unlike a ratio value that persists across runs
    unless replaced, a forensics flag can legitimately stop triggering
    (e.g. a ratio-engine bugfix corrects the underlying number), and an
    upsert-only write would leave that now-wrong flag sitting in the
    table forever with nothing to overwrite it. Delete-then-insert scoped
    to the run's own companies means a `--company` filtered run never
    touches other companies' rows.

Usage:
    python scripts/15_forensics.py
    python scripts/15_forensics.py --company "L'Oreal"
    python scripts/15_forensics.py --min-severity medium
"""
import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

FORENSICS_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_forensics.sql"


# ---------------------------------------------------------------- flag definitions

FLAGS = {
    "LOW_CASH_CONVERSION": {
        "label": "Low Cash Conversion",
        "severity": "medium",
        "description": "CFO / Operating Profit < 80% — earnings not converting to cash. "
                       "Could indicate aggressive revenue recognition, working capital build, "
                       "or one-time items inflating reported profit.",
        "what_to_check": "Receivables trend, inventory build, accruals vs cash items in P&L.",
    },
    "CASH_CONVERSION_DROP": {
        "label": "Cash Conversion Deterioration",
        "severity": "high",
        "description": "Cash conversion fell >20pp YoY — sudden deterioration in earnings quality.",
        "what_to_check": "What changed in working capital? Any large non-cash items this year?",
    },
    "MARGIN_COMPRESSION": {
        "label": "Operating Margin Compression",
        "severity": "medium",
        "description": "Operating margin fell >3pp YoY — meaningful profitability decline.",
        "what_to_check": "Cost of sales trend, SG&A growth vs revenue, pricing power.",
    },
    "MARGIN_EXPANSION": {
        "label": "Strong Margin Expansion",
        "severity": "low",
        "description": "Operating margin rose >5pp YoY — positive but worth understanding.",
        "what_to_check": "Is this structural (mix shift, pricing) or one-off (cost cuts, disposals)?",
    },
    "REVENUE_ACCELERATION": {
        "label": "Revenue Acceleration",
        "severity": "low",
        "description": "Revenue growth >20% YoY — unusual for mature European companies.",
        "what_to_check": "Organic vs M&A? FX tailwind? Sustainable?",
    },
    "REVENUE_DECELERATION": {
        "label": "Revenue Deceleration",
        "severity": "medium",
        "description": "Revenue declined >10% YoY.",
        "what_to_check": "Volumes vs pricing? Market share? Discontinued operations?",
    },
    "HIGH_LEVERAGE": {
        "label": "High Leverage",
        "severity": "high",
        "description": "Net Debt / Operating Profit > 4x — elevated financial risk.",
        "what_to_check": "Debt maturity profile, covenant headroom, FCF vs debt service.",
    },
    "NEGATIVE_NET_DEBT": {
        "label": "Net Cash Position",
        "severity": "low",
        "description": "Company holds more cash than debt — strong balance sheet. "
                       "Could indicate under-investment or M&A optionality.",
        "what_to_check": "Capital allocation plans, dividend policy, M&A pipeline.",
    },
    "TAX_RATE_ANOMALY": {
        "label": "Tax Rate Anomaly",
        "severity": "medium",
        "description": "Effective tax rate < 10% or > 50% — outside normal range.",
        "what_to_check": "Deferred tax reversals, tax credits, jurisdictional mix, one-offs.",
    },
    "THIN_DENOMINATOR": {
        "label": "Ratio Distorted by Thin Operating Profit",
        "severity": "low",
        "description": "Operating margin near zero this year — cash_conversion and "
                       "net_debt_ebitda_proxy divide by Operating Profit, so a tiny "
                       "denominator can blow the ratio up to a huge or meaningless "
                       "number even though nothing unusual happened operationally "
                       "(e.g. EssilorLuxottica 2020: 3.1% margin -> 653% cash "
                       "conversion, a COVID artifact, not an earnings-quality signal).",
        "what_to_check": "Re-read any LOW_CASH_CONVERSION / CASH_CONVERSION_DROP / "
                         "HIGH_LEVERAGE flag for this SAME company/year with caution - "
                         "the extreme reading is likely a denominator artifact, not "
                         "genuine deterioration. Compare against absolute Operating "
                         "Profit and CFO in the source filing before drawing conclusions.",
    },
    "PERNOD_FYE_WARNING": {
        "label": "Off-Calendar Fiscal Year",
        "severity": "low",
        "description": "Company has non-December fiscal year end. "
                       "Direct YoY comparisons with December filers cover different economic periods.",
        "what_to_check": "Adjust period labels when comparing across companies.",
    },
}


# Below this operating margin (absolute value, in percentage points), Operating
# Profit is considered "thin" - any ratio dividing by it (cash_conversion,
# net_debt_ebitda_proxy) can swing wildly without reflecting a real change in
# the business. 5pp is deliberately loose: EssilorLuxottica's real 2020 case
# (3.1% margin) sits comfortably inside it, while a normal ~15-20% margin
# company doesn't get flagged just for a mediocre year.
THIN_MARGIN_THRESHOLD = 5.0


# ---------------------------------------------------------------- fetch

_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def fetch_off_calendar_fye(engine, company_filter=None) -> dict:
    """{company_name: "Month Day"} for every company whose
    fiscal_year_end_month isn't 12 - sourced from company.fiscal_year_end_month/day
    (populated by migration_002_company_metadata.sql from filing.fiscal_year_end,
    backfilled from companies.yaml where the filing itself didn't have it -
    see 09_batch_load.py's get_or_create_company()). Replaces the hardcoded
    pernod_companies list compute_flags() used to carry (PLAN.md WP3c)."""
    where = "AND name = :company" if company_filter else ""
    query = f"""
        SELECT name, fiscal_year_end_month, fiscal_year_end_day FROM company
        WHERE fiscal_year_end_month IS NOT NULL AND fiscal_year_end_month != 12
        {where}
    """
    params = {"company": company_filter} if company_filter else {}
    rows = pd.read_sql(text(query), engine, params=params)
    return {
        r["name"]: f"{_MONTH_NAMES[int(r['fiscal_year_end_month'])]} {int(r['fiscal_year_end_day'])}"
        for _, r in rows.iterrows()
    }


def fetch_ratios(engine, company_filter=None) -> pd.DataFrame:
    """Fetch computed ratios from the DB ratio table."""
    where = "WHERE c.name = :company" if company_filter else ""
    query = f"""
        SELECT c.name AS company, r.year, r.ratio_name, r.value
        FROM ratio r
        JOIN company c ON r.company_id = c.company_id
        {where}
        ORDER BY c.name, r.year, r.ratio_name
    """
    params = {"company": company_filter} if company_filter else {}
    df = pd.read_sql(text(query), engine, params=params)
    if df.empty:
        return df
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def pivot_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Wide format: one row per (company, year), one column per ratio."""
    return df.pivot_table(
        index=["company", "year"],
        columns="ratio_name",
        values="value",
        aggfunc="first",
    ).reset_index()


# ---------------------------------------------------------------- flag engine

def safe_year_col(co: pd.DataFrame, year: int, col: str) -> pd.Series:
    """Like co[co['year']==year][col], but never raises KeyError when
    `col` doesn't exist at all - which happens when compute_flags() is
    called on a SINGLE company's data (e.g. from app.py's per-company
    forensics view) and that company has zero rows for that ratio_name
    anywhere in its history. Running the full universe together never
    hits this, because pivot_ratios() only omits a column when NO
    company in the filtered set has any row for it - with 11 companies
    together, someone almost always does. Filtered to one company, that
    assumption breaks."""
    if col not in co.columns:
        return pd.Series(dtype=float)
    return co[co["year"] == year][col]


def compute_flags(wide: pd.DataFrame, off_calendar_fye: dict = None) -> pd.DataFrame:
    """
    Scan the ratio table for each company/year and produce a flags table.
    Returns one row per flag triggered, with company, year, flag_id, value,
    severity, and human-readable description.

    off_calendar_fye: optional {company_name: "Month Day"} for companies
    whose fiscal year doesn't end December 31 (e.g. {"Pernod Ricard":
    "June 30"}) - see fetch_off_calendar_fye() below, sourced from
    company.fiscal_year_end_month/day (PLAN.md WP3c). Replaces what used
    to be a hardcoded `pernod_companies = ["Pernod Ricard"]` list - this
    function no longer has ANY company name baked in; it fires the same
    PERNOD_FYE_WARNING flag_id (kept as-is rather than renamed, matching
    this project's own established precedent of keeping a
    now-not-quite-accurate name over churning every downstream reader -
    see net_debt_ebitda_proxy in 11_ratio_engine.py/24_credit.py) for
    WHICHEVER company the caller says is off-calendar. Defaults to None
    (no flags of this kind) so every existing caller/test that doesn't
    pass it keeps working unchanged.
    """
    off_calendar_fye = off_calendar_fye or {}
    rows = []

    for company in wide["company"].unique():
        co = wide[wide["company"] == company].sort_values("year").copy()

        for _, row in co.iterrows():
            year = int(row["year"])
            cc = row.get("cash_conversion")
            op_margin = row.get("operating_margin")
            rev_growth = row.get("_rev_growth")  # computed below
            leverage = row.get("net_debt_ebitda_proxy")
            tax = row.get("tax_rate")

            def flag(flag_id, value, detail="", severity_override=None):
                meta = FLAGS[flag_id]
                rows.append({
                    "company": company,
                    "year": year,
                    "flag_id": flag_id,
                    "label": meta["label"],
                    "severity": severity_override or meta["severity"],
                    "value": round(float(value), 1) if pd.notna(value) else None,
                    "detail": detail or meta["description"],
                    "what_to_check": meta["what_to_check"],
                })

            # --- cash conversion flags ---
            if pd.notna(cc):
                if cc < 80:
                    flag("LOW_CASH_CONVERSION", cc, f"Cash conversion: {cc:.1f}%")

            # --- margin flags ---
            if pd.notna(op_margin):
                # get prior year margin
                prior = safe_year_col(co, year - 1, "operating_margin")
                if not prior.empty and pd.notna(prior.iloc[0]):
                    delta = op_margin - prior.iloc[0]
                    if delta < -3:
                        flag("MARGIN_COMPRESSION", delta,
                             f"Operating margin: {prior.iloc[0]:.1f}% → {op_margin:.1f}% (Δ{delta:.1f}pp)")
                    elif delta > 5:
                        flag("MARGIN_EXPANSION", delta,
                             f"Operating margin: {prior.iloc[0]:.1f}% → {op_margin:.1f}% (Δ+{delta:.1f}pp)")

            # --- cash conversion YoY ---
            if pd.notna(cc):
                prior_cc = safe_year_col(co, year - 1, "cash_conversion")
                if not prior_cc.empty and pd.notna(prior_cc.iloc[0]):
                    delta_cc = cc - prior_cc.iloc[0]
                    if delta_cc < -20:
                        # A big drop can be genuine deterioration, OR it can be
                        # nothing more than reverting FROM a THIN_DENOMINATOR
                        # year's inflated ratio back to normal (e.g.
                        # EssilorLuxottica 653.3% in 2020 -> 195.4% in 2021 is
                        # a -457.9pp "drop" that is really the 2020 artifact
                        # unwinding, not 2021 getting worse). Check both
                        # endpoints of the delta for a thin denominator before
                        # trusting the flag at full severity.
                        prior_op_margin = safe_year_col(co, year - 1, "operating_margin")
                        thin_this_year = pd.notna(op_margin) and abs(op_margin) < THIN_MARGIN_THRESHOLD
                        thin_prior_year = (not prior_op_margin.empty
                                            and pd.notna(prior_op_margin.iloc[0])
                                            and abs(prior_op_margin.iloc[0]) < THIN_MARGIN_THRESHOLD)
                        if thin_prior_year or thin_this_year:
                            distorted_year = year - 1 if thin_prior_year else year
                            flag("CASH_CONVERSION_DROP", delta_cc,
                                 f"Cash conversion: {prior_cc.iloc[0]:.1f}% → {cc:.1f}% (Δ{delta_cc:.1f}pp) "
                                 f"— baseline distorted: {distorted_year} had a thin Operating Profit "
                                 f"denominator (see THIN_DENOMINATOR flag), so this delta overstates "
                                 f"real deterioration",
                                 severity_override="low")
                        else:
                            flag("CASH_CONVERSION_DROP", delta_cc,
                                 f"Cash conversion: {prior_cc.iloc[0]:.1f}% → {cc:.1f}% (Δ{delta_cc:.1f}pp)")

            # --- leverage ---
            if pd.notna(leverage):
                if leverage > 4:
                    # net_debt_ebitda_proxy divides by the SAME Operating Profit
                    # denominator as cash_conversion, so it needs the same
                    # thin-margin caution (see the CASH_CONVERSION_DROP block
                    # above and THIN_DENOMINATOR below) - without this, a
                    # thin-margin year could print a false "high" leverage
                    # warning that's really just a small-denominator artifact.
                    thin_this_year = pd.notna(op_margin) and abs(op_margin) < THIN_MARGIN_THRESHOLD
                    if thin_this_year:
                        flag("HIGH_LEVERAGE", leverage,
                             f"Net Debt / Op. Profit: {leverage:.1f}x — baseline distorted: "
                             f"Operating Profit was thin this year ({op_margin:.1f}% margin, "
                             f"see THIN_DENOMINATOR flag), so this multiple overstates real leverage",
                             severity_override="low")
                    else:
                        flag("HIGH_LEVERAGE", leverage, f"Net Debt / Op. Profit: {leverage:.1f}x")
                elif leverage < 0:
                    flag("NEGATIVE_NET_DEBT", leverage, f"Net Cash: {abs(leverage):.1f}x Op. Profit")

            # --- tax rate ---
            if pd.notna(tax):
                if tax < 10 or tax > 50:
                    flag("TAX_RATE_ANOMALY", tax, f"Effective tax rate: {tax:.1f}%")

            # --- thin denominator: is Operating Profit itself near zero this year? ---
            # cash_conversion = CFO / OP and net_debt_ebitda_proxy = Net Debt / OP
            # both divide by OP, so when OP is tiny relative to revenue (op_margin
            # near zero), either ratio can print an extreme number that reflects
            # the SIZE of the denominator, not a real change in cash quality or
            # leverage. Flag the YEAR, and name which of those ratios actually
            # looks extreme, so LOW_CASH_CONVERSION/HIGH_LEVERAGE flags on the
            # same row can be read with the right amount of skepticism.
            if pd.notna(op_margin) and abs(op_margin) < THIN_MARGIN_THRESHOLD:
                affected = []
                if pd.notna(cc) and (cc > 250 or cc < -50):
                    affected.append(f"cash_conversion={cc:.1f}%")
                if pd.notna(leverage) and abs(leverage) > 4:
                    affected.append(f"net_debt_ebitda_proxy={leverage:.1f}x")
                if affected:
                    flag("THIN_DENOMINATOR", op_margin,
                         f"Operating margin only {op_margin:.1f}% this year, "
                         f"which makes {', '.join(affected)} unreliable as a signal")

        # --- revenue growth (needs two years of data) ---
        rev_col = co.copy()
        if "gross_margin" in wide.columns:
            # proxy: we don't store revenue directly in ratio table
            # use YoY change in gross_margin as a signal instead
            pass

    # --- off-calendar FYE warning (company-level, not per-year) ---
    for company in wide["company"].unique():
        if company in off_calendar_fye:
            years = sorted(wide[wide["company"] == company]["year"].unique())
            if years:
                rows.append({
                    "company": company,
                    "year": years[-1],  # flag on most recent year
                    "flag_id": "PERNOD_FYE_WARNING",
                    "label": FLAGS["PERNOD_FYE_WARNING"]["label"],
                    "severity": FLAGS["PERNOD_FYE_WARNING"]["severity"],
                    "value": None,
                    "detail": f"Fiscal year ends {off_calendar_fye[company]} — not December 31",
                    "what_to_check": FLAGS["PERNOD_FYE_WARNING"]["what_to_check"],
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------- fetch revenue for growth

def fetch_revenue_growth(engine, company_filter=None) -> pd.DataFrame:
    """Compute YoY revenue growth directly from fact_value."""
    where = "AND c.name = :company" if company_filter else ""
    query = f"""
        SELECT
            c.name AS company,
            p.start_date,
            fv.value::numeric AS revenue
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p ON fv.period_id = p.period_id
        JOIN filing fi ON fv.filing_id = fi.filing_id
        JOIN company c ON fi.company_id = c.company_id
        WHERE ic.normalized_name = 'revenue'
        AND p.period_type = 'duration'
        {where}
        ORDER BY c.name, p.start_date
    """
    params = {"company": company_filter} if company_filter else {}
    df = pd.read_sql(text(query), engine, params=params)
    if df.empty:
        return pd.DataFrame()
    df["year"] = pd.to_datetime(df["start_date"]).dt.year
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
    df = df.groupby(["company", "year"])["revenue"].first().reset_index()
    df["revenue_growth"] = df.groupby("company")["revenue"].pct_change() * 100
    return df[["company", "year", "revenue_growth"]]


def add_revenue_flags(flags: pd.DataFrame, rev_growth: pd.DataFrame) -> pd.DataFrame:
    """Add revenue growth flags to the flags table."""
    rows = []
    for _, row in rev_growth.iterrows():
        growth = row["revenue_growth"]
        if pd.isna(growth):
            continue
        company, year = row["company"], int(row["year"])
        if growth > 20:
            rows.append({
                "company": company, "year": year,
                "flag_id": "REVENUE_ACCELERATION",
                "label": FLAGS["REVENUE_ACCELERATION"]["label"],
                "severity": FLAGS["REVENUE_ACCELERATION"]["severity"],
                "value": round(growth, 1),
                "detail": f"Revenue growth: +{growth:.1f}% YoY",
                "what_to_check": FLAGS["REVENUE_ACCELERATION"]["what_to_check"],
            })
        elif growth < -10:
            rows.append({
                "company": company, "year": year,
                "flag_id": "REVENUE_DECELERATION",
                "label": FLAGS["REVENUE_DECELERATION"]["label"],
                "severity": FLAGS["REVENUE_DECELERATION"]["severity"],
                "value": round(growth, 1),
                "detail": f"Revenue growth: {growth:.1f}% YoY",
                "what_to_check": FLAGS["REVENUE_DECELERATION"]["what_to_check"],
            })
    if rows:
        return pd.concat([flags, pd.DataFrame(rows)], ignore_index=True)
    return flags


# ---------------------------------------------------------------- persistence

def ensure_forensics_table(engine):
    ddl = FORENSICS_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _company_id_map(conn) -> dict:
    """company name -> company_id. forensics_flag was born with a real
    company_id FK (see sql/schema_forensics.sql) - no legacy TEXT column
    to carry forward here, unlike the five WP1-migrated tables."""
    rows = conn.execute(text("SELECT company_id, name FROM company")).fetchall()
    return {name: cid for cid, name in rows}


def save_to_db(engine, flags: pd.DataFrame) -> int:
    """Delete-then-insert, scoped to the companies present in `flags` -
    see this module's docstring for why an upsert alone isn't enough (a
    flag that stops triggering needs to actually disappear, not just
    never get updated)."""
    if flags.empty:
        return 0

    with engine.begin() as conn:
        company_ids = _company_id_map(conn)

        resolved_ids = []
        for company in flags["company"].unique():
            company_id = company_ids.get(company)
            if company_id is None:
                print(f"  *** no company_id found for '{company}' - its flags were not saved (run the loader first)")
                continue
            resolved_ids.append(company_id)

        if not resolved_ids:
            return 0

        conn.execute(
            text("DELETE FROM forensics_flag WHERE company_id = ANY(:ids)"),
            {"ids": resolved_ids},
        )

        rows_written = 0
        for _, r in flags.iterrows():
            company_id = company_ids.get(r["company"])
            if company_id is None:
                continue  # already warned above
            conn.execute(text("""
                INSERT INTO forensics_flag
                    (company_id, year, flag_id, label, severity, value, detail, what_to_check, computed_at)
                VALUES
                    (:company_id, :year, :flag_id, :label, :severity, :value, :detail, :what_to_check, now())
            """), {
                "company_id": company_id, "year": int(r["year"]), "flag_id": r["flag_id"],
                "label": r["label"], "severity": r["severity"],
                "value": float(r["value"]) if pd.notna(r.get("value")) else None,
                "detail": r["detail"], "what_to_check": r["what_to_check"],
            })
            rows_written += 1

    return rows_written


# ---------------------------------------------------------------- output

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def print_summary(flags: pd.DataFrame, min_severity: str = "low"):
    """Print a clean summary table to terminal."""
    if flags.empty:
        print("No flags triggered.")
        return

    cutoff = SEVERITY_ORDER.get(min_severity, 2)
    visible = flags[flags["severity"].map(SEVERITY_ORDER) <= cutoff].copy()
    visible = visible.sort_values(
        ["severity", "company", "year"],
        key=lambda col: col.map(SEVERITY_ORDER) if col.name == "severity" else col
    )

    print(f"\n{'='*75}")
    print(f"FINANCIAL FORENSICS — {len(visible)} flags "
          f"({'all' if min_severity == 'low' else f'{min_severity}+'} severity)")
    print(f"{'='*75}")

    sev_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    current_sev = None
    for _, row in visible.iterrows():
        if row["severity"] != current_sev:
            current_sev = row["severity"]
            print(f"\n  {sev_emoji.get(current_sev, '')} {current_sev.upper()}")
            print(f"  {'─'*70}")
        val_str = f"  [{row['value']}]" if pd.notna(row.get('value')) and row.get('value') is not None else ""
        print(f"  {row['company']:20s} {int(row['year'])}  {row['label']}{val_str}")
        print(f"         {row['detail']}")


def save_excel(flags: pd.DataFrame, out_path: str):
    """Save detailed flags to Excel."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        # summary sheet
        summary = flags.sort_values(
            ["severity", "company", "year"],
            key=lambda col: col.map(SEVERITY_ORDER) if col.name == "severity" else col
        )[["severity", "company", "year", "label", "detail", "what_to_check", "value"]]
        summary.to_excel(writer, sheet_name="All Flags", index=False)

        # one sheet per severity
        for sev in ["high", "medium", "low"]:
            subset = flags[flags["severity"] == sev]
            if not subset.empty:
                subset.to_excel(writer, sheet_name=sev.capitalize(), index=False)

    print(f"\nSaved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only analyse this company")
    ap.add_argument("--min-severity", choices=["high", "medium", "low"], default="low",
                    help="Minimum severity to display (default: low = show all)")
    ap.add_argument("--out", default="data/raw/forensics_report.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found.")
        sys.exit(1)

    engine = create_engine(db_url)
    ensure_forensics_table(engine)

    print("Fetching ratios from database...")
    ratio_df = fetch_ratios(engine, company_filter=args.company)
    if ratio_df.empty:
        print("No ratio data found. Run 11_ratio_engine.py first.")
        sys.exit(1)

    wide = pivot_ratios(ratio_df)
    print(f"Analysing {wide['company'].nunique()} companies, "
          f"{wide['year'].nunique()} years...")

    # compute flags from ratios
    off_calendar_fye = fetch_off_calendar_fye(engine, company_filter=args.company)
    flags = compute_flags(wide, off_calendar_fye)

    # add revenue growth flags from raw facts
    rev_growth = fetch_revenue_growth(engine, company_filter=args.company)
    if not rev_growth.empty:
        flags = add_revenue_flags(flags, rev_growth)

    # output
    print_summary(flags, min_severity=args.min_severity)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    save_excel(flags, args.out)

    n_saved = save_to_db(engine, flags)
    print(f"\nSaved {n_saved} flags to forensics_flag table.")

    # summary counts
    print(f"\n{'─'*40}")
    counts = flags["severity"].value_counts()
    for sev in ["high", "medium", "low"]:
        n = counts.get(sev, 0)
        print(f"  {sev.capitalize():8s}: {n} flags")
    print(f"  {'Total':8s}: {len(flags)} flags across "
          f"{flags['company'].nunique()} companies")
