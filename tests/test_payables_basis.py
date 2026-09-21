"""
tests/test_payables_basis.py

DPO and the cash conversion cycle are not built on one perimeter across the 16 companies. Read from each company's own
balance-sheet row: L'Oreal, Danone, Pernod Ricard, EssilorLuxottica, Amplifon, Essity, ASM print trade payables under the
IFRS "trade payables" element; LVMH, Kering and Moncler print trade payables but tag them with the broader IFRS element
"trade AND other payables"; Heineken, Shell (and Adyen) print "Trade and other payables" and Schneider "Fournisseurs et
dettes d'exploitation" on that same element. The tag cannot tell which is which, so the engine records the line it read
(ratio.source_concepts) and the dashboard says the figure may not be comparable when it is the broader element. No database.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
TRADE = "trade_and_other_current_payables_to_trade_suppliers"
BROADER = "trade_and_other_current_payables"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def wide(**cols):
    base = {"company": "X", "company_id": 1, "year": 2025, "revenue": 1_000.0, "cost_of_sales": -600.0,
            "profit_loss_from_operating_activities": 100.0}
    return pd.DataFrame([{**base, **cols}])


class TestBasisRecorded:
    def test_trade_payables_line_is_preferred_and_named(self, r11):
        r = r11.compute_ratios(wide(**{TRADE: 60.0, BROADER: 999.0}))
        assert r["_payables_basis"].iloc[0] == TRADE and r["dpo"].iloc[0] == pytest.approx(36.5)

    def test_the_broader_line_is_named_when_it_is_all_there_is(self, r11):
        r = r11.compute_ratios(wide(**{BROADER: 60.0}))
        assert r["_payables_basis"].iloc[0] == BROADER

    def test_the_weakest_fallback_and_nothing(self, r11):
        assert r11.compute_ratios(wide(other_current_payables=60.0))["_payables_basis"].iloc[0] == "other_current_payables"
        assert r11.compute_ratios(wide())["_payables_basis"].iloc[0] == ""

    def test_rows_are_independent(self, r11):
        w = pd.DataFrame([{"company": "X", "company_id": 1, "year": 2024, TRADE: 1.0},
                          {"company": "X", "company_id": 1, "year": 2025, BROADER: 2.0}])
        assert r11.compute_ratios(w)["_payables_basis"].tolist() == [TRADE, BROADER]

    def test_only_dpo_and_ccc_carry_a_source(self, r11):
        row = {"_payables_basis": BROADER}
        assert r11.source_concepts_for("dpo", row) == [BROADER] and r11.source_concepts_for("ccc", row) == [BROADER]
        assert r11.source_concepts_for("dso", row) is None and r11.source_concepts_for("roe", row) is None
        assert r11.source_concepts_for("dpo", {"_payables_basis": ""}) is None and r11.source_concepts_for("dpo", {}) is None


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
    def test_source_concepts_reach_the_insert_for_dpo_and_ccc_only(self, r11):
        engine = _Engine()
        r11.save_to_db(engine, r11.compute_ratios(wide(**{BROADER: 60.0, "current_trade_receivables": 150.0,
                                                          "inventories": 90.0})), {})
        ins = {p["rn"]: p for s, p in engine.conn.calls if "INSERT INTO ratio" in s}
        assert ins["dpo"]["src"] == [BROADER] and ins["ccc"]["src"] == [BROADER]
        assert ins["dso"]["src"] is None and ins["gross_margin"]["src"] is None
        assert any("source_concepts = EXCLUDED.source_concepts" in s for s, _ in engine.conn.calls)

    def test_a_blank_ratio_stores_no_source(self, r11):
        engine = _Engine()
        r11.save_to_db(engine, r11.compute_ratios(wide()), {})       # no payables -> dpo NaN
        ins = {p["rn"]: p for s, p in engine.conn.calls if "INSERT INTO ratio" in s}
        assert ins["dpo"]["val"] is None and ins["dpo"]["src"] is None


class TestDashboardCaption:
    """webapp/app.py is a Streamlit script (top-level code runs on import): pull the helper out of its source."""

    @pytest.fixture
    def caption(self):
        tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
        parts = [n for n in tree.body
                 if (isinstance(n, ast.FunctionDef) and n.name == "payables_basis_caption")
                 or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "BROADER_PAYABLES" for t in n.targets))]
        assert len(parts) == 2
        ns = {"pd": pd}
        exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
        return ns["payables_basis_caption"]

    @staticmethod
    def frame(sources):
        return pd.DataFrame({"ratio_name": ["dpo"] * len(sources), "source_concepts": sources})

    def test_a_company_on_the_broader_line_gets_the_caption(self, caption):
        text_ = caption(self.frame([[BROADER], [BROADER]]))
        assert "trade and other payables" in text_ and "may not be comparable" in text_

    def test_the_weakest_fallback_gets_it_too(self, caption):
        assert caption(self.frame([["other_current_payables"]])) is not None

    def test_a_company_on_trade_payables_gets_none(self, caption):
        assert caption(self.frame([[TRADE], [TRADE]])) is None

    def test_no_caption_on_a_database_without_the_column_or_with_nulls(self, caption):
        assert caption(pd.DataFrame({"ratio_name": ["dpo"], "value": [1.0]})) is None
        assert caption(self.frame([None, None])) is None

    def test_only_the_dpo_rows_decide(self, caption):
        df = pd.DataFrame({"ratio_name": ["dpo", "ccc"], "source_concepts": [[TRADE], [BROADER]]})
        assert caption(df) is None
