"""Check Shell EBIT and Pernod duplicate period issue."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

# Check Shell operating profit
q1 = text("""
    SELECT c.name, ic.normalized_name, p.period_type,
           p.start_date, p.end_date, fv.value::numeric
    FROM fact_value fv
    JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
    JOIN period p ON fv.period_id = p.period_id
    JOIN filing fi ON fv.filing_id = fi.filing_id
    JOIN company c ON fi.company_id = c.company_id
    WHERE c.name = 'Shell'
    AND ic.normalized_name IN (
        'profit_loss_from_operating_activities',
        'revenue_and_other_income',
        'operating_expense'
    )
    ORDER BY ic.normalized_name, p.end_date
""")

print("=== Shell operating profit ===")
with e.connect() as conn:
    rows = conn.execute(q1).fetchall()
for r in rows:
    print(f"{r[1]:45s} {r[2]:8s} {str(r[3]):12s} {str(r[4]):12s} {float(r[5]):>20.0f}")

# Check Pernod duplicate periods
q2 = text("""
    SELECT p.period_id, p.start_date, p.end_date, p.period_type,
           COUNT(*) as fact_count
    FROM period p
    JOIN filing fi ON p.filing_id = fi.filing_id
    JOIN company c ON fi.company_id = c.company_id
    WHERE c.name = 'Pernod Ricard'
    GROUP BY p.period_id, p.start_date, p.end_date, p.period_type
    ORDER BY p.end_date
""")

print()
print("=== Pernod Ricard periods ===")
with e.connect() as conn:
    rows = conn.execute(q2).fetchall()
for r in rows:
    print(f"period_id={r[0]}  {str(r[1]):12s} -> {str(r[2]):12s}  {r[3]:8s}  facts={r[4]}")
