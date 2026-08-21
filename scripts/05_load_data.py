"""
Week 4 script: load parsed facts into the database.

Goal: take your ifrs_concepts_v0.yaml mapping and your parsed facts CSV,
and populate the actual database tables:
  1. company        - one row for the company
  2. filing         - one row for this filing
  3. ifrs_concept   - one row per normalized concept (revenue, ebit, etc.)
  4. concept_mapping - links each raw xbrl_tag to its ifrs_concept
  5. period         - one row per distinct reporting period found
  6. fact_value     - one row per fact, linked to filing/period/concept

Usage:
    python scripts/05_load_data.py \
        --facts data/raw/loreal_2025_facts.csv \
        --mapping data/mappings/ifrs_concepts_v0.yaml \
        --company "L'Oreal" \
        --source-file data/raw/loreal_2025.zip
"""
import argparse
import ast
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
import yaml
from dotenv import load_dotenv


def load_mapping(mapping_path: str) -> dict:
    """Flatten the statement-grouped YAML into {xbrl_tag: (normalized_name, statement, display_label)}."""
    with open(mapping_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tag_lookup = {}
    for statement, concepts in data.items():
        for normalized_name, info in concepts.items():
            for xbrl_tag in info["xbrl_tags"]:
                tag_lookup[xbrl_tag] = (normalized_name, statement, info["display_label"])
    return tag_lookup


def get_or_create_company(cur, name: str) -> int:
    cur.execute("SELECT company_id FROM company WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO company (name) VALUES (%s) RETURNING company_id", (name,))
    return cur.fetchone()[0]


def get_or_create_filing(cur, company_id: int, source_file: str) -> int:
    cur.execute(
        "SELECT filing_id FROM filing WHERE company_id = %s AND source_file = %s",
        (company_id, source_file),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO filing (company_id, source_file, parsed_at) VALUES (%s, %s, now()) RETURNING filing_id",
        (company_id, source_file),
    )
    return cur.fetchone()[0]


def get_or_create_concept(cur, normalized_name: str, statement: str, display_label: str) -> int:
    cur.execute("SELECT concept_id FROM ifrs_concept WHERE normalized_name = %s", (normalized_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO ifrs_concept (normalized_name, statement, display_label) VALUES (%s, %s, %s) RETURNING concept_id",
        (normalized_name, statement, display_label),
    )
    return cur.fetchone()[0]


def get_or_create_mapping(cur, concept_id: int, xbrl_tag: str):
    cur.execute(
        "SELECT mapping_id FROM concept_mapping WHERE xbrl_tag = %s",
        (xbrl_tag,),
    )
    if cur.fetchone():
        return
    cur.execute(
        "INSERT INTO concept_mapping (concept_id, xbrl_tag) VALUES (%s, %s)",
        (concept_id, xbrl_tag),
    )


def get_or_create_period(cur, filing_id: int, start, end, period_type: str) -> int:
    cur.execute(
        "SELECT period_id FROM period WHERE filing_id = %s AND end_date = %s AND period_type = %s "
        "AND (start_date = %s OR (start_date IS NULL AND %s IS NULL))",
        (filing_id, end, period_type, start, start),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO period (filing_id, start_date, end_date, period_type) VALUES (%s, %s, %s, %s) RETURNING period_id",
        (filing_id, start, end, period_type),
    )
    return cur.fetchone()[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True, help="Path to the parsed facts CSV")
    parser.add_argument("--mapping", required=True, help="Path to the ifrs_concepts_v0.yaml mapping file")
    parser.add_argument("--company", required=True, help="Company name, e.g. \"L'Oreal\"")
    parser.add_argument("--source-file", required=True, help="Path to the original filing zip, for provenance")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)

    if not Path(args.facts).exists():
        print(f"Facts file not found: {args.facts}")
        sys.exit(1)
    if not Path(args.mapping).exists():
        print(f"Mapping file not found: {args.mapping}")
        sys.exit(1)

    tag_lookup = load_mapping(args.mapping)
    print(f"Loaded mapping for {len(tag_lookup)} XBRL tags")

    df = pd.read_csv(args.facts)
    print(f"Loaded {len(df)} facts from {args.facts}")

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                company_id = get_or_create_company(cur, args.company)
                filing_id = get_or_create_filing(cur, company_id, args.source_file)
                print(f"company_id={company_id}, filing_id={filing_id}")

                inserted = 0
                skipped_unmapped = 0
                skipped_dimensional = 0

                for _, row in df.iterrows():
                    tag = row["concept_qname"]
                    if tag not in tag_lookup:
                        skipped_unmapped += 1
                        continue

                    # skip facts that carry dimensions (segment/member breakdowns) -
                    # V0 only wants the clean top-level totals, dimensional facts
                    # need extra logic to handle correctly and are a V1 problem
                    dims = row.get("dimensions", "[]")
                    try:
                        dims_parsed = ast.literal_eval(dims) if isinstance(dims, str) else dims
                    except (ValueError, SyntaxError):
                        dims_parsed = []
                    if dims_parsed:
                        skipped_dimensional += 1
                        continue

                    normalized_name, statement, display_label = tag_lookup[tag]
                    concept_id = get_or_create_concept(cur, normalized_name, statement, display_label)
                    get_or_create_mapping(cur, concept_id, tag)

                    period_end = row.get("period_end")
                    period_start = row.get("period_start")
                    if pd.isna(period_start):
                        period_start = None
                    period_type = "instant" if period_start is None else "duration"
                    period_id = get_or_create_period(cur, filing_id, period_start, period_end, period_type)

                    unit = row.get("unit", "")
                    currency = unit.split(":")[-1] if isinstance(unit, str) and ":" in unit else unit

                    cur.execute(
                        """
                        INSERT INTO fact_value
                            (filing_id, period_id, concept_id, raw_xbrl_tag, value, currency, decimals, context_ref)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            filing_id, period_id, concept_id, tag,
                            row.get("value"), currency, row.get("decimals"), row.get("context_id"),
                        ),
                    )
                    inserted += 1

        print(f"\nInserted {inserted} fact rows")
        print(f"Skipped {skipped_unmapped} facts with no mapping (not in your 100 concepts)")
        print(f"Skipped {skipped_dimensional} facts with dimensions (segment breakdowns - V1 territory)")
    finally:
        conn.close()

    print("\nDone. Your database now has real, queryable financial data.")
