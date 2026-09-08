"""
tests/test_forecasting.py

Regression tests for scripts/16_forecasting.py.
"""
import math

import pytest


@pytest.fixture(scope="module")
def f16(load_script):
    return load_script("16_forecasting.py")


class TestCagrForecast:
    def test_positive_growth(self, f16):
        # 100 -> 121 over 2 years = 10% CAGR
        cagr, forecasts = f16.cagr_forecast([2022, 2023, 2024], [100, 110, 121], horizon=1)
        assert cagr == pytest.approx(0.10, abs=1e-6)
        assert forecasts[2025] == pytest.approx(121 * 1.10)

    def test_negative_endpoint_is_undefined_not_guessed(self, f16):
        """A margin/ratio that goes negative has no meaningful CAGR -
        must return None, never fabricate a number."""
        cagr, forecasts = f16.cagr_forecast([2022, 2023], [10, -5], horizon=1)
        assert cagr is None
        assert forecasts == {}

    def test_zero_first_value_is_undefined(self, f16):
        cagr, forecasts = f16.cagr_forecast([2022, 2023], [0, 10], horizon=1)
        assert cagr is None


class TestLinregForecast:
    def test_perfect_line_gives_r_squared_of_one(self, f16):
        years = [2020, 2021, 2022, 2023]
        values = [10, 12, 14, 16]  # exactly slope=2
        slope, intercept, r2, forecasts = f16.linreg_forecast(years, values, horizon=1)
        assert slope == pytest.approx(2.0)
        assert r2 == pytest.approx(1.0, abs=1e-9)
        assert forecasts[2024] == pytest.approx(18.0)

    def test_flat_series_r_squared_is_nan_not_crash(self, f16):
        """A constant series has zero variance to explain - R² is
        undefined (0/0), must return NaN rather than raising."""
        years = [2020, 2021, 2022]
        values = [10, 10, 10]
        _, _, r2, _ = f16.linreg_forecast(years, values, horizon=1)
        assert math.isnan(r2)


class TestFormatValue:
    """Reuses 11_ratio_engine.py's RATIO_META for units instead of
    hardcoding '%' - the bug this guards against: DSO/DIO/DPO/CCC are in
    days, and printing them with a '%' sign is silently wrong, not just
    ugly (same class of bug as the earlier .1% formatting bug, caught
    here by unit-awareness instead of by accident)."""

    def test_days_ratio_gets_d_suffix(self, f16):
        assert f16.format_value(45.3, "dso") == "45d"

    def test_percentage_ratio_gets_percent_suffix(self, f16):
        assert f16.format_value(74.3, "gross_margin") == "74.3%"

    def test_multiple_ratio_gets_x_suffix(self, f16):
        assert f16.format_value(2.8, "net_debt_ebitda_proxy") == "2.8x"

    def test_nan_is_na_string(self, f16):
        assert f16.format_value(float("nan"), "dso") == "n/a"


def test_default_ratios_includes_working_capital_metrics(f16):
    """DSO/DIO/DPO/CCC were computed but never registered for output
    until this fix (see ROADMAP.md V2) - locking in that they're now
    included by default so a future refactor can't silently drop them
    again."""
    for ratio in ("dso", "dio", "dpo", "ccc"):
        assert ratio in f16.DEFAULT_RATIOS
