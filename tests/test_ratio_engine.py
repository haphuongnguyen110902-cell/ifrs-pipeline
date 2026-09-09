"""
tests/test_ratio_engine.py

Regression tests for scripts/11_ratio_engine.py. Every assertion here
was originally verified by hand in a chat session - see ROADMAP.md's
"Done" section for the story behind each one. Turning them into fixed
asserts means a future change to compute_ratios() that breaks one of
these gets caught by CI, not by re-deriving the same hand-calculation
again from scratch.
"""
import math

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def make_wide_row(**overrides):
    """One fully-populated company/year row for compute_ratios(), with
    sane defaults so a test only needs to override what it's testing."""
    row = {
        "company": "TestCo", "company_id": 1, "year": 2023,
        "revenue": 1000.0, "cost_of_sales": -600.0,
        "current_trade_receivables": 150.0, "inventories": 90.0,
        "trade_and_other_current_payables_to_trade_suppliers": 60.0,
        "gross_profit": 400.0, "profit_loss_from_operating_activities": 100.0,
        "profit_loss_attributable_to_owners_of_parent": 50.0,
        "cash_flows_from_used_in_operating_activities": 90.0,
        "tax_expense_continuing_operations": 20.0, "profit_loss_before_tax": 70.0,
        "equity_attributable_to_owners_of_parent": 500.0, "noncontrolling_interests": 0.0,
        "cash_and_cash_equivalents": 50.0,
        "longterm_borrowings": 200.0,
        "current_borrowings_and_current_portion_of_noncurrent_borr_etc": 30.0,
        "depreciation_property_plant_and_equipment": 25.0,
        "depreciation_rightofuse_assets": 5.0,
        "amortisation_intangible_assets_other_than_goodwill": 10.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


class TestWorkingCapitalRatios:
    """DSO=54.75d, DIO=54.75d, DPO=36.5d, CCC=73.0d for
    revenue=1000, COGS=600, receivables=150, inventory=90, payables=60
    (365-day convention) - hand-calculated, verified against GuruFocus
    for the real L'Oreal case this formula was built from."""

    def test_dso(self, r11):
        r = r11.compute_ratios(make_wide_row())
        assert r["dso"].iloc[0] == pytest.approx(54.75, abs=0.01)

    def test_dio(self, r11):
        r = r11.compute_ratios(make_wide_row())
        assert r["dio"].iloc[0] == pytest.approx(54.75, abs=0.01)

    def test_dpo(self, r11):
        r = r11.compute_ratios(make_wide_row())
        assert r["dpo"].iloc[0] == pytest.approx(36.5, abs=0.01)

    def test_ccc(self, r11):
        r = r11.compute_ratios(make_wide_row())
        assert r["ccc"].iloc[0] == pytest.approx(73.0, abs=0.01)

    def test_dpo_missing_payables_is_nan_not_zero(self, r11):
        """A company that doesn't disclose trade payables should get NaN,
        never a silently-wrong 0 that would corrupt CCC."""
        wide = make_wide_row(**{"trade_and_other_current_payables_to_trade_suppliers": None})
        wide = wide.drop(columns=["trade_and_other_current_payables_to_trade_suppliers"])
        r = r11.compute_ratios(wide)
        assert math.isnan(r["dpo"].iloc[0])
        assert math.isnan(r["ccc"].iloc[0])


class TestAbsoluteValuesForValuation:
    """19_valuation.py reuses these _-prefixed columns instead of
    re-deriving revenue/EBIT/net debt/EBITDA with separate logic - if
    compute_ratios() ever stops populating them, valuation silently
    breaks. Locking the exact values in place."""

    def test_ebitda_is_ebit_plus_da(self, r11):
        r = r11.compute_ratios(make_wide_row())
        # EBIT=100 (operating profit), D&A=25+5+10=40 -> EBITDA=140
        assert r["_ebitda"].iloc[0] == pytest.approx(140.0)

    def test_net_debt(self, r11):
        r = r11.compute_ratios(make_wide_row())
        # (200 long-term + 30 current borrowings) - 50 cash = 180
        assert r["_net_debt"].iloc[0] == pytest.approx(180.0)

    def test_revenue_and_net_income_kept(self, r11):
        r = r11.compute_ratios(make_wide_row())
        assert r["_revenue"].iloc[0] == pytest.approx(1000.0)
        assert r["_net_income"].iloc[0] == pytest.approx(50.0)

    def test_missing_da_defaults_to_zero_not_nan(self, r11):
        """A company that only tags SOME D&A line items should still get
        a usable (if understated) EBITDA, not NaN - see the module
        docstring's reasoning for this fillna(0) choice."""
        wide = make_wide_row()
        wide = wide.drop(columns=["depreciation_rightofuse_assets",
                                    "amortisation_intangible_assets_other_than_goodwill"])
        r = r11.compute_ratios(wide)
        assert r["_ebitda"].iloc[0] == pytest.approx(125.0)  # EBIT 100 + only PP&E D&A 25

    def test_combined_da_concept_preferred_over_granular_sum(self, r11):
        """The real Shell bug: some companies (oil & gas especially) only
        ever tag a single COMBINED cash-flow-statement D&A line and never
        the granular PP&E/ROU/intangibles breakdown at all. Summing only
        the granular concepts silently gave _da_total=0 for such a
        company, understating EBITDA by the full D&A amount and roughly
        doubling the computed EV/EBITDA (10.2x vs the real ~4.3-5.2x,
        verified against GuruFocus/Multiples.vc/StockAnalysis/
        Investing.com). Fixed by preferring the combined concept when
        present, falling back to the granular sum only when a company
        never discloses a combined total."""
        wide = make_wide_row()
        wide = wide.drop(columns=[
            "depreciation_property_plant_and_equipment",
            "depreciation_rightofuse_assets",
            "amortisation_intangible_assets_other_than_goodwill",
        ])
        wide["adjustments_for_depreciation_and_amortisation_expense_and_etc"] = 31290.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(31290.0)
        assert r["_ebitda"].iloc[0] == pytest.approx(100.0 + 31290.0)  # EBIT + combined D&A

    def test_granular_sum_still_used_when_no_combined_concept_exists(self, r11):
        """Guards the fix against over-firing: a company that discloses
        the granular breakdown (L'Oreal-style) and has NO combined line
        must still get the summed granular total, not silently drop to
        zero because the preferred combined concept is simply absent."""
        r = r11.compute_ratios(make_wide_row())  # has all 3 granular concepts, no combined
        assert r["_da_total"].iloc[0] == pytest.approx(40.0)  # 25 + 5 + 10, as before
        assert r["_ebitda"].iloc[0] == pytest.approx(140.0)

    def test_bare_da_expense_concept_used_as_last_resort(self, r11):
        """A second real bug found via a live 22_dcf.py run on L'Oreal:
        _da_total silently came out as 0 for 8 of the 11 companies, not
        just Shell - several filers (Pernod Ricard, Moncler, Puig Brands)
        use a THIRD tag variant, "adjustments_for_depreciation_and_
        amortisation_expense" (no "_and_etc" suffix), for their single
        combined D&A line - neither the granular concepts nor either
        existing combined-concept candidate matched it. Verified sane
        (3.9-14.0% of revenue) against each company's live data before
        adding it as a third, LOWEST-priority candidate."""
        wide = make_wide_row()
        wide = wide.drop(columns=[
            "depreciation_property_plant_and_equipment",
            "depreciation_rightofuse_assets",
            "amortisation_intangible_assets_other_than_goodwill",
        ])
        wide["adjustments_for_depreciation_and_amortisation_expense"] = 42.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(42.0)
        assert r["_ebitda"].iloc[0] == pytest.approx(100.0 + 42.0)

    def test_and_etc_combined_concept_still_wins_over_bare_variant(self, r11):
        """Priority guard: when a company has BOTH the "_and_etc" combined
        concept and the bare variant populated, the "_and_etc" one (higher
        priority, added first and already battle-tested against Shell)
        must still win - the bare variant is a last resort, not a
        replacement for it."""
        wide = make_wide_row()
        wide = wide.drop(columns=[
            "depreciation_property_plant_and_equipment",
            "depreciation_rightofuse_assets",
            "amortisation_intangible_assets_other_than_goodwill",
        ])
        wide["adjustments_for_depreciation_and_amortisation_expense_and_etc"] = 31290.0
        wide["adjustments_for_depreciation_and_amortisation_expense"] = 42.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(31290.0)


class TestExcelSheetNameSanitizer:
    """Found via a real crash: 'DSO (Days Sales O/S)' has a '/', which
    Excel rejects as a sheet name - AFTER the DB write had already
    succeeded, so the bug only showed up at export time."""

    def test_strips_illegal_characters(self, r11):
        assert r11.sanitize_sheet_name("DSO (Days Sales O/S)") == "DSO (Days Sales OS)"

    def test_strips_every_illegal_character(self, r11):
        result = r11.sanitize_sheet_name("A/B\\C?D*E[F]G:H")
        assert not any(ch in result for ch in "\\/?*[]:")

    def test_truncates_to_31_chars(self, r11):
        result = r11.sanitize_sheet_name("x" * 50)
        assert len(result) == 31

    def test_leaves_clean_names_alone(self, r11):
        assert r11.sanitize_sheet_name("Gross Margin") == "Gross Margin"
