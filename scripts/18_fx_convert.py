"""
scripts/18_fx_convert.py

WHAT
----
Fetches EUR-based foreign exchange reference rates from the European
Central Bank (ECB) and computes, per currency and per year, the TWO rates
IAS 21 (The Effects of Changes in Foreign Exchange Rates) actually
requires for consolidating a non-EUR subsidiary or comparing companies
that report in different currencies - the "current rate method":

  avg_rate      Average of daily rates across the calendar year.
                Use this to convert P&L / flow items: Revenue, EBIT,
                Net Profit, CFO. These accumulate transactions spread
                across the whole year, so the average is the standard
                approximation - there is no single "correct" day rate
                for a number that's really 365 different days' worth
                of transactions.
  closing_rate  Rate on the last available trading day of the year.
                Use this to convert balance-sheet / stock items: Assets,
                Net Debt, Equity. These are a snapshot at one instant
                (31 Dec), so the exact closing rate is not an
                approximation - it's the technically correct rate.

Both are stored PERMANENTLY per (currency, year) - never recomputed
against "today's" rate. A 2023 revenue figure converted to EUR must stay
the same number forever; if it changed every time someone reloaded the
page depending on today's live rate, the number would stop describing
2023 and would just describe today's currency market instead.

WHY
---
Every ratio computed so far (margins, ROIC, ROE, DSO/DIO/DPO/CCC) is
currency-neutral by construction (numerator and denominator are in the
same currency, so the currency cancels out) - which is why Essity (SEK)
and L'Oreal (EUR) could already be compared without this script. FX
conversion only becomes necessary the moment an ABSOLUTE value needs
comparing across currencies - e.g. "which company is bigger" (Revenue),
or valuation multiples once market cap is added (EV/EBITDA needs Net
Debt and EBITDA in the same currency as the market cap).

WHERE ELSE this pattern applies
--------------------------------
Same average-vs-closing split is exactly what any multinational's
consolidation team does every quarter-close - this is the "Finance
internationale" / group-accounting content, not a pipeline-specific
trick. If a country with a THIRD currency gets added later (e.g. a UK
company in GBP), nothing here changes - just add "GBP" to --currencies.

LIMITATION - stated up front, not discovered later
----------------------------------------------------
This script uses calendar-year averages/closing rates (Jan 1 - Dec 31),
which is correct for every company here EXCEPT Pernod Ricard (fiscal
year ends June 30) - same caveat already flagged by
PERNOD_FYE_WARNING in 15_forensics.py. A Pernod Ricard EUR conversion
using this script's rates would be a reasonable approximation, not
exact - fine for comps, not fine for a precise consolidation.

EXAMPLE
-------
    python scripts/18_fx_convert.py
    python scripts/18_fx_convert.py --currencies SEK USD
    python scripts/18_fx_convert.py --start-year 2017 --end-year 2025

Usage:
    python scripts/18_fx_convert.py                    # fetch + store all
    python scripts/18_fx_convert.py --currencies SEK    # just one currency
    python scripts/18_fx_convert.py --no-db             # Excel/cache only
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ECB_BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
DEFAULT_CURRENCIES = ["SEK", "USD"]  # fallback only - used if DB auto-detection fails/unavailable
FX_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_fx.sql"


def detect_currencies(engine) -> list:
    """Auto-detect every non-EUR currency actually loaded in fact_value,
    instead of relying on a hardcoded list someone has to remember to
    update every time a company from a new country is added (the exact
    kind of forgotten-registration bug already found once with
    RATIO_META in 11_ratio_engine.py). Falls back to DEFAULT_CURRENCIES
    only if this query fails for some reason.

    fact_value.currency also stores non-currency XBRL units - EPS facts
    use compound units like 'EUR / shares', share-count facts use
    'shares', dimensionless ratios use 'pure' - found by actually running
    this against the real DB, where a naive DISTINCT returned all of
    these alongside SEK/USD. Only real ISO 4217 codes (exactly 3 uppercase
    letters) are kept; anything else is silently dropped rather than sent
    to the ECB API, which would just 404 on it anyway.
    """
    try:
        df = pd.read_sql(text(
            "SELECT DISTINCT currency FROM fact_value "
            "WHERE currency IS NOT NULL AND currency != 'EUR'"
        ), engine)
        detected = sorted(c for c in df["currency"].dropna().unique().tolist()
                           if c != "EUR" and re.fullmatch(r"[A-Z]{3}", c))
        return detected if detected else DEFAULT_CURRENCIES
    except Exception as e:
        print(f"Could not auto-detect currencies from DB ({e}), "
              f"falling back to {DEFAULT_CURRENCIES}")
        return DEFAULT_CURRENCIES


# ---------------------------------------------------------------- fetch

def fetch_daily_rates(currency: str, start_year: int, end_year: int) -> pd.DataFrame:
    """Fetch daily EUR-based reference rates from the ECB's Statistical Data
    Warehouse (SDMX REST API, free, no key required).

    Series key D.{CCY}.EUR.SP00.A = daily spot reference rate, {CCY} per 1 EUR
    (e.g. D.USD.EUR.SP00.A gives USD-per-EUR: OBS_VALUE=1.08 means 1 EUR = 1.08 USD).
    So to convert an amount FROM {CCY} TO EUR: eur_value = ccy_value / OBS_VALUE.

    NOTE: this call was written against the documented ECB SDMX API format
    but could not be executed from the sandbox this was built in (the ECB
    domain isn't in that sandbox's allowed network list) - test this
    function for real on your machine before trusting its output blindly.
    """
    url = (f"{ECB_BASE}/D.{currency}.EUR.SP00.A"
           f"?startPeriod={start_year}-01-01&endPeriod={end_year}-12-31&format=csvdata")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        raise ValueError(
            f"Unexpected ECB response shape for {currency} - columns were "
            f"{list(df.columns)}. The ECB API format may have changed; "
            f"check https://data.ecb.europa.eu/help/api/data for the current spec."
        )
    df = df[["TIME_PERIOD", "OBS_VALUE"]].rename(
        columns={"TIME_PERIOD": "date", "OBS_VALUE": "rate"})
    df["date"] = pd.to_datetime(df["date"])
    df["currency"] = currency
    return df.dropna(subset=["rate"]).sort_values("date")


# ---------------------------------------------------------------- aggregate

def compute_yearly_rates(daily: pd.DataFrame) -> pd.DataFrame:
    """From daily rates, compute avg_rate (P&L conversion) and closing_rate
    (balance-sheet conversion) per calendar year - see module docstring for
    why these are different and both needed."""
    daily = daily.copy()
    daily["year"] = daily["date"].dt.year
    rows = []
    for year, g in daily.groupby("year"):
        g = g.sort_values("date")
        rows.append({
            "currency": g["currency"].iloc[0],
            "year": int(year),
            "avg_rate": float(g["rate"].mean()),
            "closing_rate": float(g["rate"].iloc[-1]),  # last available trading day <= Dec 31
            "n_observations": len(g),
        })
    return pd.DataFrame(rows).sort_values("year")


# ---------------------------------------------------------------- conversion utility (for other scripts to import)

def to_eur(value, currency: str, year: int, fx_lookup: dict, rate_type: str = "avg"):
    """Convert value FROM currency TO EUR using the stored rate for that
    year. fx_lookup is {(currency, year): {"avg_rate":..., "closing_rate":...}},
    normally built from the fx_rates table (see load_fx_lookup below).

    rate_type: "avg" for P&L/flow items (Revenue, EBIT, CFO), "closing" for
    balance-sheet/stock items (Assets, Net Debt, Equity) - see module
    docstring for why these must not be swapped.
    """
    if currency == "EUR" or pd.isna(value):
        return value
    key = (currency, int(year))
    if key not in fx_lookup:
        return float("nan")  # no rate for that year - never guess/extrapolate
    rate = fx_lookup[key]["avg_rate" if rate_type == "avg" else "closing_rate"]
    return value / rate  # ECB quotes CCY-per-EUR, so divide to get EUR


def load_fx_lookup(engine) -> dict:
    """Load the fx_rates table into a dict for to_eur() to use - the shape
    19_valuation.py (or anything else needing EUR conversion) will want."""
    df = pd.read_sql(text("SELECT currency, year, avg_rate, closing_rate FROM fx_rates"), engine)
    return {(r.currency, int(r.year)): {"avg_rate": r.avg_rate, "closing_rate": r.closing_rate}
            for r in df.itertuples()}


# ---------------------------------------------------------------- persistence

def ensure_fx_table(engine):
    ddl = FX_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, yearly: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in yearly.iterrows():
            conn.execute(text("""
                INSERT INTO fx_rates (currency, year, avg_rate, closing_rate, n_observations, source, computed_at)
                VALUES (:ccy, :yr, :avg, :close, :n, 'ECB', now())
                ON CONFLICT (currency, year)
                DO UPDATE SET avg_rate = EXCLUDED.avg_rate, closing_rate = EXCLUDED.closing_rate,
                              n_observations = EXCLUDED.n_observations, computed_at = now()
            """), {"ccy": r["currency"], "yr": int(r["year"]), "avg": r["avg_rate"],
                    "close": r["closing_rate"], "n": int(r["n_observations"])})
            rows_written += 1
    return rows_written


def save_to_excel(all_yearly: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    all_yearly.to_excel(out_path, index=False)
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--currencies", nargs="+", default=None,
                     help="Which currencies to fetch (default: auto-detect from the DB)")
    ap.add_argument("--start-year", type=int, default=2017)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/fx_rates.xlsx")
    args = ap.parse_args()

    conn = None
    if not args.no_db:
        load_dotenv()
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("DATABASE_URL not found. Check your .env file.")
            sys.exit(1)
        engine = create_engine(db_url)
        ensure_fx_table(engine)

    currencies = args.currencies
    if currencies is None:
        if args.no_db:
            currencies = DEFAULT_CURRENCIES
            print(f"--no-db set, can't auto-detect currencies - using fallback: {currencies}")
        else:
            currencies = detect_currencies(engine)
            print(f"Auto-detected currencies from DB: {currencies}")

    all_yearly = []
    for currency in currencies:
        print(f"Fetching {currency}/EUR daily rates ({args.start_year}-{args.end_year})...")
        try:
            daily = fetch_daily_rates(currency, args.start_year, args.end_year)
        except Exception as e:
            print(f"  FAILED to fetch {currency}: {e}")
            print(f"  Check your internet connection and "
                  f"https://data.ecb.europa.eu/help/api/data for API changes.")
            continue
        yearly = compute_yearly_rates(daily)
        print(yearly.to_string(index=False))
        all_yearly.append(yearly)

        if not args.no_db:
            rows = save_to_db(engine, yearly)
            print(f"  Wrote {rows} year(s) to fx_rates table\n")

    if not all_yearly:
        print("No currencies fetched successfully - nothing to save.")
        sys.exit(1)

    save_to_excel(pd.concat(all_yearly, ignore_index=True), args.out)
