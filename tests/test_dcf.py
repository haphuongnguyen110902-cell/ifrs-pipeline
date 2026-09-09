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


class TestConvertBaseToEur:
    """Real bug found via a live run: Essity (reports in SEK) printed a
    "EUR 884bn" Enterprise Value - actually its real figure IN SEK,
    mislabeled as EUR, because base-year financials (native currency)
    were combined with a live-converted EUR market cap for the WACC
    weights without ever converting the financials themselves. Essity
    was the first non-EUR company to reach the DCF stage - the bug had
    been latent since 22_dcf.py was written, invisible until a base-year
    fix elsewhere made a SEK-reporting company's DCF actually runnable."""

    @pytest.fixture
    def fx_lookup(self):
        return {("SEK", 2024): {"avg_rate": 11.0, "closing_rate": 11.2}}

    @pytest.fixture
    def base(self):
        return {
            "base_year": 2024, "revenue": 11000.0, "ebit": 1100.0,
            "net_debt": 3360.0, "capex": 550.0, "da_total": 440.0,
            "receivables": 1120.0, "inventory": 896.0, "payables": 672.0,
            "gross_margin": 40.0, "operating_margin": 10.0, "tax_rate": 25.0,
            "dso": 45.0, "dio": 60.0, "dpo": 40.0, "payout_ratio": 0.5,
            "history_years": [2023, 2024], "history_revenue": [9900.0, 11000.0],
        }

    def test_flow_items_use_average_rate(self, dcf, base, fx_lookup):
        result = dcf.convert_base_to_eur(base, "SEK", fx_lookup)
        assert result["revenue"] == pytest.approx(11000.0 / 11.0)
        assert result["ebit"] == pytest.approx(1100.0 / 11.0)
        assert result["capex"] == pytest.approx(550.0 / 11.0)
        assert result["da_total"] == pytest.approx(440.0 / 11.0)

    def test_balance_sheet_items_use_closing_rate(self, dcf, base, fx_lookup):
        result = dcf.convert_base_to_eur(base, "SEK", fx_lookup)
        assert result["net_debt"] == pytest.approx(3360.0 / 11.2)
        assert result["receivables"] == pytest.approx(1120.0 / 11.2)
        assert result["inventory"] == pytest.approx(896.0 / 11.2)
        assert result["payables"] == pytest.approx(672.0 / 11.2)

    def test_history_revenue_converted_per_year(self, dcf, base):
        """Each historical year's revenue must use THAT year's own rate,
        not the base year's - a moving FX rate genuinely changes the
        EUR-denominated growth rate, which is correct IAS 21 behavior,
        not something to flatten away."""
        fx_lookup = {("SEK", 2023): {"avg_rate": 10.5, "closing_rate": 10.6},
                     ("SEK", 2024): {"avg_rate": 11.0, "closing_rate": 11.2}}
        result = dcf.convert_base_to_eur(base, "SEK", fx_lookup)
        assert result["history_revenue"][0] == pytest.approx(9900.0 / 10.5)
        assert result["history_revenue"][1] == pytest.approx(11000.0 / 11.0)

    def test_ratios_left_untouched(self, dcf, base, fx_lookup):
        """Margins, day-count ratios, tax rate and payout ratio are
        currency-neutral by construction - converting them would be
        wrong, not just unnecessary."""
        result = dcf.convert_base_to_eur(base, "SEK", fx_lookup)
        for field in ("gross_margin", "operating_margin", "tax_rate",
                      "dso", "dio", "dpo", "payout_ratio"):
            assert result[field] == base[field]

    def test_eur_reporting_company_passes_through_unchanged(self, dcf, base):
        """Guards the call site, not this function: 22_dcf.py only calls
        convert_base_to_eur() when quote_ccy != "EUR" - but to_eur()
        itself is also a pass-through for EUR, so calling it accidentally
        for a EUR company must never corrupt anything either."""
        result = dcf.convert_base_to_eur(base, "EUR", {})
        assert result["revenue"] == base["revenue"]
        assert result["net_debt"] == base["net_debt"]


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
