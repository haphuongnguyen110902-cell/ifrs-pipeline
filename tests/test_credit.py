"""
tests/test_credit.py

Regression tests for scripts/24_credit.py. No test touches the database -
build_credit_profile() takes an already-computed ratios DataFrame, same
"factor the pure logic out so it's testable without a DB connection"
pattern as 21_three_statement_model.py's select_base_year_row().
"""
import math

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def credit(load_script):
    return load_script("24_credit.py")


class TestClassifyBand:
    def test_net_cash_is_its_own_band_not_very_low_leverage(self, credit):
        """A net-cash company isn't just 'low leverage', it's a
        qualitatively different balance sheet - same distinction
        15_forensics.py's NEGATIVE_NET_DEBT flag already makes."""
        assert credit.classify_band(-0.5) == "Net cash"

    @pytest.mark.parametrize("ratio,expected", [
        (0.5, "Very low leverage"),
        (1.5, "Low leverage"),
        (2.5, "Moderate leverage"),
        (4.0, "Elevated leverage"),
        (5.0, "High leverage"),
        (7.0, "Very high leverage"),
    ])
    def test_band_boundaries(self, credit, ratio, expected):
        assert credit.classify_band(ratio) == expected

    def test_exact_boundary_goes_to_the_lower_band(self, credit):
        """2.0x is the start of 'Moderate', not the end of 'Low' -
        boundaries are < cutoff, not <=, verified explicitly rather than
        left to whichever way the comparison operator happens to fall."""
        assert credit.classify_band(2.0) == "Moderate leverage"
        assert credit.classify_band(1.999) == "Low leverage"

    def test_nan_is_na_not_a_crash(self, credit):
        assert credit.classify_band(float("nan")) == "n/a"


class TestClassifyTrend:
    def test_large_increase_is_deteriorating(self, credit):
        assert credit.classify_trend(0.5) == "Deteriorating"

    def test_large_decrease_is_improving(self, credit):
        """A negative yoy_change means leverage FELL - that's an
        improving credit profile, not a 'negative' trend."""
        assert credit.classify_trend(-0.5) == "Improving"

    def test_small_change_is_stable(self, credit):
        assert credit.classify_trend(0.1) == "Stable"
        assert credit.classify_trend(-0.1) == "Stable"

    def test_nan_is_na_not_a_crash(self, credit):
        """First year of a company's history has no prior year to
        compare against - must be 'n/a', never a crash or a fake 0."""
        assert credit.classify_trend(float("nan")) == "n/a"


class TestBuildCreditProfile:
    def make_ratios(self, **overrides):
        row = {"year": 2023, "_net_debt": 200.0, "_ebitda": 100.0, "_da_total": 20.0}
        row.update(overrides)
        return pd.DataFrame([row])

    def test_uses_ebitda_not_the_misleading_proxy_column(self, credit):
        """The core bug this script exists to avoid: net_debt_ebitda
        must be net_debt / _ebitda, NOT net_debt / _ebit (what
        11_ratio_engine.py's confusingly-named net_debt_ebitda_proxy
        actually is) - see module docstring."""
        ratios = self.make_ratios(_net_debt=200.0, _ebitda=100.0)
        result = credit.build_credit_profile(ratios, "TestCo")
        assert result["net_debt_ebitda"].iloc[0] == pytest.approx(2.0)

    def test_flags_da_fallback_when_da_total_is_zero(self, credit):
        """_da_total == 0 means no D&A tag matched for this company/year
        (see 11_ratio_engine.py) - _ebitda silently collapses to _ebit,
        overstating leverage. Must be flagged, never silent."""
        ratios = self.make_ratios(_da_total=0.0)
        result = credit.build_credit_profile(ratios, "TestCo")
        assert result["is_da_fallback"].iloc[0] == True  # noqa: E712

    def test_does_not_flag_da_fallback_when_da_total_is_real(self, credit):
        ratios = self.make_ratios(_da_total=20.0)
        result = credit.build_credit_profile(ratios, "TestCo")
        assert result["is_da_fallback"].iloc[0] == False  # noqa: E712

    def test_yoy_change_and_trend_computed_across_years(self, credit):
        ratios = pd.DataFrame([
            {"year": 2022, "_net_debt": 300.0, "_ebitda": 100.0, "_da_total": 20.0},  # 3.0x
            {"year": 2023, "_net_debt": 250.0, "_ebitda": 100.0, "_da_total": 20.0},  # 2.5x
        ])
        result = credit.build_credit_profile(ratios, "TestCo")
        assert math.isnan(result["yoy_change"].iloc[0])  # no prior year
        assert result["yoy_change"].iloc[1] == pytest.approx(-0.5)
        assert result["trend"].iloc[1] == "Improving"

    def test_rows_missing_net_debt_or_ebitda_are_dropped_not_faked(self, credit):
        ratios = pd.DataFrame([
            {"year": 2022, "_net_debt": None, "_ebitda": 100.0, "_da_total": 20.0},
            {"year": 2023, "_net_debt": 250.0, "_ebitda": 100.0, "_da_total": 20.0},
        ])
        result = credit.build_credit_profile(ratios, "TestCo")
        assert len(result) == 1
        assert result["year"].iloc[0] == 2023

    def test_empty_ratios_returns_empty_not_a_crash(self, credit):
        result = credit.build_credit_profile(pd.DataFrame(), "TestCo")
        assert result.empty
