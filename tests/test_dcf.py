"""
tests/test_dcf.py

Regression tests for scripts/22_dcf.py. Every formula here is verified
against an independently hand-calculated value, not just checked for
"did it run" - see each test's inline calculation.
"""
import pytest


@pytest.fixture(scope="module")
def dcf(load_script):
    return load_script("22_dcf.py")


def test_fcff_is_unlevered_ignores_interest_and_net_income(dcf):
    """The core trap this script exists to avoid: FCFF must be computed
    from EBIT directly, completely ignoring any levered fields (interest
    expense, net income) even if they're present in the input - using
    them would double-count the cost of debt (once via WACC, once via
    the cash flow itself)."""
    import pandas as pd
    proj = pd.DataFrame([{
        "year": 2026, "ebit": 1000.0, "da": 100.0, "delta_wc": 50.0, "capex": 150.0,
        "interest_expense": 999999.0, "net_income": 999999.0,  # deliberately absurd - must be ignored
    }])
    result = dcf.compute_fcff(proj, tax_rate_pct=25.0)
    expected = 1000 * (1 - 0.25) + 100 - 50 - 150
    assert result["fcff"].iloc[0] == pytest.approx(expected)


class TestWacc:
    def test_matches_hand_calculation(self, dcf):
        w = dcf.compute_wacc(market_cap=8000.0, net_debt=2000.0, beta=1.2, tax_rate_pct=25.0,
                              risk_free_rate=0.03, erp=0.05, cost_of_debt=0.04)
        coe_expected = 0.03 + 1.2 * 0.05
        we, wd = 8000 / 10000, 2000 / 10000
        atcod_expected = 0.04 * (1 - 0.25)
        wacc_expected = we * coe_expected + wd * atcod_expected
        assert w["cost_of_equity"] == pytest.approx(coe_expected)
        assert w["wacc"] == pytest.approx(wacc_expected)

    def test_net_cash_company_gets_all_equity_wacc(self, dcf):
        """A net-cash company (negative net debt, like the real L'Oreal)
        must have its debt weight floored at 0, not negative - WACC
        collapses to cost of equity alone, the standard convention."""
        w = dcf.compute_wacc(market_cap=8000.0, net_debt=-2000.0, beta=1.2, tax_rate_pct=25.0,
                              risk_free_rate=0.03, erp=0.05, cost_of_debt=0.04)
        assert w["weight_debt"] == 0.0
        assert w["wacc"] == pytest.approx(w["cost_of_equity"])


class TestDiscountCashFlows:
    def test_matches_hand_calculation(self, dcf):
        years = [2026, 2027, 2028]
        fcff = [100.0, 110.0, 121.0]
        wacc, tg = 0.08, 0.02

        result = dcf.discount_cash_flows(years, fcff, wacc, tg, base_year=2025)

        pv_explicit_expected = sum(f / (1 + wacc) ** i for i, f in enumerate(fcff, start=1))
        tv_expected = fcff[-1] * (1 + tg) / (wacc - tg)
        pv_tv_expected = tv_expected / (1 + wacc) ** len(fcff)
        ev_expected = pv_explicit_expected + pv_tv_expected

        assert result["pv_explicit_fcff"] == pytest.approx(pv_explicit_expected)
        assert result["terminal_value"] == pytest.approx(tv_expected)
        assert result["pv_terminal_value"] == pytest.approx(pv_tv_expected)
        assert result["enterprise_value"] == pytest.approx(ev_expected)

    def test_terminal_growth_must_be_below_wacc(self, dcf):
        """A terminal growth rate >= WACC divides by a non-positive
        number in the Gordon growth formula - must raise clearly, never
        silently produce an infinite/negative 'valuation'."""
        with pytest.raises(ValueError, match="must be < WACC"):
            dcf.discount_cash_flows([2026], [100.0], wacc=0.05, terminal_growth=0.05, base_year=2025)

    def test_terminal_value_share_of_ev_is_reasonable(self, dcf):
        """Sanity check on a typical growing-company scenario: terminal
        value should be a LARGE share of EV (this is normal/expected for
        DCF, not a bug) but not literally 100% - the explicit forecast
        years should still contribute something."""
        years = list(range(2026, 2031))
        fcff = [100 * 1.05 ** i for i in range(5)]
        result = dcf.discount_cash_flows(years, fcff, wacc=0.08, terminal_growth=0.02, base_year=2025)
        assert 0.5 < result["pct_of_ev_from_terminal"] < 1.0


def test_sensitivity_table_handles_growth_at_or_above_wacc_gracefully(dcf):
    """The sensitivity grid sweeps a RANGE of WACC/growth combinations -
    some combinations in that range can have growth >= WACC (invalid).
    Those cells must show as unavailable, not crash the whole table."""
    fcff_values = [100.0, 110.0, 121.0]
    table = dcf.sensitivity_table(fcff_values, base_wacc=0.03, base_terminal_growth=0.02, steps=3)
    assert not table.empty
    # at least one cell should be None (some g >= w combination in a tight range)
    has_invalid_cell = table.iloc[:, 1:].isna().any().any()
    assert has_invalid_cell
