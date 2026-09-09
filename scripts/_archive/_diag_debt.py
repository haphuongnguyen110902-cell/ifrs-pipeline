"""
Diagnostic: what balance sheet debt concepts do LVMH, Danone,
Pernod Ricard, Shell have? Needed to fix ROIC coverage.
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
    WHERE c.name IN ('LVMH', 'Danone', 'Pernod Ricard', 'Shell')
    AND ic.statement = 'balance_sheet'
    AND ic.normalized_name NOT IN (
        'goodwill', 'intangible_assets_other_than_goodwill',
        'property_plant_and_equipment', 'rightofuse_assets',
        'inventories', 'current_trade_receivables',
        'trade_and_other_current_payables', 'deferred_tax_assets',
        'deferred_tax_liabilities', 'other_current_assets',
        'other_noncurrent_assets', 'investment_accounted_for_using_equity_method'
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
