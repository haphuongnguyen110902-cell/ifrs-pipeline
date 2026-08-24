"""
Check what's mapped vs unmapped for a specific company.

Shows exactly which concepts from the filing are:
  - Loaded into the database (mapped, non-dimensional)
  - Skipped because unmapped
  - Skipped because dimensional (segment breakdowns)

Usage:
    python scripts/_check_coverage.py --company "L'Oreal" --zip data/raw/loreal_2025.zip
    python scripts/_check_coverage.py --company "Amplifon" --zip data/raw/Amplifon_2025.zip
"""
import argparse
import ast
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def load_mapping(path):
    with open(path, encoding="utf-8") as f:
        mapping = yaml.safe_load(f)
    tags = {}
    for stmt, concepts in mapping.items():
        for name, info in concepts.items():
            for tag in info["xbrl_tags"]:
                tags[tag] = (name, stmt, info["display_label"])
    return tags


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True, help="Company name as in the database")
    ap.add_argument("--zip", required=True, help="Path to the filing zip")
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--show-unmapped", action="store_true",
                    help="Print full list of unmapped tags (default: summary only)")
    args = ap.parse_args()

    if not Path(args.zip).exists():
        print(f"Zip not found: {args.zip}")
        sys.exit(1)

    # load the facts CSV if it exists (faster than re-parsing)
    csv_path = Path(args.zip).parent / (Path(args.zip).stem + "_facts.csv")
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        print(f"Using cached facts CSV: {csv_path.name}")
    else:
        print(f"No cached CSV found at {csv_path}")
        print("Run scripts/01_explore_filing.py first to generate the facts CSV.")
        sys.exit(1)

    tag_lookup = load_mapping(args.mapping)

    # categorise every fact
    total = len(df)
    loaded = []
    unmapped = []
    dimensional = []

    for _, row in df.iterrows():
        tag = row["concept_qname"]
        dims = row.get("dimensions", "[]")
        try:
            dims_parsed = ast.literal_eval(dims) if isinstance(dims, str) else dims
        except (ValueError, SyntaxError):
            dims_parsed = []

        if dims_parsed:
            dimensional.append(tag)
        elif tag in tag_lookup:
            loaded.append(tag)
        else:
            unmapped.append(tag)

    print(f"\n{'='*60}")
    print(f"Coverage report: {args.company}")
    print(f"{'='*60}")
    print(f"Total facts in filing:     {total:5d}")
    print(f"Loaded (mapped):           {len(loaded):5d}  ({100*len(loaded)/total:.0f}%)")
    print(f"Skipped - dimensional:     {len(dimensional):5d}  ({100*len(dimensional)/total:.0f}%)  <- correct, V3")
    print(f"Skipped - unmapped:        {len(unmapped):5d}  ({100*len(unmapped)/total:.0f}%)  <- gap to close")

    # unique unmapped tags (not per-fact)
    unique_unmapped = sorted(set(unmapped))
    print(f"\nUnique unmapped concepts:  {len(unique_unmapped)}")

    # split by standard vs extension
    std = [t for t in unique_unmapped if t.startswith("ifrs-full:")]
    ext = [t for t in unique_unmapped if not t.startswith("ifrs-full:")]
    print(f"  Standard ifrs-full: tags: {len(std)}  (auto-classifiable by 12_prep_company.py)")
    print(f"  Extension tags:           {len(ext)}  (need human review)")

    # check what's in the DB
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        e = create_engine(db_url)
        with e.connect() as conn:
            result = conn.execute(text("""
                SELECT fi.source_file, COUNT(*) as facts
                FROM fact_value fv
                JOIN filing fi ON fv.filing_id = fi.filing_id
                JOIN company c ON fi.company_id = c.company_id
                WHERE c.name = :name
                GROUP BY fi.source_file
                ORDER BY fi.source_file
            """), {"name": args.company}).fetchall()

        db_total = sum(r[1] for r in result)
        print(f"\nFacts in database: {db_total}")
        for r in result:
            print(f"  {r[0]}: {r[1]} facts")

        expected = len(loaded)
        # DB count can be LESS than loaded count when multiple filing contexts
        # map to the same (filing_id, period_id, concept_id) - the ON CONFLICT
        # DO UPDATE keeps one row with the largest value (the total, not a component).
        # This is correct behavior - not a gap to close.
        if db_total < expected:
            diff = expected - db_total
            print(f"\n  Note: {diff} facts merged by upsert (multiple contexts -> one row)")
            print(f"  This is correct: totals win over components via ON CONFLICT DO UPDATE")
        elif db_total > expected:
            print(f"\n*** {db_total - expected} extra facts in DB vs mapping")
            print("*** May have multiple filings loaded - run --reset-facts")
        else:
            print(f"\n✓ DB count matches mapping exactly ({db_total} facts)")

    if args.show_unmapped or unique_unmapped:
        print(f"\n--- Standard tags to auto-classify ({len(std)}) ---")
        for t in std[:20]:
            print(f"  {t}")
        if len(std) > 20:
            print(f"  ... and {len(std)-20} more")

        print(f"\n--- Extension tags needing review ({len(ext)}) ---")
        for t in ext[:20]:
            print(f"  {t}")
        if len(ext) > 20:
            print(f"  ... and {len(ext)-20} more")

        print(f"\nTo fix: python scripts/12_prep_company.py --zip {args.zip}")
