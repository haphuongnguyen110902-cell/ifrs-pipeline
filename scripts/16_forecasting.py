"""
scripts/16_forecasting.py

WHAT
----
For each company and each currency-neutral ratio (margins, ROIC, ROE,
cash conversion) already sitting in the `ratio` table, fits TWO simple
forecasting methods over the historical years available and projects
forward:

  1. CAGR   - compounds the growth rate implied by the first and last
              historical value forward. Standard for revenue/earnings,
              but only WELL-DEFINED when both endpoints are positive -
              a margin that crossed zero has no meaningful CAGR, so
              those cases are flagged "N/A" rather than faked.
  2. LINREG - ordinary least squares trend line (value ~ year), with R²
              reported so you can see how well a straight line actually
              fits before trusting the forecast.

Both are printed side by side deliberately: this is the first entry in
what the roadmap calls the "forecasting model tournament" (V2) - CAGR
and linreg are the simplest two contestants, ARIMA/regression/Random
Forest/XGBoost with proper rolling backtesting come later. Picking a
"winner" per company/metric only makes sense once there's a backtest to
judge them by, which this script does not yet do.

WHY
---
Ratios, not raw fact_value rows, are the right input here: margins are
already currency-neutral (comparable across EUR/SEK/USD companies) and
already reconciled to a single number per company/year by
11_ratio_engine.py, so this script doesn't need to touch fact_value or
XBRL tags at all.

WHERE ELSE this pattern applies
--------------------------------
Any "compute once, forecast many times" pipeline benefits from this
separation: the expensive/fragile step (parsing filings, resolving
extension tags) stays upstream, and this cheap step can be re-run as
often as you like without re-parsing anything.

EXAMPLE
-------
    python scripts/16_forecasting.py
    python scripts/16_forecasting.py --company "L'Oreal"
    python scripts/16_forecasting.py --horizon 3 --no-db

Usage:
    python scripts/16_forecasting.py
    python scripts/16_forecasting.py --company "L'Oreal"
    python scripts/16_forecasting.py --ratios gross_margin operating_margin
    python scripts/16_forecasting.py --horizon 3
    python scripts/16_forecasting.py --no-db   # Excel only, no DB write
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

MIN_YEARS = 3  # fewer points than this and neither method means much
DEFAULT_RATIOS = [
    "gross_margin", "operating_margin", "net_margin",
    "roic", "roe", "cash_conversion",
]

FORECAST_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_forecast.sql"


# ---------------------------------------------------------------- fetch

def fetch_ratio_history(engine, ratio_names, company_filter=None) -> pd.DataFrame:
    query = """
        SELECT c.name AS company, r.company_id, r.year, r.ratio_name, r.value
        FROM ratio r
        JOIN company c ON r.company_id = c.company_id
        WHERE r.ratio_name = ANY(:ratios)
    """
    params = {"ratios": ratio_names}
    if company_filter:
        query += " AND c.name = :company"
        params["company"] = company_filter
    query += " ORDER BY c.name, r.ratio_name, r.year"
    df = pd.read_sql(text(query), engine, params=params)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


# ---------------------------------------------------------------- forecasting

def cagr_forecast(years, values, horizon):
    """Returns (cagr or None, {forecast_year: value})."""
    first_v, last_v = values[0], values[-1]
    n_periods = years[-1] - years[0]
    if n_periods <= 0 or first_v <= 0 or last_v <= 0:
        return None, {}
    cagr = (last_v / first_v) ** (1 / n_periods) - 1
    return cagr, {years[-1] + t: last_v * (1 + cagr) ** t for t in range(1, horizon + 1)}


def linreg_forecast(years, values, horizon):
    """Returns (slope, intercept, r_squared, {forecast_year: value})."""
    years_arr = np.asarray(years, dtype=float)
    vals_arr = np.asarray(values, dtype=float)
    slope, intercept = np.polyfit(years_arr, vals_arr, 1)
    pred = slope * years_arr + intercept
    ss_res = float(np.sum((vals_arr - pred) ** 2))
    ss_tot = float(np.sum((vals_arr - vals_arr.mean()) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    last_year = years_arr[-1]
    forecasts = {int(last_year + t): float(slope * (last_year + t) + intercept)
                 for t in range(1, horizon + 1)}
    return float(slope), float(intercept), r_squared, forecasts


def build_forecasts(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    rows = []
    for (company, company_id, ratio_name), g in df.groupby(["company", "company_id", "ratio_name"]):
        g = g.sort_values("year")
        years = g["year"].tolist()
        values = g["value"].tolist()
        if len(years) < MIN_YEARS:
            rows.append({
                "company": company, "company_id": company_id, "ratio_name": ratio_name,
                "n_years": len(years), "first_year": years[0] if years else None,
                "last_year": years[-1] if years else None,
                "latest_value": values[-1] if values else None,
                "note": f"skipped - only {len(years)} year(s), need >= {MIN_YEARS}",
            })
            continue

        cagr, cagr_fc = cagr_forecast(years, values, horizon)
        slope, intercept, r2, lin_fc = linreg_forecast(years, values, horizon)

        row = {
            "company": company, "company_id": company_id, "ratio_name": ratio_name,
            "n_years": len(years), "first_year": years[0], "last_year": years[-1],
            "latest_value": values[-1],
            "cagr": cagr, "linreg_slope": slope, "linreg_r2": r2,
            "note": "" if cagr is not None else "CAGR N/A (non-positive base/endpoint)",
        }
        for i in range(1, horizon + 1):
            fy = years[-1] + i
            row[f"cagr_fc_y{i}_{fy}"] = cagr_fc.get(fy)
            row[f"linreg_fc_y{i}_{fy}"] = lin_fc.get(fy)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- persistence

def ensure_forecast_table(engine):
    ddl = FORECAST_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, forecasts: pd.DataFrame, horizon: int) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in forecasts.iterrows():
            if pd.isna(r.get("n_years")) or r["n_years"] < MIN_YEARS:
                continue
            for method, param_col in (("cagr", "cagr"), ("linreg", None)):
                for i in range(1, horizon + 1):
                    fy = int(r["last_year"]) + i
                    fc_col = f"{method}_fc_y{i}_{fy}"
                    if fc_col not in r or pd.isna(r[fc_col]):
                        continue
                    conn.execute(text("""
                        INSERT INTO forecast
                            (company_id, ratio_name, method, base_year_start, base_year_end,
                             n_years, forecast_year, forecast_value, cagr, r_squared, computed_at)
                        VALUES
                            (:cid, :rn, :method, :ys, :ye, :ny, :fy, :fv, :cagr, :r2, now())
                        ON CONFLICT (company_id, ratio_name, method, forecast_year)
                        DO UPDATE SET forecast_value = EXCLUDED.forecast_value,
                                      cagr = EXCLUDED.cagr, r_squared = EXCLUDED.r_squared,
                                      computed_at = now()
                    """), {
                        "cid": int(r["company_id"]), "rn": r["ratio_name"], "method": method,
                        "ys": int(r["first_year"]), "ye": int(r["last_year"]), "ny": int(r["n_years"]),
                        "fy": fy, "fv": float(r[fc_col]),
                        "cagr": float(r["cagr"]) if method == "cagr" and pd.notna(r.get("cagr")) else None,
                        "r2": float(r["linreg_r2"]) if method == "linreg" and pd.notna(r.get("linreg_r2")) else None,
                    })
                    rows_written += 1
    return rows_written


def save_to_excel(forecasts: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        forecasts.to_excel(writer, sheet_name="Forecasts", index=False)
        for ratio_name, g in forecasts.groupby("ratio_name"):
            g.drop(columns=["ratio_name"]).to_excel(
                writer, sheet_name=ratio_name[:31], index=False)
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only forecast this one company")
    ap.add_argument("--ratios", nargs="+", default=DEFAULT_RATIOS,
                     help="Which ratio_name values to forecast")
    ap.add_argument("--horizon", type=int, default=2, help="Years ahead to forecast")
    ap.add_argument("--no-db", action="store_true", help="Skip writing to the database, Excel only")
    ap.add_argument("--out", default="data/raw/forecast_output.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    print("Fetching ratio history from database...")
    df = fetch_ratio_history(engine, args.ratios, company_filter=args.company)
    if df.empty:
        print("No ratio data found. Run 11_ratio_engine.py first "
              "(and load_historical.py before that, for enough years).")
        sys.exit(1)

    n_years_available = df.groupby(["company", "ratio_name"])["year"].nunique().max()
    print(f"Loaded {len(df)} ratio observations across "
          f"{df['company'].nunique()} companies and {df['ratio_name'].nunique()} ratios "
          f"(up to {n_years_available} years for any single company/ratio)\n")

    forecasts = build_forecasts(df, args.horizon)

    print(f"{'=' * 90}")
    print(f"{'Company':18s} {'Ratio':18s} {'Yrs':>4s} {'Latest':>9s} {'CAGR':>8s} {'LinReg R2':>10s}  Note")
    print(f"{'=' * 90}")
    for _, r in forecasts.sort_values(["company", "ratio_name"]).iterrows():
        cagr_str = f"{r['cagr']:.1%}" if pd.notna(r.get("cagr")) else "n/a"
        r2_str = f"{r['linreg_r2']:.2f}" if pd.notna(r.get("linreg_r2")) else "n/a"
        # ratio table already stores values as percentage points (74.3 == 74.3%),
        # not as fractions (0.743) - so plain .1f + '%', NOT Python's .1% format
        # (which would multiply by 100 again and print 7430%).
        latest_str = f"{r['latest_value']:.1f}%" if pd.notna(r.get("latest_value")) else "n/a"
        print(f"{r['company']:18s} {r['ratio_name']:18s} {int(r['n_years']):4d} "
              f"{latest_str:>9s} {cagr_str:>8s} {r2_str:>10s}  {r.get('note', '')}")

    skipped = (forecasts["n_years"] < MIN_YEARS).sum()
    if skipped:
        print(f"\n{skipped} company/ratio pair(s) skipped for having fewer than "
              f"{MIN_YEARS} years of data - this is exactly why load_historical.py exists.")

    if not args.no_db:
        ensure_forecast_table(engine)
        rows = save_to_db(engine, forecasts, args.horizon)
        print(f"\nWrote {rows} forecast rows to database")

    save_to_excel(forecasts, args.out)
