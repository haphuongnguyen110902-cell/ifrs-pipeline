"""
Batch parse + load every downloaded filing.

Replaces running 01_explore_filing.py and 05_load_data.py once per company
(18+ commands) with a single command. For each zip in data/raw/ that is
listed in data/companies.yaml it will:

  1. Parse the filing with Arelle (same retry logic as script 01)
  2. Save a per-company facts CSV (so you can still inspect it by hand)
  3. Report any concepts NOT covered by the mapping
  4. Load the mapped facts into the database
  5. Sanity-check the filing's actual currency against the expected one

Usage:
    python scripts/09_batch_load.py --dry-run   # parse + report, no DB writes
    python scripts/09_batch_load.py             # parse + load
    python scripts/09_batch_load.py --only essity   # just one company
    python scripts/09_batch_load.py --strict    # stop on first unmapped concept

Safe to re-run: the loader uses get_or_create for company/filing/period/
concept, so re-running does not duplicate those. NOTE: fact_value rows are
inserted unconditionally, so re-loading the SAME filing twice WILL create
duplicate fact rows. Use --reset-facts to clear a company's facts first.
"""
import argparse
import ast
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import psycopg2
import yaml
from dotenv import load_dotenv
from arelle import Cntlr, PackageManager


# ---------------------------------------------------------------- parsing

def load_filing(filepath, package_zip=None, quiet=True):
    controller = Cntlr.Cntlr(logFileName=None if quiet else "logToPrint")
    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)
    model_xbrl = controller.modelManager.load(filepath)
    return controller, model_xbrl


def dump_facts(model_xbrl) -> pd.DataFrame:
    rows = []
    for fact in model_xbrl.facts:
        if fact.concept is None or not fact.concept.isNumeric:
            continue
        ctx = fact.context
        rows.append({
            "concept_qname": str(fact.qname),
            "value": fact.value,
            "context_id": ctx.id if ctx is not None else None,
            "period_start": getattr(ctx, "startDatetime", None),
            "period_end": getattr(ctx, "endDatetime", None) or getattr(ctx, "instantDatetime", None),
            "unit": str(fact.unit.value) if fact.unit is not None else None,
            "decimals": fact.decimals,
            "dimensions": [str(d) for d in ctx.qnameDims.keys()] if ctx is not None and ctx.qnameDims else [],
        })
    return pd.DataFrame(rows)


def parse_one(zip_path: str) -> pd.DataFrame:
    """Parse with the entry-point retry that ESEF packages need."""
    controller, model_xbrl = load_filing(zip_path, package_zip=zip_path)
    df = dump_facts(model_xbrl) if model_xbrl is not None else pd.DataFrame()
    if len(df) == 0:
        controller.close()
        with zipfile.ZipFile(zip_path) as z:
            cands = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if cands:
            controller, model_xbrl = load_filing(f"{zip_path}/{cands[0]}", package_zip=zip_path)
            df = dump_facts(model_xbrl) if model_xbrl is not None else pd.DataFrame()
    controller.close()
    return df


# ---------------------------------------------------------------- mapping

def load_mapping(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    lookup = {}
    for statement, concepts in data.items():
        for normalized_name, info in concepts.items():
            for tag in info["xbrl_tags"]:
                lookup[tag] = (normalized_name, statement, info["display_label"])
    return lookup


# ---------------------------------------------------------------- database

def get_or_create_company(cur, name):
    cur.execute("SELECT company_id FROM company WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO company (name) VALUES (%s) RETURNING company_id", (name,))
    return cur.fetchone()[0]


def get_or_create_filing(cur, company_id, source_file):
    cur.execute("SELECT filing_id FROM filing WHERE company_id = %s AND source_file = %s",
                (company_id, source_file))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO filing (company_id, source_file, parsed_at) VALUES (%s, %s, now()) "
                "RETURNING filing_id", (company_id, source_file))
    return cur.fetchone()[0]


def get_or_create_concept(cur, name, statement, label):
    cur.execute("SELECT concept_id FROM ifrs_concept WHERE normalized_name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO ifrs_concept (normalized_name, statement, display_label) "
                "VALUES (%s, %s, %s) RETURNING concept_id", (name, statement, label))
    return cur.fetchone()[0]


def get_or_create_mapping_row(cur, concept_id, tag):
    cur.execute("SELECT mapping_id FROM concept_mapping WHERE xbrl_tag = %s", (tag,))
    if cur.fetchone():
        return
    cur.execute("INSERT INTO concept_mapping (concept_id, xbrl_tag) VALUES (%s, %s)",
                (concept_id, tag))


def get_or_create_period(cur, filing_id, start, end, ptype):
    cur.execute(
        "SELECT period_id FROM period WHERE filing_id = %s AND end_date = %s AND period_type = %s "
        "AND (start_date = %s OR (start_date IS NULL AND %s IS NULL))",
        (filing_id, end, ptype, start, start))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO period (filing_id, start_date, end_date, period_type) "
                "VALUES (%s, %s, %s, %s) RETURNING period_id", (filing_id, start, end, ptype))
    return cur.fetchone()[0]


