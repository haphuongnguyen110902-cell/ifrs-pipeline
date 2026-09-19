"""
tests/test_validate.py

Regression tests for scripts/08_validate.py - the pipeline's accounting-identity
safety net. Found while reviewing it:

  * it printed "FAILURES" but EXITED 0, so run_pipeline.py's validation gate and
    CI's `validate` mode could never stop anything;
  * its 1% tolerance let a genuine 0.31% gap through (Recordati 2022: exactly
    12.47M of held-for-distribution assets, an IFRS 5 line neither current nor
    non-current);
  * "Gross Profit = Revenue - CoS" failed for Adyen by 180.4M = 192.98M 'costs
    incurred from financial institutions' - 12.57M net interest income, read
    straight from its filing: a financial-model income statement, so the
    identity is inapplicable, not violated;
  * it took whichever row the database returned first per (company, year,
    concept) - the same nondeterminism the ratio engine had.

Synthetic frames only; the real-data cases above are reproduced with their
actual figures.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def v(load_script):
    return load_script("08_validate.py")


def frame(*facts):
    """facts: (company, year, concept, value)."""
    return pd.DataFrame([{"company": c, "year": y, "normalized_name": n, "value": val} for c, y, n, val in facts])


def by_label(results, label):
    return [r for r in results if r[2] == label]


class TestAssetsEqualEquityPlusLiabilities:
    def test_passes_when_they_tie(self, v):
        r = v.check_identities(frame(("A", 2024, "assets", 1000.0), ("A", 2024, "equity_and_liabilities", 1000.0)))
        assert by_label(r, "Assets = Equity + Liabilities")[0][3] is True

    def test_tolerance_is_a_tenth_of_a_percent(self, v):
        ok = v.check_identities(frame(("A", 2024, "assets", 100000.0), ("A", 2024, "equity_and_liabilities", 100040.0)))
        bad = v.check_identities(frame(("A", 2024, "assets", 100000.0), ("A", 2024, "equity_and_liabilities", 100200.0)))
        assert by_label(ok, "Assets = Equity + Liabilities")[0][3] is True       # 0.04%
        assert by_label(bad, "Assets = Equity + Liabilities")[0][3] is False     # 0.20% - the old 1% missed this


class TestHeldForSaleInTheAssetIdentity:
    def test_recordati_2022_ties_exactly_with_its_held_for_distribution_line(self, v):
        """1,173,617,000 + 2,812,736,000 + 12,470,000 = 3,998,823,000 (real figures)."""
        df = frame(("Recordati", 2022, "assets", 3_998_823_000.0),
                   ("Recordati", 2022, "current_assets", 1_173_617_000.0),
                   ("Recordati", 2022, "noncurrent_assets", 2_812_736_000.0),
                   ("Recordati", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for__etc_x", 12_470_000.0))
        assert by_label(v.check_identities(df), "Current + Non-current = Total Assets")[0][3] is True

    def test_without_that_line_the_same_gap_is_a_failure(self, v):
        df = frame(("R", 2022, "assets", 3_998_823_000.0), ("R", 2022, "current_assets", 1_173_617_000.0),
                   ("R", 2022, "noncurrent_assets", 2_812_736_000.0))
        assert by_label(v.check_identities(df), "Current + Non-current = Total Assets")[0][3] is False

    def test_held_for_sale_already_inside_current_assets_does_not_double_count(self, v):
        """Some filers include it in current assets: current + non-current already
        equals total, and adding the line would overshoot."""
        df = frame(("B", 2022, "assets", 1000.0), ("B", 2022, "current_assets", 400.0),
                   ("B", 2022, "noncurrent_assets", 600.0),
                   ("B", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for_sale", 50.0))
        assert by_label(v.check_identities(df), "Current + Non-current = Total Assets")[0][3] is True

    def test_a_real_omission_still_fails_even_with_a_held_for_sale_line(self, v):
        df = frame(("B", 2022, "assets", 1000.0), ("B", 2022, "current_assets", 400.0),
                   ("B", 2022, "noncurrent_assets", 500.0),
                   ("B", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for_sale", 20.0))
        assert by_label(v.check_identities(df), "Current + Non-current = Total Assets")[0][3] is False

    def test_the_several_held_for_names_are_summed_and_scoped_to_the_company_year(self, v):
        df = frame(("A", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for_sale", 3.0),
                   ("A", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for__etc_x", 4.0),
                   ("A", 2021, "noncurrent_assets_or_disposal_groups_classified_as_held_for_sale", 99.0),
                   ("Z", 2022, "noncurrent_assets_or_disposal_groups_classified_as_held_for_sale", 99.0),
                   ("A", 2022, "assets", 10.0))
        assert v.held_for_sale(df, "A", 2022) == 7.0
        assert v.held_for_sale(df, "A", 2030) == 0.0


class TestGrossProfitIdentityAndTheFinancialModel:
    ADYEN = [("Adyen", 2025, "revenue", 2_646_897_000.0), ("Adyen", 2025, "cost_of_sales", 102_296_000.0),
             ("Adyen", 2025, "gross_profit", 2_364_191_000.0)]

    def test_it_fails_for_an_industrial_whose_numbers_do_not_tie(self, v):
        assert by_label(v.check_identities(frame(*self.ADYEN)), "Gross Profit = Revenue - CoS")[0][3] is False

    def test_it_is_not_applied_to_a_financial_model_company(self, v):
        """Adyen's 'Net revenue' (tagged GrossProfit) is after financial-institution
        costs and net interest - 180.4M the identity cannot explain, by design."""
        r = v.check_identities(frame(*self.ADYEN), skip_gross_profit={"Adyen"})
        assert by_label(r, "Gross Profit = Revenue - CoS") == []

    def test_the_skip_is_only_for_the_named_company(self, v):
        rows = self.ADYEN + [(c, 2025, n, val) for c, _, n, val in self.ADYEN if False]
        other = [("Other", y, n, val) for _, y, n, val in self.ADYEN]
        r = v.check_identities(frame(*(rows + other)), skip_gross_profit={"Adyen"})
        assert [x[0] for x in by_label(r, "Gross Profit = Revenue - CoS")] == ["Other"]

    def test_either_sign_convention_for_cost_of_sales_passes(self, v):
        for cos in (600.0, -600.0):
            r = v.check_identities(frame(("A", 2024, "revenue", 1000.0), ("A", 2024, "cost_of_sales", cos),
                                         ("A", 2024, "gross_profit", 400.0)))
            assert by_label(r, "Gross Profit = Revenue - CoS")[0][3] is True


class TestFailureCountDrivesTheExitCode:
    CLEAN = frame(("A", 2024, "assets", 1000.0), ("A", 2024, "equity_and_liabilities", 1000.0))
    BROKEN = frame(("A", 2024, "assets", 1000.0), ("A", 2024, "equity_and_liabilities", 900.0))

    def test_a_clean_dataset_reports_zero_failures(self, v):
        assert v.run_identity_and_regression_checks(self.CLEAN, regression_cases=[]) == 0

    def test_each_failed_identity_is_counted(self, v):
        assert v.run_identity_and_regression_checks(self.BROKEN, regression_cases=[]) == 1

    def test_a_failed_known_value_is_counted(self, v):
        df = frame(("A", 2024, "revenue", 1000.0))
        assert v.run_identity_and_regression_checks(df, regression_cases=[("A", 2024, "revenue", 2000.0, 0.5)]) == 1

    def test_a_known_value_within_tolerance_passes_and_a_missing_one_is_skipped_not_failed(self, v):
        df = frame(("A", 2024, "revenue", 1001.0))
        cases = [("A", 2024, "revenue", 1000.0, 0.5), ("A", 2019, "revenue", 5.0, 0.5)]
        assert v.run_identity_and_regression_checks(df, regression_cases=cases) == 0

    def test_the_skipped_financial_companies_are_named_in_the_output(self, v, capsys):
        df = frame(*TestGrossProfitIdentityAndTheFinancialModel.ADYEN)
        v.run_identity_and_regression_checks(df, financial={"Adyen": "payments processor"}, regression_cases=[])
        assert "not applied (financial reporting model): Adyen" in capsys.readouterr().out

    def test_the_script_exits_nonzero_when_anything_failed(self):
        """__main__ needs a database, so check the source: a positive failure
        count must reach sys.exit(1)."""
        source = (REPO / "scripts" / "08_validate.py").read_text(encoding="utf-8")
        main = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.If) and getattr(n.test, "left", None) is not None
                    and getattr(n.test.left, "id", "") == "__name__")
        ifs = [n for n in ast.walk(main) if isinstance(n, ast.If) and getattr(n.test, "id", "") == "n_failed"]
        assert ifs, "expected `if n_failed:` in __main__"
        assert any(isinstance(c, ast.Call) and ast.unparse(c) == "sys.exit(1)" for n in ifs for c in ast.walk(n))


class TestDeterministicFacts:
    def test_fetch_all_resolves_conflicts_instead_of_taking_the_first_row(self, v, monkeypatch):
        """Two filings disagree about the same (company, year, concept): the
        LATER filing must win regardless of the order rows come back in."""
        from datetime import date

        def fact(concept, value, filing_id, end):
            return {"company": "T", "company_id": 1, "year": 2021, "normalized_name": concept,
                    "period_type": "duration", "start_date": date(2021, 1, 1), "end_date": end,
                    "value": value, "currency": "EUR", "filing_id": filing_id}

        anchors = lambda fid, end: [dict(fact(f"a{i}", 1.0, fid, end), period_type="instant", start_date=None) for i in range(6)]
        rows = (anchors(1, date(2022, 1, 1)) + anchors(2, date(2023, 1, 1))
                + [fact("revenue", 100.0, 1, date(2022, 1, 1)), fact("revenue", 90.0, 2, date(2022, 1, 1))])
        for order in (rows, rows[::-1]):
            monkeypatch.setattr(v.r11, "fetch_facts", lambda engine, _o=order: pd.DataFrame(_o))
            out = v.fetch_all(object())
            assert out[out["normalized_name"] == "revenue"]["value"].tolist() == [90.0]

    def test_an_empty_database_is_handled(self, v, monkeypatch):
        monkeypatch.setattr(v.r11, "fetch_facts", lambda engine: pd.DataFrame())
        assert v.fetch_all(object()).empty

    def test_the_dead_unused_identities_list_is_gone(self):
        """pyflakes flagged it: assigned and never used."""
        source = (REPO / "scripts" / "08_validate.py").read_text(encoding="utf-8")
        assert "identities = [" not in source
