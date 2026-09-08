"""
scripts/load_historical.py

WHAT
----
Loads the historical (multi-year) IFRS filings sitting in
data/raw/historical/ into the same database V1 already uses, so ratios
can be computed across 5-7 years per company instead of just the single
most-recent filing 09_batch_load.py loads.

WHY
---
09_batch_load.py assumes ONE zip == ONE company (matched by stem against
data/companies.yaml) and writes ONE filing row per company. Historical
data breaks that assumption: MANY zips == ONE company (one per year), so
this script instead:
  1. Parses the company out of the FILENAME
     (loreal_2025-12-31.zip -> "L'Oreal", fiscal_year_end 2025-12-31)
  2. Creates a SEPARATE filing row per (company, year) instead of
     reusing/overwriting the single V1 filing row
  3. Reuses every DB helper and the Arelle parsing logic from
     09_batch_load.py by importing it as a module, so a parsing fix or a
     mapping fix only ever has to happen in ONE place

WHERE ELSE this pattern applies
--------------------------------
Any time "one company = one row" stops being true (e.g. later, quarterly
filings alongside annual ones), the fix is the same: keep the natural key
at the (company, period) grain in the `filing` table, and never assume a
company maps to a single database row. This is also why `filing` has
UNIQUE(company_id, fiscal_year_end) rather than a company-level unique
key - it was already designed for this.

EXAMPLE
-------
    python scripts/load_historical.py --dry-run
    python scripts/load_historical.py
    python scripts/load_historical.py --only loreal
    python scripts/load_historical.py --reset-historical --only shell

Usage:
    python scripts/load_historical.py --dry-run
        # parse every file + report what WOULD load, write nothing

    python scripts/load_historical.py
        # parse + load everything in data/raw/historical/

    python scripts/load_historical.py --only loreal
        # just one company's historical files (key = filename prefix)

    python scripts/load_historical.py --strict
        # stop on the first file with unmapped concepts

    python scripts/load_historical.py --reset-historical
        # delete previously-loaded HISTORICAL facts for the companies
        # being processed before reloading (safe re-run, no duplicates).
        # Does NOT touch the single V1 filing loaded by 09_batch_load.py -
        # that one lives at data/raw/*.zip, this only clears rows whose
        # source_file starts with data/raw/historical/.

Filename convention expected in data/raw/historical/:
    {company_key}_{YYYY-MM-DD}.zip      e.g. loreal_2025-12-31.zip

Files that don't match this pattern, or whose prefix isn't in
COMPANY_MAP below, are reported and skipped - never silently ignored.
"""
import argparse
import ast
import importlib.util
import os
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

# ---------------------------------------------------------------- reuse 09_batch_load.py
# Its filename starts with a digit, so it can't be `import`-ed by name -
# load it by file path instead. This keeps the Arelle parsing logic and
# every DB get_or_create_* helper defined in exactly one place; fix a bug
# there and both loaders pick it up.
_THIS_DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("batch_load_09", _THIS_DIR / "09_batch_load.py")
batch09 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(batch09)


# ---------------------------------------------------------------- company map
# filename prefix (lowercased) -> (company name exactly as stored in the
# `company` table, expected reporting currency). Keep the name in sync with
# data/companies.yaml's `name` values so V1 rows and historical rows land
# on the SAME company_id - get_or_create_company matches on exact name.
COMPANY_MAP = {
    "loreal": ("L'Oreal", "EUR", "Consumer / Beauty", "France"),
    "lvmh": ("LVMH", "EUR", "Luxury Goods", "France"),
    "kering": ("Kering", "EUR", "Luxury Goods", "France"),
    "essilorluxottica": ("EssilorLuxottica", "EUR", "Consumer / Eyewear", "France"),
    "danone": ("Danone", "EUR", "Consumer Staples", "France"),
    "essity": ("Essity", "SEK", "Consumer / Hygiene", "Sweden"),
    "moncler": ("Moncler", "EUR", "Luxury Apparel", "Italy"),
    "shell": ("Shell", "USD", "Energy", "United Kingdom"),
    "amplifon": ("Amplifon", "EUR", "Consumer Health Retail", "Italy"),
    "puig": ("Puig Brands", "EUR", "Consumer / Beauty", "Spain"),
    # Pernod Ricard intentionally excluded: its June 30 fiscal year end
    # is not comparable to the December filers above without extra work
    # (see NOTES.md / roadmap "known architectural issue").
}