def clear_company_facts(cur, company_name):
    """Delete existing fact rows for a company so a re-load doesn't duplicate."""
    cur.execute("""
        DELETE FROM fact_value
        WHERE filing_id IN (
            SELECT f.filing_id FROM filing f
            JOIN company c ON f.company_id = c.company_id
            WHERE c.name = %s
        )
    """, (company_name,))
    return cur.rowcount


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="data/companies.yaml")
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--only", help="Process just this one zip stem, e.g. 'essity'")
    ap.add_argument("--dry-run", action="store_true", help="Parse and report, write nothing to the DB")
    ap.add_argument("--strict", action="store_true", help="Stop if a company has unmapped concepts")
    ap.add_argument("--reset-facts", action="store_true",
                    help="Delete a company's existing facts before loading (avoids duplicates on re-run)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)["companies"]

    tag_lookup = load_mapping(args.mapping)
    print(f"Mapping covers {len(tag_lookup)} XBRL tags\n")

    conn = None
    if not args.dry_run:
        load_dotenv()
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("DATABASE_URL not found. Check your .env file.")
            sys.exit(1)
        conn = psycopg2.connect(db_url)

    raw_dir = Path(args.raw_dir)
    summary = []
    all_unmapped = {}

    stems = [args.only] if args.only else list(config.keys())

    for stem in stems:
        if stem not in config:
            print(f"'{stem}' is not in {args.config} - skipping")
            continue
        zip_path = raw_dir / f"{stem}.zip"
        if not zip_path.exists():
            print(f"{stem}: zip not found at {zip_path} - skipping")
            summary.append((config[stem]["name"], "zip missing", 0, 0, "-"))
            continue

        company = config[stem]["name"]
        expected_cur = config[stem].get("expected_currency")
        print(f"{'=' * 60}\n{company}  ({stem}.zip)\n{'=' * 60}")

        try:
            df = parse_one(str(zip_path))
        except Exception as e:
            print(f"  PARSE FAILED: {e}")
            summary.append((company, "parse failed", 0, 0, "-"))
            continue

        if df.empty:
            print("  0 facts extracted")
            summary.append((company, "0 facts", 0, 0, "-"))
            continue

        # save the per-company facts CSV for manual inspection
        csv_path = raw_dir / f"{stem}_facts.csv"
        df.to_csv(csv_path, index=False)
        print(f"  Parsed {len(df)} facts -> {csv_path.name}")

        # currency sanity check
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

        # which concepts aren't covered by the mapping?
        # Use all unique non-dimensional concepts (same logic as
        # 04_batch_scan_concepts.py) rather than the old 'exactly 3
        # occurrences' filter, which silently missed concepts appearing
        # 1, 2, or 4+ times in a filing.
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
            all_unmapped[company] = unmapped
            if args.strict:
                print("\n--strict is set: stopping so these can be classified first.")
                if conn:
                    conn.close()
                sys.exit(1)

        if args.dry_run:
            mappable = sum(1 for _, r in df.iterrows() if r["concept_qname"] in tag_lookup)
            print(f"  DRY RUN - would load ~{mappable} facts")
            summary.append((company, "dry run", len(df), mappable, cur_str))
            continue

        # ---- load into the database
        inserted = skipped_unmapped = skipped_dim = 0
        with conn:
            with conn.cursor() as cur:
                company_id = get_or_create_company(cur, company)
                filing_id = get_or_create_filing(cur, company_id, str(zip_path))

                if args.reset_facts:
                    deleted = clear_company_facts(cur, company)
                    if deleted:
                        print(f"  Cleared {deleted} existing fact rows for re-load")

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
                    concept_id = get_or_create_concept(cur, name, statement, label)
                    get_or_create_mapping_row(cur, concept_id, tag)

                    p_start = row.get("period_start")
                    if pd.isna(p_start):
                        p_start = None
                    ptype = "instant" if p_start is None else "duration"
                    period_id = get_or_create_period(cur, filing_id, p_start, row.get("period_end"), ptype)

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
        summary.append((company, "OK", len(df), inserted, cur_str))

    if conn:
        conn.close()

    print(f"\n\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
    print(f"{'Company':22s} {'Status':12s} {'Parsed':>8s} {'Loaded':>8s}  Currency")
    for company, status, parsed, loaded, cur_str in summary:
        print(f"{company:22s} {status:12s} {parsed:8d} {loaded:8d}  {cur_str}")

    if all_unmapped:
        total = len(set(t for ts in all_unmapped.values() for t in ts))
        print(f"\n*** {total} distinct unmapped concepts across "
              f"{len(all_unmapped)} companies - these facts did NOT load.")
        print("*** Run 04_batch_scan_concepts.py to pool them for classification.")

    currencies_seen = sorted(set(c for _, _, _, _, cs in summary for c in cs.split("/") if c not in ("-", "unknown")))
    if len(currencies_seen) > 1:
        print(f"\n*** Multiple currencies loaded: {currencies_seen}")
        print("*** Absolute values are NOT comparable across companies until converted.")
        print("*** Ratios (margins, ROIC) remain valid - currency cancels out.")
