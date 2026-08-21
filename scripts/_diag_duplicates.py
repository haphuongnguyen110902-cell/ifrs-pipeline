"""
Diagnostic: find duplicate fact_value rows.

A "duplicate" here means: same company, same concept, same period,
same currency - but multiple rows. This causes aggfunc='first' in
the pivot to pick arbitrarily, potentially giving wrong ratios.

Shows:
  1. How many duplicates exist per company
  2. Which concepts are affected
  3. Whether the values differ (true duplicates vs conflicting values)
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

# Find cases where the same concept appears more than once
# for the same company + period combination
query = text("""
    SELECT
        c.name AS company,
        ic.normalized_name,
        p.period_type,
        p.start_date,
        p.end_date,
        COUNT(*) AS row_count,
        COUNT(DISTINCT fv.value::numeric) AS distinct_values,
        MIN(fv.value::numeric) AS min_val,
        MAX(fv.value::numeric) AS max_val
    FROM fact_value fv
    JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
    JOIN period p ON fv.period_id = p.period_id
    JOIN filing fi ON fv.filing_id = fi.filing_id
    JOIN company c ON fi.company_id = c.company_id
    GROUP BY c.name, ic.normalized_name, p.period_type, p.start_date, p.end_date
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC, c.name, ic.normalized_name
    LIMIT 40
""")

with e.connect() as conn:
    rows = conn.execute(query).fetchall()

if not rows:
    print("No duplicates found - fact_value is clean.")
else:
    print(f"Found {len(rows)} duplicate groups (showing up to 40):\n")
    print(f"{'Company':15s} {'Concept':45s} {'Type':8s} {'End':12s} {'Rows':>5s} {'Vals':>5s} {'Min':>15s} {'Max':>15s}")
    print("-" * 120)
    for r in rows:
        conflict = "*** CONFLICT" if r[6] > 1 else "same value"
        print(f"{r[0]:15s} {r[1]:45s} {r[2]:8s} {str(r[4]):12s} {r[5]:>5d} {r[6]:>5d} {float(r[7]):>15.0f} {float(r[8]):>15.0f}  {conflict}")

# Summary by company
print("\n--- Summary by company ---")
summary_query = text("""
    SELECT
        sub.name,
        COUNT(*) AS duplicate_groups,
        SUM(cnt - 1) AS extra_rows
    FROM (
        SELECT
            c.name,
            COUNT(*) AS cnt
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p ON fv.period_id = p.period_id
        JOIN filing fi ON fv.filing_id = fi.filing_id
        JOIN company c ON fi.company_id = c.company_id
        GROUP BY c.name, ic.normalized_name, p.period_type, p.start_date, p.end_date
        HAVING COUNT(*) > 1
    ) sub
    GROUP BY sub.name
    ORDER BY SUM(cnt - 1) DESC
""")
with e.connect() as conn:
    summary = conn.execute(summary_query).fetchall()

if summary:
    print(f"{'Company':20s} {'Duplicate groups':>18s} {'Extra rows':>12s}")
    print("-" * 55)
    for r in summary:
        print(f"{r[0]:20s} {int(r[1]):>18d} {int(r[2]):>12d}")
else:
    print("No duplicates.")
