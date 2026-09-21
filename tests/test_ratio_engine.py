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


class TestRevenueAndGrossProfitFallbacks:
    """Real bugs found via a live 22_dcf.py/21_three_statement_model.py
    run: 6 of 11 companies failed fetch_base_year() entirely, most of it
    unrelated to the D&A gap. Root causes traced to this file's revenue
    and gross_profit lookups."""

    def test_revenue_from_contracts_with_customers_used_as_fallback(self, r11):
        """Kering, Pernod Ricard and Amplifon never tag the bare "revenue"
        concept in ANY year - only "revenue_from_contracts_with_customers"
        (the IFRS 15-specific tag). Same top-line figure, different
        taxonomy element - safe to add as a fallback, unlike the D&A tags'
        genuine ambiguity."""
        wide = make_wide_row()
        wide = wide.drop(columns=["revenue"])
        wide["revenue_from_contracts_with_customers"] = 1000.0
        r = r11.compute_ratios(wide)
        assert r["_revenue"].iloc[0] == pytest.approx(1000.0)
        assert r["gross_margin"].iloc[0] == pytest.approx(40.0)  # 400/1000

    def test_bare_revenue_tag_still_preferred_over_contracts_variant(self, r11):
        """Priority guard: when both are present, the plain "revenue" tag
        (higher priority, the original/most common case) must still win."""
        wide = make_wide_row(revenue=1000.0)
        wide["revenue_from_contracts_with_customers"] = 999999.0
        r = r11.compute_ratios(wide)
        assert r["_revenue"].iloc[0] == pytest.approx(1000.0)

    def test_gross_profit_derived_from_revenue_minus_cost_of_sales_when_untagged(self, r11):
        """Danone tags cost_of_sales but never a distinct gross_profit
        subtotal - its income statement goes straight from Cost of Sales
        to Operating Profit. Gross Profit = Revenue - COGS is a textbook
        identity, not a guess, so it's safe to derive here."""
        wide = make_wide_row()
        wide = wide.drop(columns=["gross_profit"])  # revenue=1000, cost_of_sales=-600 remain
        r = r11.compute_ratios(wide)
        assert r["gross_margin"].iloc[0] == pytest.approx(40.0)  # (1000-600)/1000

    def test_cost_of_sales_derived_from_revenue_minus_gross_profit_when_untagged(self, r11):
        """Essity tags gross_profit but never cost_of_sales directly - the
        reverse of the Danone case. DIO/DPO need COGS as their
        denominator, so deriving it here (rather than leaving DIO/DPO NaN
        for a company that actually discloses enough to compute it) is
        the same textbook identity, just solved for the other variable."""
        wide = make_wide_row()
        wide = wide.drop(columns=["cost_of_sales"])  # revenue=1000, gross_profit=400 remain
        r = r11.compute_ratios(wide)
        # cogs = 1000 - 400 = 600, matching the original fixture's cost_of_sales
        assert r["dio"].iloc[0] == pytest.approx(54.75, abs=0.01)
        assert r["dpo"].iloc[0] == pytest.approx(36.5, abs=0.01)

    def test_no_derivation_possible_when_neither_tagged_stays_honest_nan(self, r11):
        """Amplifon and Shell tag NEITHER gross_profit nor cost_of_sales at
        all - a "by nature" P&L presentation with no COGS/gross-profit
        split in the statements, not a tagging bug. Nothing to derive
        from, so gross_margin/dio/dpo must stay NaN, never a fabricated
        number - see CLAUDE.md's "prefer an explicit not available
        state" principle."""
        wide = make_wide_row()
        wide = wide.drop(columns=["gross_profit", "cost_of_sales"])
        r = r11.compute_ratios(wide)
        assert math.isnan(r["gross_margin"].iloc[0])
        assert math.isnan(r["dio"].iloc[0])
        assert math.isnan(r["dpo"].iloc[0])


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

    def test_da_addback_is_never_negative_whatever_sign_the_filer_used(self, r11):
        """Essity's FY2020 filing stores its 2019 D&A as -7,529M (2020: -7,671M); only the later FY2021
        filing carries +7,671M for 2020, so 2019 has no positive twin to win the conflict. The negative value
        went straight into EBITDA (EBIT - 7,529 instead of + 7,529: ~SEK 15bn too low in 2019)."""
        wide = make_wide_row().drop(columns=["depreciation_property_plant_and_equipment",
                                             "depreciation_rightofuse_assets",
                                             "amortisation_intangible_assets_other_than_goodwill"])
        wide["adjustments_for_depreciation_and_amortisation_expense_and_etc"] = -7529.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(7529.0)
        assert r["_ebitda"].iloc[0] == pytest.approx(100.0 + 7529.0)

    @staticmethod
    def _no_granular(r):
        return r.drop(columns=["depreciation_property_plant_and_equipment", "depreciation_rightofuse_assets",
                               "amortisation_intangible_assets_other_than_goodwill"])

    def test_printed_depreciation_and_amortisation_lines_are_the_da(self, r11):
        """Recordati prints two cash-flow lines and no combined one (FY2025, EUR thousands: depreciation 36,4xx,
        amortisation 170,0xx; all six years FY2020-FY2025 have both). D&A 206.4M = 7.9% of revenue, in line with a
        specialty-pharma group that carries acquired product rights. It was read as 0 (EBITDA = EBIT, flagged)."""
        wide = self._no_granular(make_wide_row())
        wide["adjustments_for_depreciation_expense"] = 36.4
        wide["adjustments_for_amortisation_expense"] = 170.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(206.4)
        assert r["_ebitda"].iloc[0] == pytest.approx(100.0 + 206.4)
        assert r["_da_basis"].iloc[0] == "adjustments_for_depreciation_expense+adjustments_for_amortisation_expense"

    def test_a_lone_depreciation_or_amortisation_line_never_passes_for_the_total(self, r11):
        """LVMH prints right-of-use depreciation on its own (3,228M) next to a line bundled with provisions: one
        half of a pair is not D&A."""
        for name in ("adjustments_for_depreciation_expense", "adjustments_for_amortisation_expense"):
            wide = self._no_granular(make_wide_row())
            wide[name] = 3228.0
            r = r11.compute_ratios(wide)
            assert r["_da_total"].iloc[0] == 0.0 and r["_da_basis"].iloc[0] == ""

    def test_a_combined_line_still_beats_the_pair_and_the_pair_beats_the_granular_sum(self, r11):
        wide = make_wide_row()      # granular 25 + 5 + 10
        wide["adjustments_for_depreciation_expense"] = 60.0
        wide["adjustments_for_amortisation_expense"] = 40.0
        assert r11.compute_ratios(wide)["_da_total"].iloc[0] == pytest.approx(100.0)
        wide["adjustments_for_depreciation_and_amortisation_expense_and_etc"] = 777.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(777.0)
        assert r["_da_basis"].iloc[0] == "adjustments_for_depreciation_and_amortisation_expense_and_etc"

    def test_the_pair_is_sign_insensitive(self, r11):
        wide = self._no_granular(make_wide_row())
        wide["adjustments_for_depreciation_expense"] = -36.4
        wide["adjustments_for_amortisation_expense"] = -170.0
        assert r11.compute_ratios(wide)["_da_total"].iloc[0] == pytest.approx(206.4)

    def test_the_basis_names_the_granular_lines_or_is_blank(self, r11):
        assert r11.compute_ratios(make_wide_row())["_da_basis"].iloc[0] ==             "depreciation_property_plant_and_equipment+depreciation_rightofuse_assets+amortisation_intangible_assets_other_than_goodwill"
        assert r11.compute_ratios(self._no_granular(make_wide_row()))["_da_basis"].iloc[0] == ""

    def test_granular_da_components_are_abs_too(self, r11):
        wide = make_wide_row()
        wide["depreciation_property_plant_and_equipment"] = -25.0
        r = r11.compute_ratios(wide)
        assert r["_da_total"].iloc[0] == pytest.approx(40.0)   # 25 + 5 + 10


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


