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


class _Conn:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(stmt).split()), params or {}))


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    def begin(self):
        return self.conn


class TestSaveReplacesOlderRows:
    """Upserting on (company, ratio, method, forecast_year) kept an older base year's rows: after the FY2025 reports
    were loaded, 119 stored rows still forecast 2025 from 2024 next to the 2025 actuals, and 87 kept an older run's
    base year."""

    @staticmethod
    def frame(f16):
        import pandas as pd
        return pd.DataFrame([
            {"company_id": 1, "ratio_name": "dso", "n_years": 4, "first_year": 2022, "last_year": 2025,
             "cagr": 0.01, "linreg_r2": 0.5, "cagr_fc_y1_2026": 50.0, "linreg_fc_y1_2026": 51.0},
            {"company_id": 1, "ratio_name": "roe", "n_years": 2, "first_year": 2024, "last_year": 2025},   # too short
        ])

    def test_every_computed_pair_is_cleared_before_it_is_written(self, f16):
        engine = _Engine()
        written = f16.save_to_db(engine, self.frame(f16), horizon=1)
        sql = [s for s, _ in engine.conn.calls]
        deletes = [p for s, p in engine.conn.calls if s.startswith("DELETE FROM forecast")]
        assert {(p["cid"], p["rn"]) for p in deletes} == {(1, "dso"), (1, "roe")}   # roe: no forecast now, none kept
        first_insert = next(i for i, s in enumerate(sql) if s.startswith("INSERT INTO forecast"))
        assert all(i < first_insert for i, s in enumerate(sql) if s.startswith("DELETE"))
        assert written == 2

