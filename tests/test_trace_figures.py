"""
tests/test_trace_figures.py

scripts/35_trace_figures.py follows every input of the dashboard's figures back to the printed report. Its value
rests on one property: the set of inputs it traces is COMPLETE. The first version assumed the engine reads inputs
only through get_col/get_best - wrong: financial debt is read row by row (compute_financial_debt), so every
borrowing line would have gone unchecked (found on the first live run: Essity's trace listed cash but no debt line).
The completeness test below removes one input at a time and requires every input whose removal changes a figure to
be in the trace. Run live over all 16 companies and both bases: 792 columns, 0 dependencies untraced. No database.
"""
import hashlib
from datetime import date

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def t35(load_script):
    return load_script("35_trace_figures.py")


def rich_row(**overrides):
    """One company-year with every family of input the engine reads, including debt and discontinued operations."""
    row = {"company": "X", "company_id": 1, "year": 2025,
           "revenue": 1000.0, "revenue_from_contracts_with_customers": 1000.0, "cost_of_sales": -600.0,
           "current_trade_receivables": 150.0, "inventories": 90.0,
           "trade_and_other_current_payables_to_trade_suppliers": 60.0,
           "profit_loss_from_operating_activities": 100.0, "profit_loss_before_tax": 90.0,
           "income_tax_expense_continuing_operations": 20.0, "profit_loss_attributable_to_owners_of_parent": 60.0,
           "income_from_discontinued_operations_attributable_to_owner_etc": 5.0,
           "cash_flows_from_used_in_operating_activities": 120.0,
           "equity_attributable_to_owners_of_parent": 500.0, "noncontrolling_interests": 10.0,
           "cash_and_cash_equivalents": 50.0, "longterm_borrowings": 200.0, "shortterm_borrowings": 30.0,
           "noncurrent_lease_liabilities": 40.0, "current_lease_liabilities": 8.0,
           "adjustments_for_depreciation_and_amortisation_expense_and_etc": 45.0}
    row.update(overrides)
    return pd.DataFrame([row])


class TestUsedInputs:
    def test_get_best_records_the_winner_only(self, t35):
        used = t35.used_inputs(rich_row())
        assert (1, 2025, "revenue") in used
        assert (1, 2025, "revenue_from_contracts_with_customers") not in used      # present, but lost to "revenue"

    def test_debt_lines_read_row_by_row_are_traced(self, t35):
        used = t35.used_inputs(rich_row())
        for name in ("longterm_borrowings", "shortterm_borrowings", "noncurrent_lease_liabilities",
                     "current_lease_liabilities"):
            assert (1, 2025, name) in used, name

    def test_extra_concepts_are_read_like_get_best(self, t35):
        used = t35.used_inputs(rich_row(dividends_paid=25.0, dividends_paid_classified_as_financing_activities=25.0),
                               extra_concepts=[("dividends_paid_classified_as_financing_activities", "dividends_paid")])
        assert (1, 2025, "dividends_paid_classified_as_financing_activities") in used
        assert (1, 2025, "dividends_paid") not in used

    def test_the_engine_readers_are_restored(self, t35):
        before = (t35.r11.get_col, t35.r11.get_best)
        t35.used_inputs(rich_row())
        assert (t35.r11.get_col, t35.r11.get_best) == before

    def test_nothing_a_figure_depends_on_is_left_out(self, t35):
        """Remove one input at a time: every input whose removal changes an engine output must be traced."""
        wide = rich_row()
        used = t35.used_inputs(wide)
        base = t35.r11._compute_ratios(wide).select_dtypes("number").drop(columns=["company_id", "year"])
        for col in wide.columns.difference(["company", "company_id", "year"]):
            out = t35.r11._compute_ratios(wide.assign(**{col: np.nan})).select_dtypes("number") \
                .drop(columns=["company_id", "year"])
            a, b = base.to_numpy(float), out.to_numpy(float)
            changed = not np.all((a == b) | (np.isnan(a) & np.isnan(b)))
            if changed:
                assert (1, 2025, col) in used, f"{col} changes a figure but is not traced"