# ---------------------------------------------------------------- deterministic fact resolution
#
# Real bug behind these: the wide pivot used aggfunc="first" over a SQL
# result with no ORDER BY, so where two filings disagreed about the same
# (company, year, concept) the winner was whatever the database returned
# first. Measured live: shuffling the input rows moved up to 47 ratio cells
# on the old code, 0 on the new. The concrete cases below are the ones found
# in the live data (Recordati's mis-dated opening cash, EssilorLuxottica's
# restated 2021, Essity's flipped D&A sign).

from datetime import date  # noqa: E402


def _fact(concept, value, filing_id, *, year=2021, company_id=1, ptype="duration",
          start=None, end=None):
    if ptype == "duration":
        start = start or date(year, 1, 1)
        end = end or date(year + 1, 1, 1)
    else:
        start, end = None, end or date(year + 1, 1, 1)
    return {"company": "TestCo", "company_id": company_id, "year": year,
            "normalized_name": concept, "period_type": ptype, "start_date": start,
            "end_date": end, "value": value, "currency": "EUR", "filing_id": filing_id}


def _anchors(filing_id, fy_end, n=6):
    """Enough facts ending on fy_end that the filing's own fiscal year is
    unambiguous - a real filing has hundreds."""
    return [_fact(f"anchor_{i}", 1.0, filing_id, ptype="instant", end=fy_end) for i in range(n)]


