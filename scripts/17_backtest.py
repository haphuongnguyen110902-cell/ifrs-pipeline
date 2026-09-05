"""
scripts/17_backtest.py

WHAT
----
Rolling-origin backtest that answers the question 16_forecasting.py could
not: for a given company and ratio, which method - CAGR or linear
regression - actually predicts better? For each ratio's year sequence, it
repeatedly trains on years[:t], forecasts the NEXT actual year, and scores
the miss. Repeating this for every possible cutoff (not just the very
last year) is what "rolling" means - one held-out year would be a single
anecdote, several rolling folds start to look like evidence.

Metrics per (company, ratio, method):
  MAE    Mean Absolute Error, in the ratio's own units (percentage points).
         Easiest to read: "on average, off by X pp".
  RMSE   Root Mean Squared Error - same units as MAE, but squares errors
         first, so a few big misses dominate more than in MAE. RMSE > MAE
         by a lot means the errors are inconsistent (mostly small, a few
         huge), not uniformly mediocre.
  BIAS   Mean signed error (forecast - actual), NOT absolute. Positive
         bias = method systematically over-predicts this ratio for this
         company; negative = under-predicts. MAE/RMSE alone can't tell
         you this - a method with MAE=2 could be always +2 (bias problem,
         fixable) or randomly +/-2 (noise, not fixable the same way).
  MAPE   Mean Absolute Percentage Error - error as a % of the actual
         value. Comparable ACROSS ratios with different scales, unlike
         MAE. Deliberately EXCLUDES folds where the actual value is near
         zero (see THIN_ACTUAL_THRESHOLD) - dividing by a near-zero
         actual explodes MAPE the exact same way dividing by a near-zero
         Operating Profit exploded cash_conversion in 15_forensics.py.
         Same lesson, different denominator.

WHY
---
16_forecasting.py already showed L'Oreal's roe: CAGR=5.5% vs linreg
R²=0.39 - two different forecasts with no objective way to prefer one.
R² only says how well a line fits the PAST; it says nothing about which
method would have predicted the FUTURE better. Backtesting is the only
way to actually answer that, which is exactly what the roadmap's V2
"forecasting model tournament" calls for.

WHERE ELSE this pattern applies
--------------------------------
The exact same rolling-origin structure is what you'd use later to
evaluate ARIMA/regression/Random Forest/XGBoost (roadmap V2) - none of
this script's harness changes, only the list of methods being compared
grows. Building the harness once now means plugging in a new model later
is a one-function addition, not a rewrite.

EXAMPLE
-------
    python scripts/17_backtest.py
    python scripts/17_backtest.py --company "L'Oreal"
    python scripts/17_backtest.py --min-train 4

Usage:
    python scripts/17_backtest.py
    python scripts/17_backtest.py --company "L'Oreal"
    python scripts/17_backtest.py --ratios gross_margin roic
    python scripts/17_backtest.py --min-train 4   # require >=4 training years before the first fold
    python scripts/17_backtest.py --no-db         # Excel only, no DB write
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------- reuse 16_forecasting.py
# Same reasoning as load_historical.py reusing 09_batch_load.py: the CAGR and
# linreg math must stay IDENTICAL between the forecast script and the
# backtest that judges it, or the backtest would be scoring a different
# formula than the one actually used to forecast. Filename starts with a
# digit, so load by path rather than `import`.
_THIS_DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("forecasting_16", _THIS_DIR / "16_forecasting.py")
f16 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f16)

DEFAULT_RATIOS = f16.DEFAULT_RATIOS
MIN_TRAIN_DEFAULT = f16.MIN_YEARS  # don't forecast off fewer years than 16_forecasting.py would

# Below this absolute actual value (percentage points), MAPE is excluded for
# that fold - same reasoning as THIN_MARGIN_THRESHOLD in 15_forensics.py:
# dividing an error by a denominator near zero produces a huge % that
# reflects the denominator's size, not real forecast quality.
THIN_ACTUAL_THRESHOLD = 3.0

BACKTEST_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_backtest.sql"


# ---------------------------------------------------------------- backtest core

def rolling_folds(years, values, min_train):
    """Yield (train_years, train_values, test_year, test_value) for every
    cutoff from min_train up to the last available year. Handles gaps in
    the year sequence (e.g. Kering missing 2024) by NOT assuming the test
    year is exactly train_years[-1] + 1 - the caller computes the real gap."""
    for t in range(min_train, len(years)):
        yield years[:t], values[:t], years[t], values[t]


def evaluate_method(df: pd.DataFrame, min_train: int) -> pd.DataFrame:
    """Run the rolling backtest for both methods, for every (company, ratio)
    with enough history. Returns one row per (company, ratio, method) with
    aggregate MAE/RMSE/BIAS/MAPE across all folds."""
    rows = []
    for (company, company_id, ratio_name), g in df.groupby(["company", "company_id", "ratio_name"]):
        g = g.sort_values("year")
        years = g["year"].tolist()
        values = g["value"].tolist()
        if len(years) < min_train + 1:
            continue

        for method in ("cagr", "linreg"):
            errors, pct_errors, thin_excluded, folds_used = [], [], 0, 0
            for train_years, train_values, test_year, test_value in rolling_folds(years, values, min_train):
                gap = test_year - train_years[-1]
                if gap <= 0:
                    continue  # shouldn't happen given years is sorted, but guard anyway
                if method == "cagr":
                    _, fc = f16.cagr_forecast(train_years, train_values, gap)
                else:
                    _, _, _, fc = f16.linreg_forecast(train_years, train_values, gap)
                pred = fc.get(test_year)
                if pred is None:
                    continue  # CAGR declined to forecast (non-positive base/endpoint)
                folds_used += 1
                err = pred - test_value
                errors.append(err)
                if abs(test_value) >= THIN_ACTUAL_THRESHOLD:
                    pct_errors.append(err / test_value * 100)
                else:
                    thin_excluded += 1

            if not errors:
                rows.append({
                    "company": company, "company_id": company_id, "ratio_name": ratio_name,
                    "method": method, "n_folds": 0, "mae": None, "rmse": None,
                    "bias": None, "mape": None, "n_thin_excluded": thin_excluded,
                    "note": "no valid folds (CAGR likely undefined every cutoff)",
                })
                continue

            err_arr = np.asarray(errors)
            rows.append({
                "company": company, "company_id": company_id, "ratio_name": ratio_name,
                "method": method, "n_folds": folds_used,
                "mae": float(np.mean(np.abs(err_arr))),
                "rmse": float(np.sqrt(np.mean(err_arr ** 2))),
                "bias": float(np.mean(err_arr)),
                "mape": float(np.mean(np.abs(pct_errors))) if pct_errors else None,
                "n_thin_excluded": thin_excluded,
                "note": "",
            })
    return pd.DataFrame(rows)


def confidence_label(n_folds: int) -> str:
    """How much to trust a winner call based on fold count alone. 1 fold is
    a single anecdote (Kering roe/roic in early test runs picked a winner
    off exactly 1 fold) - not evidence of which method is actually better."""
    if n_folds <= 1:
        return "low (1 fold - anecdotal, don't trust this pick)"
    if n_folds <= 3:
        return "medium"
    return "high"


def mark_winners(results: pd.DataFrame) -> pd.DataFrame:
    """Lower MAE wins, per (company, ratio) - only meaningful where BOTH
    methods produced at least one fold. Never picks a winner off a single
    fold's worth of evidence without saying so (see n_folds in the note)."""
    results = results.copy()
    results["is_winner"] = False
    results["confidence"] = ""
    for (company, ratio_name), g in results.groupby(["company", "ratio_name"]):
        valid = g[g["n_folds"] > 0]
        if len(valid) < 2:
            continue  # only one method (or neither) produced folds - no comparison to make
        winner_idx = valid["mae"].idxmin()
        results.loc[winner_idx, "is_winner"] = True
        min_folds = valid["n_folds"].min()  # weakest-covered method sets the confidence
        results.loc[valid.index, "confidence"] = confidence_label(int(min_folds))
    return results


