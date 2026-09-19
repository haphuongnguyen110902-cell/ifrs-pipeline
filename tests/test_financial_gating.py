"""
tests/test_financial_gating.py

Regression tests for the financial-sector gating in scripts/11_ratio_engine.py
(and the dashboard caption that explains it).

The real bug: the engine applied industrial working-capital ideas to every
company. Adyen (a payments processor: EUR 6.4bn of merchant payables vs
EUR 0.3bn of trade payables) showed DIO 275d / DPO 1,018d / CCC -713d and a
-15.9% ROIC beside a +20% ROE - plausible-looking, meaningless numbers, which
this project's own principle says are worse than a blank. The fixtures are
"Adyen-shaped" synthetic data, not Adyen's exact figures.
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
GATED = ["gross_margin", "cash_conversion", "dso", "dio", "dpo", "ccc", "roic", "net_debt_ebitda_proxy"]
UNGATED = ["operating_margin", "net_margin", "tax_rate", "roe"]


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide_row(company_id=1, company="TestCo", year=2025, **overrides):
    row = {
        "company": company, "company_id": company_id, "year": year,
        "revenue": 1000.0, "cost_of_sales": -100.0,
        "current_trade_receivables": 200.0, "inventories": 77.0,
        "trade_and_other_current_payables_to_trade_suppliers": 285.0,
        "gross_profit": 900.0, "profit_loss_from_operating_activities": 400.0,
        "profit_loss_attributable_to_owners_of_parent": 300.0,
        "cash_flows_from_used_in_operating_activities": 370.0,
        "income_tax_expense_continuing_operations": 90.0, "profit_loss_before_tax": 390.0,
        "equity_attributable_to_owners_of_parent": 1500.0, "noncontrolling_interests": 0.0,
        "cash_and_cash_equivalents": 9000.0, "longterm_borrowings": 10.0,
        "current_borrowings_and_current_portion_of_noncurrent_borr_etc": 5.0,
    }
    row.update(overrides)
    return row


def ratios_for(r11, *rows):
    return r11.compute_ratios(pd.DataFrame(list(rows)))


class TestGateDefinition:
    def test_gated_set_is_the_documented_one_and_every_name_is_a_real_ratio(self, r11):
        """A typo'd ratio name would silently gate nothing."""
        assert sorted(r11.FINANCIAL_NOT_MEANINGFUL) == sorted(GATED)
        assert set(r11.FINANCIAL_NOT_MEANINGFUL) <= set(r11.RATIO_META)
        assert all(reason.strip() for reason in r11.FINANCIAL_NOT_MEANINGFUL.values())

    def test_the_kept_ratios_are_exactly_the_complement(self, r11):
        """Widening the gate must be a deliberate edit of BOTH lists."""
        assert sorted(set(r11.RATIO_META) - set(r11.FINANCIAL_NOT_MEANINGFUL)) == sorted(UNGATED)