def _frame(*rows):
    return pd.DataFrame([r for group in rows for r in (group if isinstance(group, list) else [group])])


def _value(r11, df, concept, year=2021):
    wide = r11.pivot_to_wide(df)
    return wide.loc[wide["year"] == year, concept].iloc[0]


class TestDeterministicFactResolution:

    def test_result_does_not_depend_on_row_order(self, r11):
        """The property the old code lacked."""
        df = _frame(
            _anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
            _fact("revenue", 100.0, 1), _fact("revenue", 90.0, 2),                 # restated
            _fact("da", -7671.0, 1), _fact("da", 7671.0, 2),                       # sign flip
            _fact("cash", 188.0, 2, ptype="instant", end=date(2021, 1, 2)),        # mis-dated opening
            _fact("cash", 245.0, 2, ptype="instant", end=date(2022, 1, 1)),
        )
        expected = r11.pivot_to_wide(df).sort_index(axis=1).reset_index(drop=True)
        for seed in range(25):
            shuffled = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
            got = r11.pivot_to_wide(shuffled).sort_index(axis=1).reset_index(drop=True)
            pd.testing.assert_frame_equal(got, expected)

    def test_latest_filing_wins_for_restated_comparatives(self, r11):
        """EssilorLuxottica 2021: the FY2022 filing restated FY2021
        (operating profit 2,326M -> 2,307M); the restated figure is the one
        on the same basis as the latest year."""
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("revenue", 100.0, 1), _fact("revenue", 90.0, 2))
        assert _value(r11, df, "revenue") == 90.0
        _, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        row = conflicts[conflicts["normalized_name"] == "revenue"].iloc[0]
        assert row["kind"] == "restated"
        assert (row["chosen_value"], row["alt_value"]) == (90.0, 100.0)

    def test_identical_repeats_are_not_conflicts(self, r11):
        """The normal case: each annual report repeats last year's
        comparatives verbatim - 4,752 of 4,977 multi-row keys in the live DB."""
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("revenue", 100.0, 1), _fact("revenue", 100.0, 2))
        wide, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        assert wide.loc[wide["year"] == 2021, "revenue"].iloc[0] == 100.0
        assert conflicts[conflicts["normalized_name"] == "revenue"].empty

    def test_year_end_instant_beats_a_mis_dated_opening_balance(self, r11):
        """Recordati: the opening cash balance is dated 2021-01-02, which the
        engine's year mapping puts in 2021 - the same year as the real
        2021-12-31 close. The old 'first' picked the opening balance (188.2M
        vs the real 244.6M), overstating net debt by 9%."""
        df = _frame(_anchors(1, date(2023, 1, 1)),
                    _fact("cash", 188.23, 1, ptype="instant", end=date(2021, 1, 2)),
                    _fact("cash", 244.578, 1, ptype="instant", end=date(2022, 1, 1)))
        assert _value(r11, df, "cash", 2021) == pytest.approx(244.578)
        _, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        assert conflicts[conflicts["normalized_name"] == "cash"]["kind"].tolist() == ["other_period"]

    def test_annual_duration_beats_a_stub_even_from_a_newer_filing(self, r11):
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("revenue", 100.0, 1),                                     # 365 days
                    _fact("revenue", 40.0, 2, end=date(2021, 7, 1)),                # 6-month stub
                    _fact("revenue", 999.0, 2, end=date(2033, 1, 1)))               # bogus far-future end
        assert _value(r11, df, "revenue") == 100.0

    def test_sign_flip_is_reported_and_the_later_filing_sign_used(self, r11):
        """Essity 2020 D&A: -7,671M in the 2021 filing, +7,671M in the 2022
        filing. (_da_total itself is abs()'d, see test_da_addback_is_never_negative...; the conflict is
        still reported so a sign flip stays visible.)"""
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("da", -7671.0, 1), _fact("da", 7671.0, 2))
        assert _value(r11, df, "da") == 7671.0
        _, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        assert conflicts[conflicts["normalized_name"] == "da"]["kind"].tolist() == ["sign_flip"]

    def test_tiny_difference_is_classified_as_rounding(self, r11):
        """Kering 2021 revenue: 17,645.2M (one-decimal filing) vs 17,645.0M."""
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("revenue", 17645.2, 1), _fact("revenue", 17645.0, 2))
        _, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        assert conflicts[conflicts["normalized_name"] == "revenue"]["kind"].tolist() == ["rounding"]

    def test_a_stray_future_dated_fact_does_not_make_an_old_filing_look_newest(self, r11):
        """Recordati's filing carries one fact dated 2033. Judging a filing's
        year by its MAX end date would rank that old filing as the newest and
        let its older numbers beat the real latest filing's restatement."""
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("stray", 1.0, 1, ptype="instant", end=date(2033, 1, 1)),
                    _fact("x", 5.0, 1, ptype="instant", end=date(2022, 1, 1)),
                    _fact("x", 7.0, 2, ptype="instant", end=date(2022, 1, 1)))
        assert _value(r11, df, "x") == 7.0

    def test_filing_reporting_year_is_taken_from_the_facts(self, r11):
        df = _frame(_anchors(1, date(2023, 1, 1)), _fact("stray", 1.0, 1, ptype="instant", end=date(2033, 1, 1)))
        assert r11.filing_reporting_years(df).loc[1] == 2022

    def test_nan_never_beats_a_real_value(self, r11):
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("revenue", 100.0, 1), _fact("revenue", float("nan"), 2))
        assert _value(r11, df, "revenue") == 100.0

    def test_works_without_a_filing_id_column(self, r11):
        """Callers/tests that predate filing_id still get a deterministic result."""
        df = _frame(_fact("revenue", 100.0, 1), _fact("revenue", 90.0, 1)).drop(columns=["filing_id"])
        first = r11.pivot_to_wide(df)
        second = r11.pivot_to_wide(df.iloc[::-1].reset_index(drop=True))
        pd.testing.assert_frame_equal(first.sort_index(axis=1), second.sort_index(axis=1))

    def test_empty_input_is_safe(self, r11):
        resolved, conflicts = r11.resolve_fact_conflicts(pd.DataFrame(
            columns=["company", "company_id", "year", "normalized_name", "period_type",
                     "start_date", "end_date", "value", "currency", "filing_id"]))
        assert resolved.empty and conflicts.empty

    def test_conflict_summary_prints_sign_flips_loudly(self, r11, capsys):
        df = _frame(_anchors(1, date(2022, 1, 1)), _anchors(2, date(2023, 1, 1)),
                    _fact("da", -7671.0, 1), _fact("da", 7671.0, 2))
        _, conflicts = r11.pivot_to_wide(df, return_conflicts=True)
        r11.print_conflict_summary(conflicts)
        out = capsys.readouterr().out
        assert "SIGN FLIP" in out and "TestCo 2021 da" in out
        r11.print_conflict_summary(conflicts.iloc[0:0])      # empty report prints nothing
        assert capsys.readouterr().out == ""
