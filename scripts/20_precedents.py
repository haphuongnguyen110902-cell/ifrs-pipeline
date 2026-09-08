"""
scripts/20_precedents.py

WHAT
----
Precedent transactions - the third leg of the DCF / trading comps /
precedent transactions "valuation triangle" every real valuation
presentation includes (see ROADMAP.md Phase 4). Curated, real,
publicly-announced M&A deals in the same sectors as this project's
universe, with implied multiples computed from DISCLOSED figures and
compared against the current trading comps range from 19_valuation.py.

WHY curated and static, not scraped/automated
-----------------------------------------------
There is no free, comprehensive M&A database (Refinitiv/Bloomberg/
Mergermarket are paid) - hand-curating a small number of well-sourced,
verifiable deals is the correct professional approach when a paid
database isn't available, not a shortcut to apologize for. Every deal
below is backed by cited public sources with the actual disclosed EV
and revenue/EBITDA figures - nothing here is estimated or invented to
pad the list. Two well-verified deals is more honest than five
uncertain ones.

DEALS (sources in each entry's "source" field):
  1. L'Oréal / Aesop (announced April 2023) - EV/Sales disclosed
     directly (Morningstar); EV/EBITDA is a Bloomberg Intelligence
     ESTIMATE, not company-disclosed - flagged as such, not presented
     with false precision.
  2. EssilorLuxottica / GrandVision (completed July 2021) - EV/Sales
     computed from disclosed EV (~EUR7.2bn, CNBC/EU antitrust filing)
     and disclosed revenue (~EUR3.7bn, MergerSight).

Adding a new deal later: append one dict to PRECEDENT_DEALS below, with
a real source. Do not estimate/invent figures to fill the list.

EXAMPLE
-------
    python scripts/20_precedents.py

Usage:
    python scripts/20_precedents.py
    python scripts/20_precedents.py --no-db
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
_val_spec = importlib.util.spec_from_file_location("valuation_19", _THIS_DIR / "19_valuation.py")
val19 = importlib.util.module_from_spec(_val_spec)
_val_spec.loader.exec_module(val19)

PRECEDENT_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_precedents.sql"

# Every figure here is a DISCLOSED number from a cited public source -
# never an estimate presented as fact. Where only an analyst estimate
# exists (not company-disclosed), it's marked is_estimate=True and the
# print/Excel output visibly flags it.
PRECEDENT_DEALS = [
    {
        "deal": "L'Oreal / Aesop",
        "announced": "2023-04-03",
        "sector": "Consumer / Beauty",
        "acquirer": "L'Oreal",
        "target": "Aesop",
        "ev_eur_m": 2300.0,  # $2.53bn at ~1.10 EUR/USD (announcement-period rate)
        "revenue_eur_m": 488.0,  # $537M 2022 revenue at ~1.10 EUR/USD
        "ebitda_eur_m": None,  # not company-disclosed
        "ev_sales": 4.7,  # DISCLOSED directly by Morningstar ("4.7 times 2022 sales")
        "ev_ebitda": 20.0,  # Bloomberg Intelligence ESTIMATE, not disclosed
        "ev_ebitda_is_estimate": True,
        "source": "Morningstar (EV/Sales), Bloomberg Intelligence via Luxuriousmagazine.com (EV/EBITDA estimate), BeautyMatter (deal terms)",
    },
    {
        "deal": "EssilorLuxottica / GrandVision",
        "announced": "2019-07-31",  # agreement date; deal completed 2021-07-01
        "sector": "Consumer / Eyewear",
        "acquirer": "EssilorLuxottica",
        "target": "GrandVision",
        "ev_eur_m": 7200.0,  # CNBC: EUR7.2bn / $8.5bn EU antitrust filing
        "revenue_eur_m": 3700.0,  # MergerSight: "EUR 3.7bn in total annual revenues"
        "ebitda_eur_m": None,  # not cleanly disclosed for GrandVision standalone
        "ev_sales": None,  # computed below from disclosed EV/revenue, not separately disclosed
        "ev_ebitda": None,
        "ev_ebitda_is_estimate": False,
        "source": "CNBC (EU antitrust approval, deal value), MergerSight (revenue, peer multiples context)",
    },
]


def build_precedents_table() -> pd.DataFrame:
    rows = []
    for d in PRECEDENT_DEALS:
        row = dict(d)
        # compute ev_sales from disclosed EV/revenue if not already given directly
        if row["ev_sales"] is None and row["revenue_eur_m"]:
            row["ev_sales"] = row["ev_eur_m"] / row["revenue_eur_m"]
        if row["ev_ebitda"] is None and row["ebitda_eur_m"]:
            row["ev_ebitda"] = row["ev_eur_m"] / row["ebitda_eur_m"]
        rows.append(row)
    return pd.DataFrame(rows)


def compare_to_trading_comps(precedents: pd.DataFrame, comps: pd.DataFrame) -> pd.DataFrame:
    """For each precedent deal's sector, show the current trading comps
    median EV/Sales alongside the precedent's own multiple - precedent
    deals typically trade at a PREMIUM to trading comps (control premium
    for acquiring 100% vs. buying a minority stake on the public market),
    so seeing them side by side, not the precedent multiple alone, is
    the actual analytical point."""
    precedents = precedents.copy()
    trading_median = []
    for _, row in precedents.iterrows():
        sector_comps = comps[comps["sector"] == row["sector"]] if "sector" in comps.columns else pd.DataFrame()
        if sector_comps.empty or "ev_sales" not in sector_comps.columns:
            trading_median.append(None)
        else:
            trading_median.append(sector_comps["ev_sales"].median())
    precedents["current_trading_comps_ev_sales_median"] = trading_median
    precedents["implied_control_premium_pct"] = [
        (p / t - 1) * 100 if pd.notna(p) and pd.notna(t) and t else None
        for p, t in zip(precedents["ev_sales"], trading_median)
    ]
    return precedents


# ---------------------------------------------------------------- persistence

def ensure_precedents_table(engine):
    ddl = PRECEDENT_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_to_db(engine, precedents: pd.DataFrame) -> int:
    rows_written = 0
    with engine.begin() as conn:
        for _, r in precedents.iterrows():
            conn.execute(text("""
                INSERT INTO precedent_transaction
                    (deal, announced, sector, acquirer, target, ev_eur_m, revenue_eur_m,
                     ebitda_eur_m, ev_sales, ev_ebitda, ev_ebitda_is_estimate,
                     current_trading_comps_ev_sales_median, implied_control_premium_pct,
                     source, computed_at)
                VALUES
                    (:deal, :announced, :sector, :acquirer, :target, :ev, :rev, :ebitda,
                     :evsales, :evebitda, :isest, :tradmed, :premium, :source, now())
                ON CONFLICT (deal, announced)
                DO UPDATE SET current_trading_comps_ev_sales_median = EXCLUDED.current_trading_comps_ev_sales_median,
                              implied_control_premium_pct = EXCLUDED.implied_control_premium_pct,
                              computed_at = now()
            """), {
                "deal": r["deal"], "announced": r["announced"], "sector": r["sector"],
                "acquirer": r["acquirer"], "target": r["target"], "ev": r["ev_eur_m"],
                "rev": r["revenue_eur_m"], "ebitda": r["ebitda_eur_m"], "evsales": r["ev_sales"],
                "evebitda": r["ev_ebitda"], "isest": bool(r["ev_ebitda_is_estimate"]),
                "tradmed": r.get("current_trading_comps_ev_sales_median"),
                "premium": r.get("implied_control_premium_pct"), "source": r["source"],
            })
            rows_written += 1
    return rows_written


def save_to_excel(precedents: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    precedents.to_excel(out_path, index=False)
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--out", default="data/raw/precedent_transactions.xlsx")
    args = ap.parse_args()

    precedents = build_precedents_table()

    comps = pd.DataFrame()
    engine = None
    if not args.no_db:
        load_dotenv()
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            engine = create_engine(db_url)
            try:
                comps = pd.read_sql(text(
                    "SELECT company, sector, ev_sales FROM valuation "
                    "WHERE ev_sales IS NOT NULL"
                ), engine)
            except Exception as e:
                print(f"Could not load trading comps for comparison ({e}) - "
                      f"showing precedent multiples alone. Run 19_valuation.py first.")

    precedents = compare_to_trading_comps(precedents, comps)

    print(f"{'Deal':38s} {'Announced':>11s} {'EV (EURm)':>10s} {'EV/Sales':>9s} "
          f"{'EV/EBITDA':>10s} {'Trading Med.':>12s} {'Premium':>9s}")
    for _, r in precedents.iterrows():
        evsales_str = f"{r['ev_sales']:.1f}x" if pd.notna(r.get("ev_sales")) else "n/a"
        evebitda_str = f"{r['ev_ebitda']:.1f}x*" if r.get("ev_ebitda_is_estimate") and pd.notna(r.get("ev_ebitda")) \
            else (f"{r['ev_ebitda']:.1f}x" if pd.notna(r.get("ev_ebitda")) else "n/a")
        tradmed_str = (f"{r['current_trading_comps_ev_sales_median']:.1f}x"
                       if pd.notna(r.get("current_trading_comps_ev_sales_median")) else "n/a")
        prem_str = (f"{r['implied_control_premium_pct']:+.0f}%"
                    if pd.notna(r.get("implied_control_premium_pct")) else "n/a")
        print(f"{r['deal']:38s} {r['announced']:>11s} {r['ev_eur_m']:>10,.0f} {evsales_str:>9s} "
              f"{evebitda_str:>10s} {tradmed_str:>12s} {prem_str:>9s}")
    print("\n* = analyst estimate, not company-disclosed")
    print("\nSources:")
    for _, r in precedents.iterrows():
        print(f"  {r['deal']}: {r['source']}")

    if engine is not None:
        ensure_precedents_table(engine)
        rows = save_to_db(engine, precedents)
        print(f"\nWrote {rows} precedent deal(s) to database")

    save_to_excel(precedents, args.out)