class TestGateFinancialRatios:
    def test_the_adyen_shape_is_absurd_before_and_blank_after(self, r11):
        before = ratios_for(r11, wide_row())
        assert before["dpo"].iloc[0] > 365 * 2          # the nonsense that was being shown
        gated, _, _ = r11.gate_financial_ratios(before, {1: "payments processor"})
        assert gated[GATED].isna().all(axis=None)

    def test_kept_ratios_are_unchanged(self, r11):
        before = ratios_for(r11, wide_row())
        gated, _, _ = r11.gate_financial_ratios(before, {1: "x"})
        pd.testing.assert_frame_equal(gated[UNGATED], before[UNGATED])
        assert gated["operating_margin"].iloc[0] == pytest.approx(40.0)

    def test_only_financial_companies_are_touched(self, r11):
        before = ratios_for(r11, wide_row(company_id=1, company="Widget"),
                            wide_row(company_id=2, company="Bank"))
        gated, _, _ = r11.gate_financial_ratios(before, {2: "sector_std is 'Financial Services'"})
        widget, bank = gated[gated["company_id"] == 1], gated[gated["company_id"] == 2]
        pd.testing.assert_frame_equal(widget.reset_index(drop=True),
                                      before[before["company_id"] == 1].reset_index(drop=True))
        assert bank[GATED].isna().all(axis=None)

    def test_private_absolute_columns_are_left_for_valuation(self, r11):
        """19_valuation/21/22/24 read _ebit/_net_debt/_ebitda/_revenue from
        compute_ratios; gating the public ratios must not starve them."""
        before = ratios_for(r11, wide_row())
        gated, _, _ = r11.gate_financial_ratios(before, {1: "x"})
        private = [c for c in before.columns if c.startswith("_")]
        assert private and set(private) >= {"_ebit", "_net_debt", "_ebitda", "_revenue"}
        pd.testing.assert_frame_equal(gated[private], before[private])

    def test_the_blanked_count_only_counts_values_that_existed(self, r11):
        before = ratios_for(r11, wide_row(inventories=None))     # DIO (and so CCC) never existed
        existing = int(before[GATED].notna().sum().sum())
        _, _, n_blanked = r11.gate_financial_ratios(before, {1: "x"})
        assert n_blanked == existing and existing < len(GATED)

    def test_every_gated_cell_gets_a_reason_even_where_no_value_existed(self, r11):
        before = ratios_for(r11, wide_row(year=2024), wide_row(year=2025, inventories=None))
        _, notes, _ = r11.gate_financial_ratios(before, {1: "payments processor"})
        assert len(notes) == 2 * len(GATED)
        assert notes[(1, 2025, "dio")] == "Not meaningful for a financial company (payments processor)"

    def test_no_financial_companies_is_a_no_op(self, r11):
        before = ratios_for(r11, wide_row())
        gated, notes, n = r11.gate_financial_ratios(before, {})
        pd.testing.assert_frame_equal(gated, before)
        assert notes == {} and n == 0

    def test_gating_twice_changes_nothing(self, r11):
        before = ratios_for(r11, wide_row())
        once, notes1, _ = r11.gate_financial_ratios(before, {1: "x"})
        twice, notes2, n2 = r11.gate_financial_ratios(once, {1: "x"})
        pd.testing.assert_frame_equal(once, twice)
        assert notes1 == notes2 and n2 == 0

    def test_input_frame_is_not_mutated(self, r11):
        before = ratios_for(r11, wide_row())
        snapshot = before.copy()
        r11.gate_financial_ratios(before, {1: "x"})
        pd.testing.assert_frame_equal(before, snapshot)


class TestWhoIsFinancial:
    def profiles(self, **rows):
        return pd.DataFrame([{"company_id": i, "name": n, "sector_std": s}
                             for i, (n, s) in enumerate(rows.items(), start=1)])

    def test_financial_services_sector_is_gated(self, r11):
        got = r11.financial_company_reasons(self.profiles(Bank="Financial Services"), {})
        assert got == {1: "sector_std is 'Financial Services'"}

    def test_other_sectors_and_missing_sector_are_not(self, r11):
        p = self.profiles(A="Technology", B=None, C="Consumer Defensive")
        p.loc[1, "sector_std"] = np.nan
        assert r11.financial_company_reasons(p, {}) == {}

    def test_an_explicit_override_catches_what_the_sector_label_misses(self, r11):
        """yfinance calls Adyen 'Technology'."""
        got = r11.financial_company_reasons(self.profiles(Adyen="Technology"),
                                            {"Adyen": "payments processor"})
        assert got == {1: "payments processor"}

    def test_an_override_reason_outranks_the_sector_label(self, r11):
        got = r11.financial_company_reasons(self.profiles(X="Financial Services"), {"X": "custom reason"})
        assert got == {1: "custom reason"}


class TestReportingModelOverrides:
    def write(self, tmp_path, text_):
        p = tmp_path / "companies.yaml"
        p.write_text(text_, encoding="utf-8")
        return p

    def test_reads_declared_overrides_with_their_reason(self, r11, tmp_path):
        p = self.write(tmp_path, (
            "companies:\n"
            "  adyen:\n    name: Adyen\n    reporting_model: financial\n    reporting_model_reason: client money\n"
            "  heineken:\n    name: Heineken\n"))
        assert r11.load_reporting_model_overrides(p) == {"Adyen": "client money"}

    def test_a_default_reason_is_supplied_when_none_is_given(self, r11, tmp_path):
        p = self.write(tmp_path, "companies:\n  a:\n    name: A\n    reporting_model: Financial\n")
        assert "financial" in r11.load_reporting_model_overrides(p)["A"].lower()

    def test_an_unknown_model_is_ignored_loudly_not_guessed(self, r11, tmp_path, capsys):
        p = self.write(tmp_path, "companies:\n  a:\n    name: A\n    reporting_model: banking\n")
        assert r11.load_reporting_model_overrides(p) == {}
        assert "unknown reporting_model" in capsys.readouterr().out

    def test_missing_file_means_no_overrides(self, r11, tmp_path):
        assert r11.load_reporting_model_overrides(tmp_path / "nope.yaml") == {}

    def test_the_real_config_declares_adyen_as_financial(self, r11):
        """The motivating case must stay covered - and the reason must be
        stated, since an override is a judgement."""
        overrides = r11.load_reporting_model_overrides()
        assert "Adyen" in overrides and "merchants" in overrides["Adyen"]


