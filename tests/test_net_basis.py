"""
tests/test_net_basis.py

Net margin divides the owners' profit by revenue. When a business is discontinued, IFRS 5.33 takes it out of revenue
and shows its result as one line below the continuing result - but the owners' profit still includes that line. Live
data: Essity 2024 (Vinda sold, a SEK 8,919M gain in discontinued operations) showed a 14.4% net margin against 8.2% on
its continuing business; Kering 2025 showed a profit where its continuing operations made a loss (-29M). The numerator
is now the owners' profit from continuing operations - checked live against every company-year where the filer also
tags that line itself (13 of 13 equal). No database.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
CONTINUING = "profit attributable to owners from continuing operations (IFRS 5)"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide(**cols):
    base = {"company": "X", "company_id": 1, "year": 2024, "revenue": 145_546.0, "cost_of_sales": -100_000.0,
            "profit_loss_from_operating_activities": 17_000.0, "profit_loss_before_tax": 15_500.0,
            "income_tax_expense_continuing_operations": 3_500.0,
            "profit_loss_attributable_to_owners_of_parent": 20_888.0}
    return pd.DataFrame([{**base, **cols}])


class TestContinuingNetMargin:
    def test_the_owners_discontinued_result_is_taken_out(self, r11):
        """Essity 2024: 20,888 - 8,919 = 11,969, the continuing line Essity prints."""
        r = r11.compute_ratios(wide(income_from_discontinued_operations_attributable_to_owner_etc=8_919.0))
        assert r["net_margin"].iloc[0] == pytest.approx(100 * 11_969.0 / 145_546.0)
        assert r["_net_basis"].iloc[0] == CONTINUING

    def test_a_continuing_loss_shows_as_a_loss(self, r11):
        """Kering 2025: owners' profit 72M of which 101M from discontinued operations."""
        r = r11.compute_ratios(wide(profit_loss_attributable_to_owners_of_parent=72.0,
                                    profit_loss_from_discontinued_operations_attributable_to_ord_etc=101.0))
        assert r["net_margin"].iloc[0] == pytest.approx(100 * -29.0 / 145_546.0)

    def test_the_owners_share_derives_from_total_minus_non_controlling(self, r11):
        r = r11.compute_ratios(wide(profit_loss_from_discontinued_operations=1_000.0,
                                    profit_loss_from_discontinued_operations_attributable_to__etc=100.0))
        assert r["net_margin"].iloc[0] == pytest.approx(100 * (20_888.0 - 900.0) / 145_546.0)

    def test_an_unsplit_discontinued_total_leaves_net_margin_not_available(self, r11):
        """Amplifon 2021 / Schneider 2019: only the total is tagged and both have non-controlling interests, so the
        owners' share is unknown - blank, never assumed to be all the owners'."""
        r = r11.compute_ratios(wide(profit_loss_from_discontinued_operations=-5.755))
        assert pd.isna(r["net_margin"].iloc[0])
        assert r["_net_basis"].iloc[0] == ""

    def test_the_blank_says_why(self, r11):
        """Stored in ratio.note, so the dashboard shows it as blank on purpose, never as missing data."""
        r = r11.compute_ratios(pd.concat([wide(profit_loss_from_discontinued_operations=-5.755),
                                          wide(year=2025)], ignore_index=True))
        notes = r11.net_margin_notes(r)
        assert list(notes) == [(1, 2024, "net_margin")]
        assert "IFRS 5.33(d)" in notes[(1, 2024, "net_margin")] and "2024" in notes[(1, 2024, "net_margin")]
        assert r11.net_margin_notes(r.drop(columns=["_net_unsplit"])) == {}

    def test_a_zero_discontinued_line_changes_nothing(self, r11):
        r = r11.compute_ratios(wide(profit_loss_from_discontinued_operations=0.0))
        assert r["net_margin"].iloc[0] == pytest.approx(100 * 20_888.0 / 145_546.0)
        assert r["_net_basis"].iloc[0] == ""

    def test_no_discontinued_line_is_the_ordinary_case(self, r11):
        r = r11.compute_ratios(wide())
        assert r["net_margin"].iloc[0] == pytest.approx(100 * 20_888.0 / 145_546.0)
        assert r["_net_basis"].iloc[0] == ""

    def test_roe_keeps_the_whole_owners_profit(self, r11):
        """ROE is the owners' return on the owners' equity, discontinued result included."""
        r = r11.compute_ratios(wide(income_from_discontinued_operations_attributable_to_owner_etc=8_919.0,
                                    equity_attributable_to_owners_of_parent=100_000.0))
        assert r["roe"].iloc[0] == pytest.approx(100 * 20_888.0 / 100_000.0)

    def test_only_net_margin_carries_the_basis_source(self, r11):
        row = {"_net_basis": CONTINUING}
        assert r11.source_concepts_for("net_margin", row) == [CONTINUING]
        assert r11.source_concepts_for("operating_margin", row) is None
        assert r11.source_concepts_for("net_margin", {"_net_basis": ""}) is None


class TestDashboardCaption:
    """webapp/app.py is a Streamlit script (top-level code runs on import): pull the helper out of its source."""

    @pytest.fixture
    def caption(self):
        tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
        parts = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "net_basis_caption"]
        assert len(parts) == 1
        ns = {"pd": pd}
        exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
        return ns["net_basis_caption"]

    def test_names_the_years_on_the_continuing_basis(self, caption):
        df = pd.DataFrame({"ratio_name": ["net_margin", "net_margin", "net_margin"], "year": [2025, 2023, 2024],
                           "source_concepts": [[CONTINUING], None, [CONTINUING]]})
        text_ = caption(df)
        assert "2024, 2025" in text_ and "2023" not in text_ and "IFRS 5" in text_

    def test_no_caption_without_a_discontinued_operation_or_the_column(self, caption):
        assert caption(pd.DataFrame({"ratio_name": ["net_margin"], "year": [2025], "source_concepts": [None]})) is None
        assert caption(pd.DataFrame({"ratio_name": ["net_margin"], "year": [2025], "value": [1.0]})) is None

    def test_other_ratios_never_decide(self, caption):
        df = pd.DataFrame({"ratio_name": ["roe"], "year": [2025], "source_concepts": [[CONTINUING]]})
        assert caption(df) is None