# ---------------------------------------------------------------- persistence

def ensure_backtest_table(engine):
    ddl = BACKTEST_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, results: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in results.iterrows():
            if r["n_folds"] == 0:
                continue
            conn.execute(text("""
                INSERT INTO backtest
                    (company_id, ratio_name, method, n_folds, mae, rmse, bias, mape,
                     n_thin_excluded, is_winner, confidence, computed_at)
                VALUES
                    (:cid, :rn, :method, :nf, :mae, :rmse, :bias, :mape, :nte, :win, :conf, now())
                ON CONFLICT (company_id, ratio_name, method)
                DO UPDATE SET n_folds = EXCLUDED.n_folds, mae = EXCLUDED.mae,
                              rmse = EXCLUDED.rmse, bias = EXCLUDED.bias,
                              mape = EXCLUDED.mape, n_thin_excluded = EXCLUDED.n_thin_excluded,
                              is_winner = EXCLUDED.is_winner, confidence = EXCLUDED.confidence,
                              computed_at = now()
            """), {
                "cid": int(r["company_id"]), "rn": r["ratio_name"], "method": r["method"],
                "nf": int(r["n_folds"]), "mae": r["mae"], "rmse": r["rmse"], "bias": r["bias"],
                "mape": r["mape"], "nte": int(r["n_thin_excluded"]), "win": bool(r["is_winner"]),
                "conf": r.get("confidence") or None,
            })
            rows_written += 1
    return rows_written


