"""
Deduplication script for fact_value.

Removes duplicate rows where the same concept appears more than once
for the same company + period combination. Safe to run because all
duplicates have identical values (confirmed by _diag_duplicates.py).

Also adds a UNIQUE constraint so this cannot happen again.

Usage:
    python scripts/_fix_duplicates.py --dry-run   # show what would be deleted
    python scripts/_fix_duplicates.py             # actually delete + add constraint
"""
import argparse
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

with e.connect() as conn:

    # Step 1: count duplicates before
    count_before = conn.execute(text(
        "SELECT COUNT(*) FROM fact_value"
    )).scalar()

    dup_groups = conn.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT filing_id, period_id, concept_id
            FROM fact_value
            GROUP BY filing_id, period_id, concept_id
            HAVING COUNT(*) > 1
        ) sub
    """)).scalar()

    print(f"Before: {count_before} total rows, {dup_groups} duplicate groups")

    if args.dry_run:
        print("\nDRY RUN - no changes made.")
        print("Run without --dry-run to delete duplicates and add constraint.")
    else:
        # Step 2: delete duplicates, keeping the row with the lowest value_id
        # (arbitrary but deterministic - since all values are identical it doesn't matter)
        result = conn.execute(text("""
            DELETE FROM fact_value
            WHERE value_id NOT IN (
                SELECT MIN(value_id)
                FROM fact_value
                GROUP BY filing_id, period_id, concept_id
            )
        """))
        deleted = result.rowcount
        conn.commit()

        count_after = conn.execute(text(
            "SELECT COUNT(*) FROM fact_value"
        )).scalar()
        print(f"Deleted {deleted} duplicate rows")
        print(f"After: {count_after} total rows")

        # Step 3: add UNIQUE constraint so this can't happen again
        # Use IF NOT EXISTS equivalent - catch error if constraint already exists
        try:
            conn.execute(text("""
                ALTER TABLE fact_value
                ADD CONSTRAINT fact_value_unique_fact
                UNIQUE (filing_id, period_id, concept_id)
            """))
            conn.commit()
            print("Added UNIQUE constraint on (filing_id, period_id, concept_id)")
        except Exception as ex:
            conn.rollback()
            if "already exists" in str(ex).lower() or "duplicate" in str(ex).lower():
                print("UNIQUE constraint already exists - skipping")
            else:
                print(f"Could not add constraint: {ex}")
                print("Deduplication still succeeded - constraint can be added manually.")

        # Step 4: verify
        remaining_dups = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT filing_id, period_id, concept_id
                FROM fact_value
                GROUP BY filing_id, period_id, concept_id
                HAVING COUNT(*) > 1
            ) sub
        """)).scalar()
        print(f"\nRemaining duplicate groups: {remaining_dups}")
        if remaining_dups == 0:
            print("Clean. Duplicate rows eliminated.")
        else:
            print("*** Some duplicates remain - investigate manually.")
