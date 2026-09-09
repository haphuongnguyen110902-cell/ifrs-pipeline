"""Check exactly what Shell and Pernod Ricard have for ROIC inputs."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

query = text("""
    SELECT c.name, ic.normalized_name, p.period_type,
           p.start_date, p.end_date, fv.value::numeric
    FROM fact_value fv
    JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
    JOIN period p ON fv.period_id = p.period_id
    JOIN filing fi ON fv.filing_id = fi.filing_id
    JOIN company c ON fi.company_id = c.company_id
    WHERE c.name IN ('Shell', 'Pernod Ricard')
    AND ic.normalized_name IN (
        'longterm_borrowings',
        'noncurrent_liabilities',
        'current_borrowings_and_current_portion_of_noncurrent_borr_etc',
        'cash_and_cash_equivalents',
        'equity_attributable_to_owners_of_parent',
        'noncontrolling_interests',
        'profit_loss_from_operating_activities'
    )
    ORDER BY c.name, ic.normalized_name, p.end_date
""")

with e.connect() as conn:
    rows = conn.execute(query).fetchall()

print(f"{'Company':15s} {'Concept':50s} {'Type':8s} {'End':12s} {'Value':>20s}")
print("-" * 110)
for r in rows:
    print(f"{r[0]:15s} {r[1]:50s} {r[2]:8s} {str(r[4]):12s} {float(r[5]):>20.0f}")
