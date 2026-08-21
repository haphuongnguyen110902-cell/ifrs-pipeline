"""
Diagnostic: what income statement concepts do LVMH, Danone, and
Pernod Ricard actually have loaded? This tells us which PBT tag
to add to the ratio engine's fallback chain.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

query = text("""
    SELECT c.name, ic.normalized_name, COUNT(*) as facts,
           MAX(fv.value::numeric) as max_val
    FROM fact_value fv
    JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
    JOIN filing fi ON fv.filing_id = fi.filing_id
    JOIN company c ON fi.company_id = c.company_id
    WHERE c.name IN ('LVMH', 'Danone', 'Pernod Ricard')
    AND ic.statement = 'income_statement'
    AND ic.normalized_name NOT IN (
        'revenue', 'cost_of_sales', 'gross_profit',
        'advertising_expense', 'basic_earnings_loss_per_share',
        'diluted_earnings_loss_per_share', 'research_and_development_expense',
        'selling_general_and_administrative_expense'
    )
    GROUP BY c.name, ic.normalized_name
    ORDER BY c.name, ic.normalized_name
""")

with e.connect() as conn:
    rows = conn.execute(query).fetchall()

print(f"{'Company':15s} {'Concept':55s} {'Facts':>5s} {'Max Value':>20s}")
print("-" * 100)
for r in rows:
    print(f"{r[0]:15s} {r[1]:55s} {r[2]:>5d} {float(r[3]):>20.0f}")
