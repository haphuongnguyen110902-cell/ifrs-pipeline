"""
tests/test_ebit_and_tax.py

Found by tracing every dashboard input back to the printed reports (scripts/35_trace_figures.py, Phase 2):

- Shell prints no operating-profit subtotal. Its EBIT was "revenue and other income - operating expense", which is
  equal to the unit to its profit BEFORE TAX in every year (2025: 29,756): interest expense (4,671) was deducted from
  an "operating" profit. EBIT is now profit before tax + finance costs (IAS 1.82(b)), both printed lines, and the
  basis is stored so the dashboard can say so.
- The effective tax rate divided tax by an approximation (operating profit + financial result + associates) that
  counted a missing financial result as ZERO. Danone's financial result is not one tagged line, so its 2025 rate came
  out at 24.4%. It is now IAS 12.86's own definition: tax expense / accounting profit, and accounting profit is
  "profit or loss before deducting tax expense" (IAS 12.5) - profit from continuing operations + tax, both printed,
  the same for every company (Danone 2025: 741 / 2,628 = 28.2%).
- Net debt subtracted cash with a missing cash line counted as zero - gross debt passed for net debt.
No database.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def row(**cols):
    base = {"company": "X", "company_id": 1, "year": 2025, "revenue": 266_886.0}
    return pd.DataFrame([{**base, **cols}])


class TestEffectiveTaxRate:
    def test_accounting_profit_is_profit_plus_tax(self, r11):
        """Danone 2025: profit 1,887 (associates 92 included), tax 741; its printed subtotal before associates 2,536
        does not decide the rate."""
        r = r11.compute_ratios(row(profit_loss=1_887.0, income_tax_expense_continuing_operations=741.0,
                                   profit_loss_before_tax_before_share_of_profit_loss_of_associ_etc=2_536.0))
        assert r["tax_rate"].iloc[0] == pytest.approx(100 * 741.0 / 2_628.0)

    def test_the_discontinued_result_is_left_out(self, r11):
        """It is net of its own tax: tax on continuing operations is divided by the continuing profit before tax."""
        r = r11.compute_ratios(row(profit_loss=1_300.0, profit_loss_from_discontinued_operations=300.0,
                                   income_tax_expense_continuing_operations=250.0))
        assert r["tax_rate"].iloc[0] == pytest.approx(100 * 250.0 / 1_250.0)

    def test_a_printed_profit_before_tax_is_the_fallback(self, r11):
        r = r11.compute_ratios(row(profit_loss_before_tax=70.0, income_tax_expense_continuing_operations=20.0))
        assert r["tax_rate"].iloc[0] == pytest.approx(100 * 20.0 / 70.0)

    def test_nothing_is_approximated_from_operating_profit(self, r11):
        """No profit line and no profit before tax: blank - not operating profit + a financial result taken as 0."""
        r = r11.compute_ratios(row(profit_loss_from_operating_activities=2_940.0,
                                   income_tax_expense_continuing_operations=741.0))
        assert pd.isna(r["tax_rate"].iloc[0])


class TestEbit:
    SHELL_2025 = dict(revenue_and_other_income=273_731.0, operating_expense=243_975.0, profit_loss_before_tax=29_756.0,
                      interest_expense=4_671.0, profit_loss=29_000.0, income_tax_expense_continuing_operations=756.0)

    def test_a_printed_operating_profit_is_used_as_printed(self, r11):
        r = r11.compute_ratios(row(profit_loss_from_operating_activities=2_940.0, profit_loss_before_tax=2_536.0,
                                   finance_costs=498.0))
        assert r["_ebit"].iloc[0] == 2_940.0 and r["_ebit_basis"].iloc[0] == ""

    def test_without_one_ebit_is_profit_before_tax_plus_finance_costs(self, r11):
        r = r11.compute_ratios(row(**self.SHELL_2025))
        assert r["_ebit"].iloc[0] == pytest.approx(29_756.0 + 4_671.0)
        assert r["_ebit_basis"].iloc[0] == r11.EBIT_BASIS_DERIVED
        assert r11.source_concepts_for("operating_margin", r.iloc[0]) == [r11.EBIT_BASIS_DERIVED]

    def test_revenue_less_operating_expense_is_never_ebit(self, r11):
        """Shell's 'total expenditure' includes interest expense: the difference is profit before tax."""
        cols = {k: v for k, v in self.SHELL_2025.items() if k != "interest_expense"}
        r = r11.compute_ratios(row(**cols))
        assert pd.isna(r["_ebit"].iloc[0])

    def test_an_adjusted_measure_is_never_operating_profit(self, r11):
        """Essity's 'operating profit excluding items affecting comparability' is an alternative performance measure."""
        r = r11.compute_ratios(row(operating_profit_excl_i_a_c=19_000.0))
        assert pd.isna(r["_ebit"].iloc[0])


class TestNetDebtNeedsCash:
    def test_no_cash_line_leaves_net_debt_blank(self, r11):
        r = r11.compute_ratios(row(longterm_borrowings=200.0, shortterm_borrowings=30.0))
        assert pd.isna(r["_net_debt"].iloc[0])

    def test_with_cash_it_is_debt_less_cash(self, r11):
        r = r11.compute_ratios(row(longterm_borrowings=200.0, shortterm_borrowings=30.0, cash_and_cash_equivalents=50.0))
        assert r["_net_debt"].iloc[0] == pytest.approx(180.0)


class TestDashboardCaption:
    """webapp/app.py is a Streamlit script (top-level code runs on import): pull the helper out of its source."""

    @pytest.fixture
    def caption(self):
        tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
        parts = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "ebit_basis_caption"]
        assert len(parts) == 1
        ns = {"pd": pd}
        exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
        return ns["ebit_basis_caption"]

    def test_a_derived_ebit_is_said_so(self, caption, r11):
        df = pd.DataFrame({"ratio_name": ["operating_margin"], "source_concepts": [[r11.EBIT_BASIS_DERIVED]]})
        assert "no operating-profit line" in caption(df)

    def test_a_printed_operating_profit_has_no_caption(self, caption):
        assert caption(pd.DataFrame({"ratio_name": ["operating_margin"], "source_concepts": [None]})) is None
        assert caption(pd.DataFrame({"ratio_name": ["operating_margin"], "value": [1.0]})) is None