class TestPrintedKey:
    def test_an_instant_is_shifted_back_one_day(self, t35):
        assert t35.printed_key("ifrs-full:Assets", "instant", None, date(2026, 1, 1)) == \
            ("ifrs-full:Assets", None, "2025-12-31")

    def test_a_duration_keeps_its_start(self, t35):
        assert t35.printed_key("ifrs-full:Revenue", "duration", date(2025, 1, 1), date(2026, 1, 1)) == \
            ("ifrs-full:Revenue", "2025-01-01", "2025-12-31")

    def test_a_one_day_duration_still_matches_its_printed_context(self, t35):
        """Recordati tags its cash-flow lines with start == end == 31 December."""
        assert t35.printed_key("Rec:X", "duration", date(2025, 12, 31), date(2026, 1, 1)) == \
            ("Rec:X", "2025-12-31", "2025-12-31")


class TestTraceStatus:
    PRINTED = {("ifrs-full:Revenue", "2025-01-01", "2025-12-31"): {1000.0},
               ("ifrs-full:DividendsPaid", "2025-01-01", "2025-12-31"): {25.0}}

    @staticmethod
    def fact(tag, value, start=date(2025, 1, 1), end=date(2026, 1, 1)):
        return {"raw_xbrl_tag": tag, "period_type": "duration", "start_date": start, "end_date": end, "value": value}

    def test_a_primary_statement_line(self, t35):
        assert t35.trace_status(self.fact("ifrs-full:Revenue", 1000.0), self.PRINTED, {"ifrs-full:Revenue"}) == \
            ("PRINTED", 1000.0)

    def test_a_figure_tagged_only_in_the_notes(self, t35):
        assert t35.trace_status(self.fact("ifrs-full:DividendsPaid", 25.0), self.PRINTED, set())[0] == "NOTE"

    def test_a_different_number_is_reported_with_what_was_printed(self, t35):
        assert t35.trace_status(self.fact("ifrs-full:Revenue", 1100.0), self.PRINTED, {"ifrs-full:Revenue"}) == \
            ("DIFFERENT", [1000.0])

    def test_another_period_is_not_found(self, t35):
        f = self.fact("ifrs-full:Revenue", 1000.0, start=date(2024, 1, 1), end=date(2025, 1, 1))
        assert t35.trace_status(f, self.PRINTED, {"ifrs-full:Revenue"})[0] == "NOT_FOUND"

    def test_a_reviewed_note_figure(self, t35):
        assert t35.trace_status(self.fact("note:essity_da_note_fy2025", 7157.0), {}, set())[0] == "REVIEWED"


class TestArchivePackage:
    def test_only_a_historical_package_name_is_looked_up(self, t35):
        assert t35.archive_package(r"data\raw\gate40\recordati.zip", lambda s, d: pytest.fail("looked up")) is None

    def test_the_package_is_looked_up_by_company_and_period(self, t35, monkeypatch):
        body = b"PK-package-bytes"
        seen = {}

        class Resp:
            content = body

            def raise_for_status(self):
                pass

        monkeypatch.setattr("requests.get", lambda url, timeout: Resp())

        def lookup(slug, period):
            seen["key"] = (slug, period)
            return "/x/package.zip", hashlib.sha256(body).hexdigest()
        assert t35.archive_package(r"data\raw\historical\asm_2020-12-31.zip", lookup) == body
        assert seen["key"] == ("asm", date(2020, 12, 31))

    def test_a_checksum_mismatch_is_refused(self, t35, monkeypatch):
        class Resp:
            content = b"tampered"

            def raise_for_status(self):
                pass

        monkeypatch.setattr("requests.get", lambda url, timeout: Resp())
        assert t35.archive_package("data/raw/historical/asm_2020-12-31.zip",
                                   lambda s, d: ("/x.zip", hashlib.sha256(b"original").hexdigest())) is None
