"""
tests/test_by_nature.py

IAS 1.99 lets a company classify expenses by nature instead of by function; the function method must show cost of
sales (IAS 1.103), the nature method has none - IAS 2.39 has such a company disclose raw materials, labour and other
costs INSTEAD of the cost of inventories sold. Heineken, Amplifon and Shell report this way (checked in their FY2025
reports: none prints cost of sales, and none discloses the IAS 2.36(d) amount in its inventories note).

Before: gross margin, DIO, DPO and the cash conversion cycle were silently blank for them, and the 3-statement model
and DCF refused them ("missing required inputs: gross_margin, dio, dpo"). Now:
- those four ratios are blank ON PURPOSE, with the IAS 1.99 reason stored in ratio.note;
- the 3-statement model carries working capital from the printed balances as shares of revenue, which needs no cost
  of sales. For every function-method company that is the same projection as before (checked live: the 12 stored
  projections recomputed to within 1e-14), because inventory = revenue x (1 - gross margin) x DIO / 365 is
  revenue x inventory / revenue when margin and days are held flat.
No database.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


@pytest.fixture(scope="module")
def tsm(load_script):
    return load_script("21_three_statement_model.py")


def wide(**cols):
    base = {"company": "X", "company_id": 7, "year": 2025, "revenue": 34_257.0,
            "profit_loss_from_operating_activities": 3_406.0, "current_trade_receivables": 4_500.0,
            "inventories": 3_263.0, "trade_and_other_current_payables": 9_560.0}
    return pd.DataFrame([{**base, **cols}])


class TestRatios:
    def test_no_cost_of_sales_means_by_nature(self, r11):
        r = r11.compute_ratios(wide())
        assert bool(r["_by_nature"].iloc[0])
        for name in r11.BY_NATURE_RATIOS:
            assert pd.isna(r[name].iloc[0]), name
        assert r["dso"].iloc[0] == pytest.approx(4_500.0 / 34_257.0 * 365)      # revenue-based: still there

    def test_the_blanks_carry_the_ias_1_99_reason(self, r11):
        notes = r11.by_nature_notes(r11.compute_ratios(wide()))
        assert set(notes) == {(7, 2025, n) for n in r11.BY_NATURE_RATIOS}
        assert all("IAS 1.99" in t for t in notes.values())

    def test_a_function_method_company_is_not_by_nature(self, r11):
        r = r11.compute_ratios(wide(cost_of_sales=-20_000.0))
        assert not bool(r["_by_nature"].iloc[0])
        assert r11.by_nature_notes(r) == {}

    def test_gross_profit_alone_is_enough(self, r11):
        """Essity prints gross profit but never cost of sales - function method."""
        assert not bool(r11.compute_ratios(wide(gross_profit=10_000.0))["_by_nature"].iloc[0])

    def test_the_printed_balances_are_kept(self, r11):
        r = r11.compute_ratios(wide())
        assert (r["_receivables"].iloc[0], r["_inventories"].iloc[0], r["_payables"].iloc[0]) == (4_500.0, 3_263.0, 9_560.0)
        assert {"_receivables", "_inventories", "_payables"} <= set(r11.AS_REPORTED_COLUMNS)


class TestModel:
    DAYS_BASE = {"base_year": 2025, "revenue": 1000.0, "ebit": 150.0, "net_debt": 500.0, "da_total": 40.0,
                 "operating_margin": 15.0, "tax_rate": 25.0, "capex": 50.0, "payout_ratio": 0.3,
                 "gross_margin": 70.0, "dso": 45.0, "dio": 60.0, "dpo": 90.0,
                 "receivables": 1000 * 45 / 365, "inventory": 300 * 60 / 365, "payables": 300 * 90 / 365}

    def shares_base(self):
        b = {k: v for k, v in self.DAYS_BASE.items() if k not in ("gross_margin", "dso", "dio", "dpo")}
        b.update(receivables_share=b["receivables"] / 1000, inventory_share=b["inventory"] / 1000,
                 payables_share=b["payables"] / 1000)
        return b

    def test_shares_and_days_give_the_same_projection(self, tsm):
        by_days = tsm.project(self.DAYS_BASE, 5, 0.05, 0.04)
        by_shares = tsm.project(self.shares_base(), 5, 0.05, 0.04)
        pd.testing.assert_frame_equal(by_days, by_shares)

    def test_a_by_nature_base_needs_no_gross_margin(self, tsm):
        """Heineken FY2025: no cost of sales anywhere - the model still runs from its printed balances."""
        proj = tsm.project(self.shares_base(), 5, 0.05, 0.04)
        assert len(proj) == 5 and proj["fcf"].notna().all()

    def test_working_capital_grows_with_revenue(self, tsm):
        b = self.shares_base()
        row = tsm.project(b, 1, 0.10, 0.04).iloc[0]
        nwc0 = b["receivables"] + b["inventory"] - b["payables"]
        assert row["delta_wc"] == pytest.approx(nwc0 * 0.10)