class TestProfilesDegradeLoudly:
    def test_missing_sector_column_turns_sector_gating_off_not_the_whole_run(self, r11, monkeypatch, capsys):
        """sector_std comes from migration 002; a database built only from
        schema.sql lacks it. The ratio run must not crash."""
        calls = []

        def fake_read_sql(query, engine, **kw):
            calls.append(str(query))
            if "sector_std" in str(query):
                raise RuntimeError("column company.sector_std does not exist")
            return pd.DataFrame({"company_id": [1], "name": ["A"]})

        monkeypatch.setattr(r11.pd, "read_sql", fake_read_sql)
        profiles = r11.fetch_company_profiles(object())
        assert list(profiles["company_id"]) == [1] and profiles["sector_std"].isna().all()
        assert "sector-based" in capsys.readouterr().out and len(calls) == 2


class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.conn = _FakeConn()

    def connect(self):
        return self.conn


class TestSaveToDb:
    def test_the_reason_reaches_the_insert_and_ungated_rows_get_none(self, r11):
        before = ratios_for(r11, wide_row(company_id=1, company="Widget"), wide_row(company_id=2, company="Bank"))
        gated, notes, _ = r11.gate_financial_ratios(before, {2: "sector_std is 'Financial Services'"})
        engine = _FakeEngine()
        r11.save_to_db(engine, gated, {}, notes)
        statements = [s for s, _ in engine.conn.calls]
        assert any("ADD COLUMN IF NOT EXISTS note" in s for s in statements)
        inserts = {(p["cid"], p["rn"]): p for s, p in engine.conn.calls if "INSERT INTO ratio" in s}
        assert inserts[(2, "dpo")]["note"] == "Not meaningful for a financial company (sector_std is 'Financial Services')"
        assert inserts[(2, "dpo")]["val"] is None
        assert inserts[(2, "roe")]["note"] is None and inserts[(2, "roe")]["val"] is not None
        assert inserts[(1, "dpo")]["note"] is None

    def test_omitting_notes_is_backward_compatible(self, r11):
        engine = _FakeEngine()
        r11.save_to_db(engine, ratios_for(r11, wide_row()), {})
        assert all(p["note"] is None for s, p in engine.conn.calls if "INSERT INTO ratio" in s)


class TestDashboardCaption:
    """webapp/app.py is a Streamlit script (top-level code runs on import), so
    the pure helper is extracted from its source rather than imported."""

    @pytest.fixture
    def gating_caption(self):
        source = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "gating_caption")
        namespace = {"pd": pd}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), namespace)
        return namespace["gating_caption"]

    def frame(self, notes):
        return pd.DataFrame({"display_label": ["DPO", "DIO", "ROE"], "note": notes})

    def test_lists_the_gated_ratios_and_the_reason(self, gating_caption):
        text_ = gating_caption(self.frame(["Not meaningful for a financial company (x)",
                                           "Not meaningful for a financial company (x)", None]))
        assert "DIO, DPO" in text_ and "ROE" not in text_ and "(x)" in text_ and "not missing data" in text_

    def test_no_caption_when_nothing_is_gated(self, gating_caption):
        assert gating_caption(self.frame([None, None, None])) is None

    def test_no_caption_on_a_database_without_the_note_column(self, gating_caption):
        """The engine has not run since the fix: the app must still render."""
        assert gating_caption(pd.DataFrame({"display_label": ["DPO"], "value": [1.0]})) is None

    def test_the_ratio_query_does_not_name_the_note_column(self):
        """Naming `note` would break the whole Ratios tab on a database the
        engine has not been re-run against - the moment this is merged."""
        source = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "load_ratios")
        sql = [n.value for n in ast.walk(fn)
               if isinstance(n, ast.Constant) and isinstance(n.value, str) and "FROM ratio" in n.value]
        assert len(sql) == 1
        assert sql[0].startswith("SELECT * FROM ratio") and "note" not in sql[0]
