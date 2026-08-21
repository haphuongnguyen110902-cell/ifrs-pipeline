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
        "revenue", "revenue_from_dividends", "cost_of_sales", "gross_profit",
        "research_and_development_expense", "advertising_expense",
        "selling_general_and_administrative_expense",
        "autres_produits_et_charges_non_recurrent",
        "resultat_dexploitation", "profit_loss_from_operating_activities",
        "other_finance_income_cost", "cout_de_lendettement_financier_brut",
        "cout_de_lendettement_financier_net",
        "interest_income_on_cash_and_cash_equivalents",
        "share_of_profit_loss_of_associates_and_joint_ventures_acc_etc",
        "resultat_avant_impot_et_societes_mises_en_equivalence",
        "income_tax_expense_continuing_operations",
        "comprehensive_income_attributable_to_owners_of_parent",
        "comprehensive_income_attributable_to_noncontrolling_inter_etc",
        "basic_earnings_loss_per_share", "diluted_earnings_loss_per_share",
        "resultat_net_par_action_hors_elements_non_recurrents_part_etc",
        "resultat_net_dilue_par_action_hors_elements_non_recurrent_etc",
    ],
    "balance_sheet": [
        "property_plant_and_equipment", "rightofuse_assets", "goodwill",
        "intangible_assets_other_than_goodwill",
        "investment_accounted_for_using_equity_method",
        "noncurrent_financial_assets", "deferred_tax_assets", "noncurrent_assets",
        "inventories", "current_trade_receivables", "current_tax_assets_current",
        "actifs_courants_autres", "current_assets", "assets",
        "issued_capital", "additional_paidin_capital",
        "retained_earnings_excluding_profit_loss_for_reporting_period",
        "retained_earnings_profit_loss_for_reporting_period",
        "accumulated_other_comprehensive_income",
        "equity_attributable_to_owners_of_parent", "noncontrolling_interests",
        "longterm_borrowings", "noncurrent_lease_liabilities",
        "deferred_tax_liabilities", "current_tax_liabilities_noncurrent",
        "noncurrent_provisions_for_employee_benefits", "other_longterm_provisions",
        "noncurrent_liabilities",
        "current_borrowings_and_current_portion_of_noncurrent_borr_etc",
        "current_lease_liabilities", "trade_and_other_current_payables_to_trade_suppliers",
        "current_tax_liabilities_current", "current_provisions",
        "passifs_courants_autres", "current_liabilities", "equity_and_liabilities",
    ],
    "cash_flow": [
        "other_adjustments_for_noncash_items",
        "elimination_de_produits_sans_incidence_sur_la_tresorerie__etc",
        "adjustments_for_losses_gains_on_disposal_of_noncurrent_as_etc",
        "adjustments_for_deferred_tax_expense", "adjustments_for_sharebased_payments",
        "adjustments_for_undistributed_profits_of_investments_acco_etc",
        "cash_flows_from_used_in_operations_before_changes_in_work_etc",
        "increase_decrease_in_working_capital",
        "dividends_received_classified_as_operating_activities",
        "interest_paid_classified_as_operating_activities", "income_taxes_paid_refund",
        "cash_flows_from_used_in_operating_activities",
        "acquisitions_d_immobilisations_corporelles_et_incorporelles",
        "cessions_d_immobilisations_corporelles_et_incorporelle",
        "variation_des_autres_actifs_financiers_y_compris_les_titr_etc",
        "incidence_des_variations_de_perimetre",
        "cash_flows_from_used_in_investing_activities",
        "proceeds_from_issuing_shares",
        "valeur_de_cession_acquisition_des_actions_propres",
        "variations_nettes_des_titres_loreal_auto_detenus",
        "proceeds_from_noncurrent_borrowings", "repayments_of_noncurrent_borrowings",
        "cash_flows_from_used_in_increase_decrease_in_current_borr_etc",
        "payments_from_changes_in_ownership_interests_in_subsidiaries",
        "payments_of_lease_liabilities_classified_as_financing_act_etc",
        "interets_payes_sur_dettes_de_location", "cash_outflow_for_leases",
        "dividends_paid_classified_as_financing_activities",
        "cash_flows_from_used_in_financing_activities",
        "effect_of_exchange_rate_changes_on_cash_and_cash_equivalents",
        "increase_decrease_in_cash_and_cash_equivalents",
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
    currencies = sorted(set(c for c in df["currency"].dropna() if c))
    cur_label = "/".join(currencies) if currencies else "unknown currency"
    print(f"Loaded {len(df)} facts for {args.company}")
    print(f"Reporting currency: {cur_label}")
    if len(currencies) > 1:
        print("*** WARNING: multiple currencies in one company's filing - check the data.")
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
