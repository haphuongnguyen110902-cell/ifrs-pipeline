"""
Week 5 script: generate the final clean statements.

Goal: query the database (not the CSV - the database is now the source
of truth) and reconstruct a clean income statement, balance sheet, and
cash flow statement, one column per year, correctly labeled.

Year labeling: Arelle stores ALL dates - both duration end_dates and
instant dates - one day ahead of the real reporting date. This is
because XBRL treats a calendar day as a whole span, so "as of Dec 31
2023" and "up through Dec 31 2023" both get normalized internally to
"before Jan 1 2024". So for BOTH duration and instant facts, the true
date is (stored_end_date - 1 day). Duration facts are labeled by
start_date's year instead (which isn't shifted and is simpler), while
instant facts have no start_date, so we correct end_date directly.

Usage:
    python scripts/07_generate_statements.py --company "L'Oreal"
"""
import argparse
import os
import sys
from datetime import timedelta

import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Explicit line-item order, matching how these actually appear in a real
# financial statement (top to bottom), instead of pandas' default
# alphabetical sort. Keyed by normalized_name (not display_label).
# Anything found in the data but NOT listed here gets appended at the
# end, alphabetically - so nothing silently disappears if your mapping
# grows to cover concepts this list doesn't yet know about.
STATEMENT_ORDER = {
    "income_statement": [
        "revenue", "revenue_and_other_income",
        "cost_of_sales", "raw_materials_and_consumables_used", "operating_expense",
        "gross_profit", "gross_profit_excl_i_a_c",
        "distribution_costs", "administrative_expense",
        "selling_general_and_administrative_expense",
        "advertising_expense", "research_and_development_expense", "marketing_expense",
        "other_operating_income_expense",
        "profit_loss_from_operating_activities",
        "operating_profit_excl_i_a_c", "resultat_dexploitation",
        "recurring_operating_income",
        "profit_loss_from_operating_activities_after_share_of_prof_etc",
        "profit_loss_from_operating_activities_recurring_including__etc",
        "finance_income", "finance_costs", "finance_income_cost",
        "interest_expense", "interest_expense_on_lease_liabilities",
        "other_finance_income_cost", "cost_of_net_debt",
        "share_of_profit_loss_of_associates_and_joint_ventures_acc_etc",
        "profit_loss_before_tax",
        "resultat_avant_impot_et_societes_mises_en_equivalence",
        "income_tax_expense_continuing_operations",
        "profit_loss",
        "profit_loss_attributable_to_owners_of_parent",
        "profit_loss_attributable_to_noncontrolling_interests",
        "comprehensive_income",
        "comprehensive_income_attributable_to_owners_of_parent",
        "comprehensive_income_attributable_to_noncontrolling_inter_etc",
        "basic_earnings_loss_per_share", "diluted_earnings_loss_per_share",
        "adjusted_weighted_average_shares", "weighted_average_shares",
    ],
    "balance_sheet": [
        "goodwill", "intangible_assets_other_than_goodwill",
        "property_plant_and_equipment", "rightofuse_assets",
        "investment_accounted_for_using_equity_method",
        "noncurrent_financial_assets_availableforsale",
        "noncurrent_investments_other_than_investments_accounted_fo_etc",
        "deferred_tax_assets",
        "noncurrent_recognised_assets_defined_benefit_plan",
        "other_noncurrent_assets", "noncurrent_assets",
        "inventories", "trade_and_other_current_receivables",
        "other_current_assets", "current_tax_assets_current",
        "cash_and_cash_equivalents",
        "noncurrent_assets_or_disposal_groups_classified_as_held_fo_etc",
        "current_assets", "assets",
        "equity_attributable_to_owners_of_parent",
        "noncontrolling_interests", "equity", "equity_and_liabilities",
        "longterm_borrowings", "noncurrent_lease_liabilities",
        "deferred_tax_liabilities",
        "noncurrent_provisions_for_employee_benefits",
        "noncurrent_recognised_liabilities_defined_benefit_plan",
        "other_noncurrent_liabilities", "noncurrent_liabilities",
        "current_borrowings_and_current_portion_of_noncurrent_borr_etc",
        "current_lease_liabilities",
        "trade_and_other_current_payables",
        "current_tax_liabilities_current",
        "other_current_liabilities", "current_liabilities",
    ],
    "cash_flow": [
        "cash_flows_from_used_in_operations_before_changes_in_work_etc",
        "increase_decrease_in_working_capital",
        "adjustments_for_depreciation_and_amortisation_expense_and_etc",
        "adjustments_for_provisions",
        "other_adjustments_to_reconcile_profit_loss",
        "income_taxes_paid_refund",
        "income_taxes_paid_refund_classified_as_operating_activities",
        "cash_flows_from_used_in_operating_activities",
        "purchase_of_property_plant_and_equipment_intangible_assets_etc",
        "proceeds_from_sales_of_property_plant_and_equipment_classi_etc",
        "cash_flows_used_in_obtaining_control_of_subsidiaries_or_ot_etc",
        "cash_flows_from_losing_control_of_subsidiaries_or_other_bu_etc",
        "dividends_received_classified_as_investing_activities",
        "interest_received_classified_as_investing_activities",
        "income_taxes_paid_refund_classified_as_investing_activities",
        "cash_flows_from_used_in_investing_activities",
        "proceeds_from_borrowings_classified_as_financing_activities",
        "repayments_of_borrowings_classified_as_financing_activities",
        "payments_to_acquire_or_redeem_entitys_shares",
        "dividends_paid_classified_as_financing_activities",
        "dividends_paid_to_equity_holders_of_parent_classified_as_f_etc",
        "dividends_paid_to_noncontrolling_interests_classified_as_f_etc",
        "interest_paid_classified_as_financing_activities",
        "cash_flows_from_used_in_financing_activities",
        "effect_of_exchange_rate_changes_on_cash_and_cash_equivalents",
        "increase_decrease_in_cash_and_cash_equivalents",
        "cash_and_cash_equivalents",
    ],
}