FILENAME_RE = re.compile(r"^([a-zA-Z]+)_(\d{4})-(\d{2})-(\d{2})\.zip$")


# ---------------------------------------------------------------- discovery

def discover_files(raw_dir: Path):
    """Match every zip in raw_dir against FILENAME_RE. Returns
    (matched, unmatched) - unmatched files are never silently dropped,
    they're reported to the user so a typo'd filename gets noticed."""
    matched, unmatched = [], []
    for zip_path in sorted(raw_dir.glob("*.zip")):
        m = FILENAME_RE.match(zip_path.name)
        if not m:
            unmatched.append(zip_path)
            continue
        prefix, y, mo, d = m.groups()
        key = prefix.lower()
        if key not in COMPANY_MAP:
            unmatched.append(zip_path)
            continue
        name, currency, sector, country = COMPANY_MAP[key]
        matched.append({
            "path": zip_path,
            "key": key,
            "company": name,
            "expected_currency": currency,
            "sector": sector,
            "country": country,
            "fiscal_year_end": f"{y}-{mo}-{d}",
        })
    return matched, unmatched


# ---------------------------------------------------------------- DB helpers specific to historical loads

def get_or_create_historical_filing(cur, company_id, source_file, fiscal_year_end):
    """Like batch09.get_or_create_filing, but ALSO records fiscal_year_end.
    batch09's version leaves it NULL because V1 only ever had one filing
    per company and didn't need to tell years apart; here telling years
    apart is the entire point."""
    cur.execute(
        "SELECT filing_id FROM filing WHERE company_id = %s AND source_file = %s",
        (company_id, source_file))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO filing (company_id, source_file, fiscal_year_end, parsed_at) "
        "VALUES (%s, %s, %s, now()) RETURNING filing_id",
        (company_id, source_file, fiscal_year_end))
    return cur.fetchone()[0]


