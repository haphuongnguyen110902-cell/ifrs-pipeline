"""
tests/test_three_statement_model.py

Regression tests for scripts/21_three_statement_model.py. The
circularity-solving mechanism (interest expense <-> debt balance) is
the single most error-prone part of a linked 3-statement model, so it's
verified against an INDEPENDENTLY DERIVED closed-form algebraic
solution, not just checked for "did it converge to something" -
see the module docstring's derivation.
"""
import math

import pytest


@pytest.fixture(scope="module")
def tsm(load_script):
    return load_script("21_three_statement_model.py")


class TestCircularitySolver:
    def test_converges_to_the_closed_form_algebraic_solution(self, tsm):
        """Closed form (derived independently, not copied from the
        implementation): with K = ebit*(1-t) + da - delta_wc - capex,
        IE = r*(prev_debt - K/2) / (1 - r*(1-t)/2)."""
        prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r = (
            1000.0, 500.0, 100.0, 50.0, 150.0, 25.0, 0.04)
        t = tax_rate_pct / 100

        ie, new_debt, ni, cfo, fcf, div = tsm.solve_circularity(
            prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r)

        k = ebit * (1 - t) + da - delta_wc - capex
        ie_closed_form = r * (prev_debt - k / 2) / (1 - r * (1 - t) / 2)

        assert ie == pytest.approx(ie_closed_form, abs=1e-4)
        assert div == 0.0  # default payout_ratio=0

    def test_converged_state_is_self_consistent(self, tsm):
        """Feeding the converged output back through the same formula
        must reproduce the same interest expense - if it didn't, the
        solver would have stopped before actually converging."""
        prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r = (
            800.0, 300.0, 60.0, -20.0, 80.0, 28.0, 0.035)
        ie, new_debt, ni, cfo, fcf, div = tsm.solve_circularity(
            prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r)

        t = tax_rate_pct / 100
        ebt_check = ebit - ie
        ni_check = ebt_check - ebt_check * t
        fcf_check = (ni_check + da - delta_wc) - capex
        new_debt_check = prev_debt - fcf_check
        ie_recomputed = r * (prev_debt + new_debt_check) / 2

        assert ie_recomputed == pytest.approx(ie, abs=1e-4)
        assert new_debt == pytest.approx(new_debt_check, abs=1e-4)

    def test_zero_interest_rate_gives_zero_interest_expense(self, tsm):
        """Sanity check: with no interest rate, there's nothing to solve
        circularly - interest expense must be exactly zero, not some
        near-zero float-noise artifact of the iteration."""
        ie, new_debt, ni, cfo, fcf, div = tsm.solve_circularity(
            1000.0, 500.0, 100.0, 50.0, 150.0, 25.0, interest_rate=0.0)
        assert ie == 0.0

    def test_negative_debt_balance_net_cash_still_solves(self, tsm):
        """A net-cash company (like the real L'Oreal, whose _net_debt is
        negative) must still solve cleanly - no floor/edge-case crash
        for negative starting debt."""
        ie, new_debt, ni, cfo, fcf, div = tsm.solve_circularity(
            -300.0, 400.0, 80.0, 30.0, 100.0, 24.0, 0.04)
        assert math.isfinite(ie)
        assert math.isfinite(new_debt)

    def test_payout_ratio_matches_generalized_closed_form(self, tsm):
        """Found via a real run: with payout_ratio=0 (the pre-fix
        default), L'Oreal's projected net debt reached -29bn EUR net
        cash after 5 years - correct math, unrealistic economics for any
        dividend-paying company, since 100% of FCF silently piled up as
        debt paydown/cash forever. Verifies the GENERALIZED closed form:
        K' = ebit*(1-t)*(1-payout) + da - delta_wc - capex,
        IE = r*(prev_debt - K'/2) / (1 - r*(1-t)*(1-payout)/2)."""
        prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r, payout = (
            1000.0, 500.0, 100.0, 50.0, 150.0, 25.0, 0.04, 0.6)
        t = tax_rate_pct / 100

        ie, new_debt, ni, cfo, fcf, div = tsm.solve_circularity(
            prev_debt, ebit, da, delta_wc, capex, tax_rate_pct, r, payout_ratio=payout)

        k_prime = ebit * (1 - t) * (1 - payout) + da - delta_wc - capex
        ie_closed_form = r * (prev_debt - k_prime / 2) / (1 - r * (1 - t) * (1 - payout) / 2)
        assert ie == pytest.approx(ie_closed_form, abs=1e-4)

        ebt = ebit - ie
        ni_check = ebt - ebt * t
        assert div == pytest.approx(ni_check * payout, abs=1e-4)

    def test_higher_payout_ratio_slows_debt_paydown(self, tsm):
        """More cash going to dividends means less available for debt
        paydown - new_debt after 1 year should be HIGHER (less paid
        down) with a higher payout ratio, all else equal."""
        args = (1000.0, 500.0, 100.0, 50.0, 150.0, 25.0, 0.04)
        _, debt_low_payout, *_ = tsm.solve_circularity(*args, payout_ratio=0.1)
        _, debt_high_payout, *_ = tsm.solve_circularity(*args, payout_ratio=0.8)
        assert debt_high_payout > debt_low_payout


