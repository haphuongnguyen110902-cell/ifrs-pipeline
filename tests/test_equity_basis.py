"""
tests/test_equity_basis.py

ROIC and ROE need equity attributable to owners of the parent (ifrs-full:EquityAttributableToOwnersOfParent) - a
DIFFERENT tag from total equity (ifrs-full:Equity). ASM International only tags the total: its balance sheet, five-year
summary and statement of changes in equity print one "Equity" line with no split into owners' equity and non-controlling
interests. Because a reviewed check (data/mappings/reviewed_note_figures.yaml's asm_no_noncontrolling_interests_* entries)
proves ASM's non-controlling interests are exactly zero - not merely untagged - total equity IS, by definition, equity
attributable to owners of the parent, and 11_ratio_engine.py's compute_ratios() now uses it as such. A company whose
non-controlling interests are simply missing (not proven zero) stays blank, exactly as before. No database.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide(**cols):
    base = {"company": "X", "company_id": 1, "year": 2025, "revenue": 1_000.0, "cost_of_sales": -600.0,
            "profit_loss_from_operating_activities": 100.0, "profit_loss_before_tax": 90.0,
            "income_tax_expense_continuing_operations": 20.0, "profit_loss_attributable_to_owners_of_parent": 60.0,
            "longterm_borrowings": 0.0, "shortterm_borrowings": 0.0, "cash_and_cash_equivalents": 50.0}
    return pd.DataFrame([{**base, **cols}])


class TestBasisRecorded:
    def test_a_proven_zero_nci_lets_total_equity_stand_in_for_the_parent_split(self, r11):
        r = r11.compute_ratios(wide(equity=4_005.8, noncontrolling_interests=0.0))
        assert r["_equity_basis"].iloc[0] == "equity (no non-controlling interests)"
        assert r["roe"].iloc[0] == pytest.approx(100 * 60.0 / 4_005.8)
        assert not pd.isna(r["roic"].iloc[0])

    def test_a_printed_split_is_preferred_and_carries_no_basis(self, r11):
        r = r11.compute_ratios(wide(equity_attributable_to_owners_of_parent=500.0,
                                    noncontrolling_interests=0.0, equity=500.0))
        assert r["_equity_basis"].iloc[0] == "" and r["roe"].iloc[0] == pytest.approx(12.0)

    def test_missing_nci_is_not_proven_zero_so_it_stays_blank(self, r11):
        """The pre-existing case: no NCI tag at all (most companies) - equity_attributable_to_owners_of_parent
        stays NaN, ROIC/ROE stay blank, exactly as before this fix."""
        r = r11.compute_ratios(wide(equity=4_005.8))
        assert r["_equity_basis"].iloc[0] == "" and pd.isna(r["roe"].iloc[0])

    def test_a_nonzero_nci_is_never_substituted(self, r11):
        r = r11.compute_ratios(wide(equity=4_005.8, noncontrolling_interests=12.0))
        assert r["_equity_basis"].iloc[0] == "" and pd.isna(r["roe"].iloc[0])

    def test_rows_are_independent(self, r11):
        w = pd.DataFrame([
            {**wide(equity=100.0, noncontrolling_interests=0.0).iloc[0].to_dict(), "year": 2024},
            {**wide().iloc[0].to_dict(), "year": 2025},
        ])
        assert r11.compute_ratios(w)["_equity_basis"].tolist() == ["equity (no non-controlling interests)", ""]

    def test_only_roic_and_roe_carry_the_equity_basis_source(self, r11):
        row = {"_equity_basis": "equity (no non-controlling interests)"}
        assert r11.source_concepts_for("roic", row) == ["equity (no non-controlling interests)"]
        assert r11.source_concepts_for("roe", row) == ["equity (no non-controlling interests)"]
        assert r11.source_concepts_for("dso", row) is None and r11.source_concepts_for("dpo", row) is None
        assert r11.source_concepts_for("roe", {"_equity_basis": ""}) is None
        assert r11.source_concepts_for("roe", {}) is None


class _Conn:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))

    def commit(self):
        pass


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    def connect(self):
        return self.conn


class TestSaveToDb:
    def test_source_concepts_reach_the_insert_for_roic_and_roe_only(self, r11):
        engine = _Engine()
        ratios = r11.compute_ratios(wide(equity=4_005.8, noncontrolling_interests=0.0))
        r11.save_to_db(engine, ratios, {})
        ins = {p["rn"]: p for s, p in engine.conn.calls if "INSERT INTO ratio" in s}
        assert ins["roic"]["src"] == ["equity (no non-controlling interests)"]
        assert ins["roe"]["src"] == ["equity (no non-controlling interests)"]
        assert ins["dso"]["src"] is None and ins["gross_margin"]["src"] is None

    def test_the_ordinary_case_stores_no_source(self, r11):
        engine = _Engine()
        r11.save_to_db(engine, r11.compute_ratios(wide()), {})       # no NCI proof -> roic/roe NaN, no basis
        ins = {p["rn"]: p for s, p in engine.conn.calls if "INSERT INTO ratio" in s}
        assert ins["roe"]["val"] is None and ins["roe"]["src"] is None


class TestDashboardCaption:
    """webapp/app.py is a Streamlit script (top-level code runs on import): pull the helper out of its source."""

    @pytest.fixture
    def caption(self):
        tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
        parts = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "equity_basis_caption"]
        assert len(parts) == 1
        ns = {"pd": pd}
        exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
        return ns["equity_basis_caption"]

    @staticmethod
    def frame(sources, ratio_names=None):
        names = ratio_names or (["roic", "roe"] * len(sources))[:len(sources)]
        return pd.DataFrame({"ratio_name": names, "source_concepts": sources})

    def test_a_company_relying_on_the_proven_zero_nci_gets_the_caption(self, caption):
        text_ = caption(self.frame([["equity (no non-controlling interests)"], ["equity (no non-controlling interests)"]]))
        assert "no non-controlling interests" in text_

    def test_no_caption_on_a_database_without_the_column_or_with_nulls(self, caption):
        assert caption(pd.DataFrame({"ratio_name": ["roic"], "value": [1.0]})) is None
        assert caption(self.frame([None, None])) is None

    def test_only_roic_and_roe_rows_decide(self, caption):
        df = pd.DataFrame({"ratio_name": ["dpo"], "source_concepts": [["equity (no non-controlling interests)"]]})
        assert caption(df) is None
