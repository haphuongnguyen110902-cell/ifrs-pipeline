"""
tests/test_capex_resolution.py

Capex (feeds the 3-statement model and the DCF) used to read ONE line: the first of five concept names that was
stored. Audited against the companies' own cash-flow statements (2025 unless noted, millions) that was wrong for
filers that print PP&E and intangibles on separate lines: Schneider 1,072 vs 1,543 printed (PP&E + intangibles 471),
Amplifon 56.4 vs 118.4, ASM 218.5 vs 468.3 (+ capitalised development 205.1 + intangibles 44.7), Recordati 39.4 vs 85.1,
Heineken 2,133 vs 2,402. A combined line, where a company has one, is still what is read. No database.
"""
import pandas as pd
import pytest

PPE = "purchase_of_property_plant_and_equipment_classified_as_inves_etc"
INTANG = "purchase_of_intangible_assets_classified_as_investing_activi_etc"
DEV = "payments_for_development_project_expenditure"
COMBINED = "purchase_of_property_plant_and_equipment_and_intangible_asse_etc"


@pytest.fixture(scope="module")
def m21(load_script):
    return load_script("21_three_statement_model.py")


def wide(**cols):
    return pd.DataFrame([{"company": "X", "company_id": 1, "year": 2025, **cols}])


def capex(m21, **cols):
    r = m21.resolve_capex(wide(**cols))
    return r["capex"].iloc[0], r["basis"].iloc[0]


class TestPrintedStatements:
    def test_schneider_2025_ppe_plus_intangibles(self, m21):
        assert capex(m21, **{PPE: 1_072e6, INTANG: 471e6})[0] == pytest.approx(1_543e6)

    def test_amplifon_2025(self, m21):
        assert capex(m21, **{PPE: 56.4e6, INTANG: 62.0e6})[0] == pytest.approx(118.4e6)

    def test_asm_2025_includes_capitalised_development(self, m21):
        v, basis = capex(m21, **{PPE: 218.5e6, INTANG: 44.7e6, DEV: 205.1e6})
        assert v == pytest.approx(468.3e6) and basis == f"{PPE}+{INTANG}+{DEV}"

    def test_recordati_2025(self, m21):
        assert capex(m21, **{PPE: 39.4e6, INTANG: 45.7e6})[0] == pytest.approx(85.1e6)

    def test_heineken_2025(self, m21):
        assert capex(m21, **{PPE: 2_133e6, INTANG: 269e6})[0] == pytest.approx(2_402e6)

    def test_danone_prints_one_capex_line_tagged_as_ppe_so_that_is_the_capex(self, m21):
        assert capex(m21, **{PPE: 923e6})[0] == pytest.approx(923e6)


class TestRules:
    def test_a_combined_line_wins_and_is_never_added_to_its_own_parts(self, m21):
        v, basis = capex(m21, **{COMBINED: 1_495.3e6, PPE: 900e6, INTANG: 595e6})
        assert v == pytest.approx(1_495.3e6) and basis == COMBINED

    def test_the_essity_four_asset_line_and_shell_total_are_combined_lines(self, m21):
        assert capex(m21, purchase_of_property_plant_and_equipment_intangible_asset_etc=7_396e6)[1] \
            == "purchase_of_property_plant_and_equipment_intangible_asset_etc"
        assert capex(m21, cash_outflow_for_total_cash_capital_expenditure=20_915e6)[0] == pytest.approx(20_915e6)

    def test_sign_as_stored_does_not_matter(self, m21):
        """Puig stores its purchase line negative (-190.9)."""
        assert capex(m21, **{COMBINED: -190.9e6})[0] == pytest.approx(190.9e6)
        assert capex(m21, **{PPE: -10.0, INTANG: -5.0})[0] == pytest.approx(15.0)

    def test_nothing_stored_is_nan_and_blank_basis_never_zero(self, m21):
        v, basis = capex(m21, revenue=1.0)
        assert pd.isna(v) and basis == ""

    def test_only_the_intangible_line_stored_is_what_was_printed(self, m21):
        v, basis = capex(m21, **{INTANG: 5.0})
        assert v == 5.0 and basis == INTANG

    def test_rows_are_independent(self, m21):
        w = pd.DataFrame([{"year": 2023, PPE: 10.0, INTANG: 4.0}, {"year": 2024, COMBINED: 30.0},
                          {"year": 2025, "revenue": 1.0}])
        r = m21.resolve_capex(w)
        assert r["capex"].iloc[:2].tolist() == [14.0, 30.0] and pd.isna(r["capex"].iloc[2])


class TestBaseYear:
    """fetch_base_year end to end on an in-memory frame (the engine's fetch/pivot are stubbed)."""

    @staticmethod
    def base_row(**over):
        row = {"company": "X", "company_id": 1, "year": 2025,
               "revenue": 1_000.0, "cost_of_sales": -600.0, "gross_profit": 400.0,
               "profit_loss_from_operating_activities": 100.0, "profit_loss_before_tax": 90.0,
               "income_tax_expense_continuing_operations": -20.0,
               "profit_loss_attributable_to_owners_of_parent": 60.0,
               "current_trade_receivables": 150.0, "inventories": 90.0,
               "trade_and_other_current_payables_to_trade_suppliers": 60.0,
               "longterm_borrowings": 200.0, "shortterm_borrowings": 30.0, "cash_and_cash_equivalents": 50.0,
               "equity_attributable_to_owners_of_parent": 500.0,
               "adjustments_for_depreciation_and_amortisation_expense_and_etc": 40.0}
        row.update(over)
        return pd.DataFrame([row])

    def run(self, m21, monkeypatch, **over):
        w = self.base_row(**over)
        monkeypatch.setattr(m21.r11, "fetch_facts", lambda *a, **k: pd.DataFrame({"x": [1]}))
        monkeypatch.setattr(m21.r11, "pivot_to_wide", lambda df, *a, **k: w)
        return m21.fetch_base_year(None, "X")

    def test_capex_is_the_sum_of_the_printed_lines_and_says_so(self, m21, monkeypatch):
        b = self.run(m21, monkeypatch, **{PPE: 30.0, INTANG: 20.0})
        assert b["capex"] == pytest.approx(50.0) and not b["capex_is_fallback"]
        assert b["capex_basis"] == f"{PPE}+{INTANG}"

    def test_no_capex_line_is_still_the_flagged_three_percent_fallback(self, m21, monkeypatch):
        b = self.run(m21, monkeypatch)
        assert b["capex_is_fallback"] and b["capex"] == pytest.approx(30.0) and b["capex_basis"] == ""