class TestMultiYearProjection:
    @pytest.fixture
    def base(self):
        return {
            "company": "TestCo", "base_year": 2024,
            "revenue": 1000.0, "ebit": 150.0, "net_debt": 500.0, "da_total": 40.0,
            "gross_margin": 70.0, "operating_margin": 15.0, "tax_rate": 25.0,
            "dso": 45.0, "dio": 60.0, "dpo": 90.0,
            "capex": 50.0, "capex_is_fallback": False,
            "receivables": 1000 * 45 / 365, "inventory": 300 * 60 / 365, "payables": 300 * 90 / 365,
            "history_years": [2022, 2023, 2024], "history_revenue": [900, 950, 1000],
        }

    def test_year_one_matches_hand_calculation(self, tsm, base):
        proj = tsm.project(base, years=1, growth=0.05, interest_rate=0.04)
        row = proj.iloc[0]

        assert row["revenue"] == pytest.approx(1050.0)
        assert row["ebit"] == pytest.approx(157.5)
        assert row["da"] == pytest.approx(42.0)  # 40/1000 * 1050
        assert row["capex"] == pytest.approx(52.5)  # 50/1000 * 1050

        cogs1 = 1050 * (1 - 0.70)
        recv1, inv1, pay1 = 1050 * 45 / 365, cogs1 * 60 / 365, cogs1 * 90 / 365
        expected_dwc = (recv1 + inv1 - pay1) - (base["receivables"] + base["inventory"] - base["payables"])
        assert row["delta_wc"] == pytest.approx(expected_dwc)

    def test_debt_paydown_compounds_correctly_across_years(self, tsm, base):
        """Year 2's STARTING debt must be year 1's ENDING debt, not the
        base year's debt again - the whole point of a linked model is
        that each year's balance sheet carries forward."""
        proj = tsm.project(base, years=3, growth=0.05, interest_rate=0.04)
        assert len(proj) == 3
        # with positive FCF every year (profitable, growing company),
        # debt should be monotonically decreasing
        debts = proj["net_debt_end"].tolist()
        assert debts[0] > debts[1] > debts[2]

    def test_revenue_compounds_at_the_growth_rate(self, tsm, base):
        proj = tsm.project(base, years=3, growth=0.05, interest_rate=0.04)
        assert proj.iloc[2]["revenue"] == pytest.approx(1000 * 1.05 ** 3)


def test_default_growth_rate_uses_cagr_from_history(tsm):
    base = {"history_years": [2020, 2021, 2022, 2023], "history_revenue": [100, 110, 121, 133.1]}
    growth = tsm.default_growth_rate(base)
    assert growth == pytest.approx(0.10, abs=1e-3)  # perfect 10% CAGR series


def test_default_growth_rate_falls_back_with_insufficient_history(tsm):
    base = {"history_years": [2023], "history_revenue": [100]}
    growth = tsm.default_growth_rate(base)
    assert growth == 0.03  # documented fallback, not a crash


class TestSelectBaseYearRow:
    """Real bug found via a live run: Pernod Ricard's numerically latest
    year (2025) was completely empty (every ratio NaN - likely an
    incomplete filing given its June 30 fiscal year end), while 2024 had
    a full, real set of ratios. fetch_base_year() used to blindly take
    idxmax() on `year` and silently report EVERYTHING as "missing" -
    select_base_year_row() picks the latest year that actually has data
    instead."""

    def test_skips_an_empty_latest_year_for_a_populated_prior_year(self, tsm):
        import pandas as pd
        ratios = pd.DataFrame([
            {"year": 2023, "_revenue": 1000.0, "_ebit": 100.0},
            {"year": 2024, "_revenue": 1100.0, "_ebit": 120.0},
            {"year": 2025, "_revenue": None, "_ebit": None},  # the Pernod Ricard case
        ])
        row = tsm.select_base_year_row(ratios)
        assert row["year"] == 2024

    def test_normal_case_still_picks_the_true_latest_year(self, tsm):
        """Guards against over-firing: a company with real data in its
        latest year must still get that year, not an older one."""
        import pandas as pd
        ratios = pd.DataFrame([
            {"year": 2023, "_revenue": 1000.0, "_ebit": 100.0},
            {"year": 2024, "_revenue": 1100.0, "_ebit": 120.0},
        ])
        row = tsm.select_base_year_row(ratios)
        assert row["year"] == 2024

    def test_falls_back_to_latest_year_when_no_year_has_data(self, tsm):
        """A company missing _revenue/_ebit in EVERY year has a real data
        gap, not a stale-year problem - falls back to the old idxmax()
        behavior so the caller's "missing required inputs" error still
        surfaces honestly, rather than this function silently picking an
        arbitrary equally-empty year."""
        import pandas as pd
        ratios = pd.DataFrame([
            {"year": 2023, "_revenue": None, "_ebit": None},
            {"year": 2024, "_revenue": None, "_ebit": None},
        ])
        row = tsm.select_base_year_row(ratios)
        assert row["year"] == 2024  # still the numerically latest, per old behavior
