"""
scripts/_diag_shell_ebitda.py

Diagnostic: EssilorLuxottica's EBITDA reconstruction (EBIT + D&A) uses
only 3 concepts. Shell's real EV/EBITDA (verified against 4 independent
sources: GuruFocus, Multiples.vc, StockAnalysis, Investing.com) is
~4.3-5.2x, but this pipeline computed ~10.2x - roughly double. This
script checks whether Shell has additional depreciation/depletion/
amortisation-related facts in the database that the current 3-concept
list in 11_ratio_engine.py's compute_ratios() isn't picking up - which
would explain the gap (oil & gas companies often report "depletion" of
reserves as a distinct, large line item under its own XBRL concept).

Usage:
    python scripts/_diag_shell_ebitda.py
"""
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])

query = text("""
    SELECT c.normalized_name AS concept_name, p.end_date AS year_end, fv.value, fv.currency
    FROM fact_value fv
    JOIN ifrs_concept c ON fv.concept_id = c.concept_id
    JOIN period p ON fv.period_id = p.period_id
    JOIN filing f ON fv.filing_id = f.filing_id
    JOIN company co ON f.company_id = co.company_id
    WHERE co.name = 'Shell'
      AND (c.normalized_name ILIKE '%deprec%' OR c.normalized_name ILIKE '%amortis%'
           OR c.normalized_name ILIKE '%amortiz%' OR c.normalized_name ILIKE '%deplet%')
    ORDER BY p.end_date DESC, c.normalized_name
""")

df = pd.read_sql(query, engine)
if df.empty:
    print("No depreciation/amortisation/depletion facts found for Shell at all - "
          "check the concept table join or Shell's mapping coverage.")
else:
    print(f"Found {len(df)} D&A/depletion-related fact(s) for Shell:\n")
    print(df.to_string(index=False))
    print()
    currently_used = {
        "depreciation_property_plant_and_equipment",
        "depreciation_rightofuse_assets",
        "amortisation_intangible_assets_other_than_goodwill",
    }
    concepts_found = set(df["concept_name"].unique())
    missing = concepts_found - currently_used
    if missing:
        print(f"*** {len(missing)} concept(s) found for Shell that are NOT in the "
              f"current 3-concept EBITDA reconstruction:")
        for m in sorted(missing):
            print(f"      {m}")
        print("\nThese are likely why Shell's EBITDA is understated - "
              "add whichever of these apply to compute_ratios()'s D&A sum.")
    else:
        print("All D&A concepts found for Shell are already included in "
              "the current EBITDA reconstruction - the gap must be elsewhere "
              "(worth checking _net_debt or market_cap_eur next).")