def save_to_excel(results: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="Backtest", index=False)
        winners = results[results["is_winner"]]
        winners.to_excel(writer, sheet_name="Winners", index=False)
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only backtest this one company")
    ap.add_argument("--ratios", nargs="+", default=DEFAULT_RATIOS)
    ap.add_argument("--min-train", type=int, default=MIN_TRAIN_DEFAULT,
                     help="Minimum training years before the first fold (default: "
                          f"{MIN_TRAIN_DEFAULT}, matching 16_forecasting.py's MIN_YEARS)")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/backtest_output.xlsx")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    print("Fetching ratio history from database...")
    df = f16.fetch_ratio_history(engine, args.ratios, company_filter=args.company)
    if df.empty:
        print("No ratio data found. Run 11_ratio_engine.py first.")
        sys.exit(1)

    results = evaluate_method(df, args.min_train)
    if results.empty:
        print(f"No company/ratio pair had >= {args.min_train + 1} years of data - "
              f"nothing to backtest. Lower --min-train or load more historical years.")
        sys.exit(1)

    results = mark_winners(results)

    print(f"\n{'=' * 95}")
    print(f"{'Company':18s} {'Ratio':18s} {'Method':7s} {'Folds':>5s} "
          f"{'MAE':>7s} {'RMSE':>7s} {'Bias':>7s} {'MAPE':>7s}  Winner")
    print(f"{'=' * 95}")
    def unit_suffix(ratio_name):
        unit = f16.RATIO_UNITS.get(ratio_name, "%")
        return "d" if unit == "days" else ("x" if unit == "x" else "pp")

    for _, r in results.sort_values(["company", "ratio_name", "method"]).iterrows():
        if r["n_folds"] == 0:
            print(f"{r['company']:18s} {r['ratio_name']:18s} {r['method']:7s} "
                  f"{'0':>5s}  {'--- ' + r['note']}")
            continue
        u = unit_suffix(r["ratio_name"])
        mape_str = f"{r['mape']:.1f}%" if pd.notna(r.get("mape")) else "n/a"
        winner_str = f"*** WINNER ({r['confidence']})" if r["is_winner"] else ""
        print(f"{r['company']:18s} {r['ratio_name']:18s} {r['method']:7s} {int(r['n_folds']):5d} "
              f"{r['mae']:6.2f}{u:<1s} {r['rmse']:6.2f}{u:<1s} {r['bias']:6.2f}{u:<1s} {mape_str:>7s}  "
              f"{winner_str}")

    n_pairs = results.groupby(["company", "ratio_name"]).ngroups
    n_decided = results[results["is_winner"]].shape[0]
    print(f"\n{n_decided}/{n_pairs} company/ratio pairs had enough folds to pick a winner.")

    if not args.no_db:
        ensure_backtest_table(engine)
        rows = save_to_db(engine, results)
        print(f"Wrote {rows} backtest rows to database")

    save_to_excel(results, args.out)