def fetch_facts(engine, company_name: str) -> pd.DataFrame:
    query = """
        SELECT
            ic.statement,
            ic.display_label,
            ic.normalized_name,
            p.period_type,
            p.start_date,
            p.end_date,
            fv.value,
            fv.currency
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p ON fv.period_id = p.period_id
        JOIN filing fil ON fv.filing_id = fil.filing_id
        JOIN company c ON fil.company_id = c.company_id
        WHERE c.name = %(company_name)s
    """
    df = pd.read_sql(query, engine, params={"company_name": company_name})
    if df.empty:
        return df

    # correct year labeling - see module docstring for why these differ
    def get_year(row):
        if row["period_type"] == "instant":
            return (row["end_date"] - timedelta(days=1)).year
        return row["start_date"].year

    df["year"] = df.apply(get_year, axis=1)
    return df


def build_statement_table(df: pd.DataFrame, statement: str) -> pd.DataFrame:
    subset = df[df["statement"] == statement]
    if subset.empty:
        return pd.DataFrame()

    # pivot on normalized_name first (stable key for ordering), keep a
    # name -> display_label lookup on the side to relabel at the end
    label_lookup = subset.drop_duplicates("normalized_name").set_index("normalized_name")["display_label"]
    pivoted = subset.pivot_table(
        index="normalized_name", columns="year", values="value", aggfunc="first"
    )
    pivoted = pivoted.sort_index(axis=1)  # years left to right, oldest first

    # apply the explicit statement order; anything not in the list goes
    # to the end, alphabetically, so nothing is silently dropped
    known_order = [n for n in STATEMENT_ORDER.get(statement, []) if n in pivoted.index]
    leftover = sorted(set(pivoted.index) - set(known_order))
    pivoted = pivoted.reindex(known_order + leftover)

    pivoted.index = pivoted.index.map(label_lookup)
    pivoted.index.name = "display_label"
    return pivoted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="Company name as stored in the company table")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)

    engine = create_engine(db_url)
    df = fetch_facts(engine, args.company)

    if df.empty:
        print(f"No data found for company '{args.company}'. Check the name matches what's in the database.")
        sys.exit(1)

    # report the currency explicitly - values from different companies are
    # NOT comparable unless they share one, and silently printing bare
    # numbers is how that mistake gets made
    import re
    iso_pattern = re.compile(r'^[A-Z]{3}$')
    currencies = sorted(set(
        c for c in df["currency"].dropna()
        if c and iso_pattern.match(str(c))
    ))
    cur_label = currencies[0] if len(currencies) == 1 else "/".join(currencies) if currencies else "unknown"
    print(f"Loaded {len(df)} facts for {args.company}")
    print(f"Reporting currency: {cur_label}")
    if len(currencies) > 1:
        print(f"*** WARNING: multiple currencies {currencies} - check the data.")
    print()

    statements = {
        "income_statement": "INCOME STATEMENT",
        "balance_sheet": "BALANCE SHEET",
        "cash_flow": "CASH FLOW STATEMENT",
    }

    tables = {}
    for key, title in statements.items():
        table = build_statement_table(df, key)
        tables[key] = table
        print(f"\n{'=' * 70}")
        print(f"{title}  (in {cur_label})")
        print("=" * 70)
        if table.empty:
            print("(no data)")
        else:
            pd.set_option("display.float_format", lambda x: f"{x:,.0f}")
            print(table.to_string())

    # save both a combined CSV and a multi-sheet Excel file
    out_dir = "data/raw"
    out_path = f"{out_dir}/{args.company.replace(' ', '_')}_statements.xlsx"
    try:
        with pd.ExcelWriter(out_path) as writer:
            for key, title in statements.items():
                if not tables[key].empty:
                    tables[key].to_excel(writer, sheet_name=title[:31])  # Excel sheet name limit
        print(f"\n\nSaved to {out_path}")
    except PermissionError:
        print(f"\n\nCould not save to {out_path} - the file is likely still open in Excel.")
        print("Close it and rerun this script, or the statements above are already complete in your terminal.")