def clear_historical_facts(cur, company_name, raw_dir_prefix):
    """Delete fact rows loaded from data/raw/historical/* for one company,
    WITHOUT touching the single V1 filing loaded by 09_batch_load.py from
    data/raw/*.zip. Matched by source_file path prefix, so re-running with
    --reset-historical can't accidentally wipe the V1 data."""
    cur.execute("""
        DELETE FROM fact_value
        WHERE filing_id IN (
            SELECT f.filing_id FROM filing f
            JOIN company c ON f.company_id = c.company_id
            WHERE c.name = %s AND f.source_file LIKE %s
        )
    """, (company_name, f"{raw_dir_prefix}%"))
    return cur.rowcount


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="data/raw/historical")
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--only", help="Only load this one company key, e.g. 'loreal'")
    ap.add_argument("--dry-run", action="store_true", help="Parse and report, write nothing to the DB")
    ap.add_argument("--strict", action="store_true", help="Stop on the first file with unmapped concepts")
    ap.add_argument("--reset-historical", action="store_true",
                     help="Delete previously-loaded historical facts for the companies "
                          "being processed before reloading (does not touch V1 data)")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        print(f"{raw_dir} does not exist.")
        sys.exit(1)

    tag_lookup = batch09.load_mapping(args.mapping)
    print(f"Mapping covers {len(tag_lookup)} XBRL tags\n")

    matched, unmatched = discover_files(raw_dir)

    if unmatched:
        print(f"*** {len(unmatched)} file(s) did not match a known company - "
              f"NOT loaded (fix the filename or add it to COMPANY_MAP):")
        for p in unmatched:
            print(f"      {p.name}")
        print()

    if args.only:
        key = args.only.lower()
        matched = [m for m in matched if m["key"] == key]
        if not matched:
            print(f"No files matched --only {args.only}")
            sys.exit(1)

    matched.sort(key=lambda m: (m["company"], m["fiscal_year_end"]))
    n_companies = len({m["company"] for m in matched})
    print(f"Found {len(matched)} historical filing(s) to process across {n_companies} companies\n")

    conn = None
    if not args.dry_run:
        load_dotenv()
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("DATABASE_URL not found. Check your .env file.")
            sys.exit(1)
        conn = psycopg2.connect(db_url)

        if args.reset_historical:
            raw_prefix = str(raw_dir) + os.sep
            with conn:
                with conn.cursor() as cur:
                    for company in sorted({m["company"] for m in matched}):
                        deleted = clear_historical_facts(cur, company, raw_prefix)
                        if deleted:
                            print(f"Cleared {deleted} existing historical fact rows for {company}")
            print()

    summary = []
    all_unmapped = {}

    for item in matched:
        zip_path = item["path"]
        company = item["company"]
        expected_cur = item["expected_currency"]
        sector = item["sector"]
        country = item["country"]
        fye = item["fiscal_year_end"]

        print(f"{'=' * 60}\n{company}  ({zip_path.name})\n{'=' * 60}")

        try:
            df = batch09.parse_one(str(zip_path))
        except Exception as e:
            print(f"  PARSE FAILED: {e}")
            summary.append((company, zip_path.name, "parse failed", 0, 0, "-"))
            continue

        if df.empty:
            print("  0 facts extracted")
            summary.append((company, zip_path.name, "0 facts", 0, 0, "-"))
            continue

        print(f"  Parsed {len(df)} facts")

        # currency sanity check (same logic as 09_batch_load.py)
        units = [u for u in df["unit"].dropna().unique() if isinstance(u, str)]
        currencies = []
        for u in units:
            if u in {"EUR", "USD", "SEK", "GBP", "CHF", "DKK", "NOK", "JPY"}:
                currencies.append(u)
            elif ":" in u:
                currencies.append(u.split(":")[-1])
        currencies = sorted(set(currencies))
        cur_str = "/".join(currencies) if currencies else "unknown"
        if expected_cur and expected_cur not in currencies:
            print(f"  *** CURRENCY MISMATCH: expected {expected_cur}, filing reports {cur_str}")
        else:
            print(f"  Currency: {cur_str}")

        # unmapped concepts (same logic as 09_batch_load.py: only look at
        # non-dimensional facts, since dimensional facts are skipped anyway)
        df_clean = df[df["dimensions"].apply(
            lambda d: len(ast.literal_eval(d)) == 0 if isinstance(d, str) else len(d) == 0)]
        clean_tags = set(df_clean["concept_qname"].unique())
        unmapped = sorted(clean_tags - set(tag_lookup))
        if unmapped:
            print(f"  *** {len(unmapped)} unmapped concepts (these will NOT load):")
            for t in unmapped[:5]:
                print(f"        {t}")
            if len(unmapped) > 5:
                print(f"        ... and {len(unmapped) - 5} more")
            all_unmapped[f"{company} ({fye})"] = unmapped
            if args.strict:
                print("\n--strict is set: stopping so these can be classified first.")
                if conn:
                    conn.close()
                sys.exit(1)

        if args.dry_run:
            mappable = sum(1 for _, r in df.iterrows() if r["concept_qname"] in tag_lookup)
            print(f"  DRY RUN - would load ~{mappable} facts")
            summary.append((company, zip_path.name, "dry run", len(df), mappable, cur_str))
            continue

        # ---- load into the database
        inserted = skipped_unmapped = skipped_dim = 0
        with conn:
            with conn.cursor() as cur:
                company_id = batch09.get_or_create_company(cur, company, sector=sector, country=country)
                filing_id = get_or_create_historical_filing(cur, company_id, str(zip_path), fye)

                for _, row in df.iterrows():
                    tag = row["concept_qname"]
                    if tag not in tag_lookup:
                        skipped_unmapped += 1
                        continue
                    dims = row.get("dimensions", "[]")
                    try:
                        parsed = ast.literal_eval(dims) if isinstance(dims, str) else dims
                    except (ValueError, SyntaxError):
                        parsed = []
                    if parsed:
                        skipped_dim += 1
                        continue

                    name, statement, label = tag_lookup[tag]
                    concept_id = batch09.get_or_create_concept(cur, name, statement, label)
                    batch09.get_or_create_mapping_row(cur, concept_id, tag)

                    p_start = row.get("period_start")
                    if pd.isna(p_start):
                        p_start = None
                    ptype = "instant" if p_start is None else "duration"
                    period_id = batch09.get_or_create_period(
                        cur, filing_id, p_start, row.get("period_end"), ptype)

                    unit = row.get("unit", "")
                    currency = unit.split(":")[-1] if isinstance(unit, str) and ":" in unit else unit

                    raw_decimals = row.get("decimals")
                    if raw_decimals == "INF":
                        decimals = None
                    else:
                        try:
                            decimals = int(raw_decimals)
                        except (ValueError, TypeError):
                            decimals = None

                    raw_value = row.get("value")
                    if raw_value is None or (isinstance(raw_value, str) and raw_value.strip() == ""):
                        continue

                    cur.execute(
                        "INSERT INTO fact_value (filing_id, period_id, concept_id, raw_xbrl_tag, "
                        "value, currency, decimals, context_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (filing_id, period_id, concept_id) DO UPDATE SET "
                        "value = CASE WHEN ABS(EXCLUDED.value::numeric) > ABS(fact_value.value::numeric) "
                        "THEN EXCLUDED.value ELSE fact_value.value END",
                        (filing_id, period_id, concept_id, tag, raw_value,
                         currency, decimals, row.get("context_id")))
                    inserted += 1
        print(f"  Loaded {inserted} facts  (skipped {skipped_unmapped} unmapped, {skipped_dim} dimensional)")
        summary.append((company, zip_path.name, "OK", len(df), inserted, cur_str))

    if conn:
        conn.close()

    print(f"\n\n{'=' * 84}\nSUMMARY\n{'=' * 84}")
    print(f"{'Company':20s} {'File':28s} {'Status':10s} {'Parsed':>7s} {'Loaded':>7s}  Currency")
    for company, fname, status, parsed, loaded, cur_str in summary:
        print(f"{company:20s} {fname:28s} {status:10s} {parsed:7d} {loaded:7d}  {cur_str}")

    if all_unmapped:
        total = len(set(t for ts in all_unmapped.values() for t in ts))
        print(f"\n*** {total} distinct unmapped concepts across "
              f"{len(all_unmapped)} filing(s) - these facts did NOT load.")
        print("*** Run 04b_batch_scan_concepts.py / 10_auto_classify.py to pool them for classification.")

    ok = sum(1 for row in summary if row[2] == "OK")
    if args.dry_run:
        would_load = sum(1 for row in summary if row[2] == "dry run")
        print(f"\nDRY RUN: {would_load}/{len(summary)} filings parsed cleanly and would load. "
              f"Nothing was written to the DB. Re-run without --dry-run to load for real.")
    else:
        print(f"\n{ok}/{len(summary)} filings loaded successfully.")
    if not args.dry_run and ok:
        print("\nNext steps:")
        print("  python scripts/08_validate.py")
        print("  python scripts/11_ratio_engine.py")
        print("\nNote: a single historical filing often already contains 2 comparative")
        print("years (current + prior). Where two different zips both cover the same")
        print("year for the same company, 11_ratio_engine.py's pivot (aggfunc='first')")
        print("keeps whichever one the SQL query happens to return first - fine for now,")
        print("but worth reconciling explicitly if the numbers for an overlap year ever look off.")
