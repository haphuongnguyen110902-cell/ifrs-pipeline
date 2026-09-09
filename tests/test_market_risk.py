"""
tests/test_market_risk.py

Regression tests for scripts/23_market_risk.py. Every formula here is
verified against an independently hand-calculated or numpy-derived
value, not just checked for "did it run" - see each test's inline
calculation. No test touches yfinance/the network, same "synthetic data,
no live calls" convention as every other test file in this project.
"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def mr(load_script):
    return load_script("23_market_risk.py")


def test_annualized_volatility_matches_hand_calculation(mr):
    returns = pd.Series([0.01, -0.02, 0.015, -0.005, 0.02])
    result = mr.annualized_volatility(returns)
    expected = returns.std() * np.sqrt(252)
    assert result == pytest.approx(expected)


def test_annualized_volatility_empty_series_is_nan_not_zero(mr):
    """An empty return series (e.g. yfinance returned nothing) must
    surface as NaN, never a silently-wrong 0% volatility."""
    assert np.isnan(mr.annualized_volatility(pd.Series(dtype=float)))


class TestSharpeRatio:
    def test_matches_hand_calculation(self, mr):
        returns = pd.Series([0.001, 0.002, -0.001, 0.0015, 0.0005])
        risk_free = 0.03
        result = mr.sharpe_ratio(returns, risk_free)
        annualized_return = returns.mean() * 252
        vol = returns.std() * np.sqrt(252)
        expected = (annualized_return - risk_free) / vol
        assert result == pytest.approx(expected)

    def test_zero_volatility_is_nan_not_a_crash(self, mr):
        """A constant (zero-variance) return series must not raise a
        division-by-zero - NaN is the honest answer, not a crash."""
        returns = pd.Series([0.001, 0.001, 0.001, 0.001])
        result = mr.sharpe_ratio(returns, 0.03)
        assert np.isnan(result)


class TestMaxDrawdown:
    def test_matches_hand_calculation(self, mr):
        # Prices: 100 -> 120 (peak) -> 90 (trough, -25% from peak) -> 110
        prices = pd.Series([100, 120, 90, 110])
        result = mr.max_drawdown(prices)
        assert result == pytest.approx(-0.25)

    def test_monotonically_rising_prices_have_zero_drawdown(self, mr):
        prices = pd.Series([100, 105, 110, 120])
        assert mr.max_drawdown(prices) == pytest.approx(0.0)

    def test_empty_series_is_nan(self, mr):
        assert np.isnan(mr.max_drawdown(pd.Series(dtype=float)))


class TestBeta:
    def test_matches_hand_calculation(self, mr):
        dates = pd.date_range("2026-01-01", periods=5)
        stock = pd.Series([0.02, -0.01, 0.03, 0.00, 0.01], index=dates)
        bench = pd.Series([0.01, -0.005, 0.02, 0.005, 0.008], index=dates)
        beta, n = mr.compute_beta(stock, bench)
        expected = stock.cov(bench) / bench.var()
        assert beta == pytest.approx(expected)
        assert n == 5

    def test_beta_of_one_for_identical_series(self, mr):
        """A stock that moves exactly with its benchmark has beta = 1 -
        the simplest possible sanity check on the formula's direction."""
        dates = pd.date_range("2026-01-01", periods=10)
        bench = pd.Series(np.linspace(0.01, 0.05, 10), index=dates)
        beta, _ = mr.compute_beta(bench.copy(), bench)
        assert beta == pytest.approx(1.0, abs=1e-9)

    def test_aligns_on_shared_dates_only(self, mr):
        """A stock and its benchmark don't trade on exactly the same
        calendar (different market holidays) - misaligned dates must be
        dropped, not silently paired up positionally by row index."""
        stock = pd.Series([0.01, 0.02, 0.03],
                           index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]))
        bench = pd.Series([0.01, 0.02, 0.03],
                           index=pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-05"]))
        beta, n = mr.compute_beta(stock, bench)
        assert n == 2  # only 2026-01-01 and 2026-01-05 are shared

    def test_zero_benchmark_variance_is_nan_not_a_crash(self, mr):
        dates = pd.date_range("2026-01-01", periods=4)
        stock = pd.Series([0.01, -0.01, 0.02, 0.0], index=dates)
        bench = pd.Series([0.005, 0.005, 0.005, 0.005], index=dates)
        beta, _ = mr.compute_beta(stock, bench)
        assert np.isnan(beta)


def test_correlation_matches_hand_calculation(mr):
    dates = pd.date_range("2026-01-01", periods=6)
    stock = pd.Series([0.01, -0.02, 0.03, 0.0, 0.015, -0.005], index=dates)
    bench = pd.Series([0.008, -0.015, 0.025, 0.002, 0.01, -0.003], index=dates)
    result = mr.compute_correlation(stock, bench)
    expected = stock.corr(bench)
    assert result == pytest.approx(expected)


class TestRollingCorrelationSummary:
    def test_too_few_observations_returns_all_nan(self, mr):
        """Fewer observations than the rolling window must produce an
        honest all-NaN summary, never a crash or a misleadingly-narrow
        window silently substituted."""
        dates = pd.date_range("2026-01-01", periods=5)
        stock = pd.Series([0.01, -0.01, 0.02, 0.0, 0.01], index=dates)
        bench = pd.Series([0.01, -0.01, 0.02, 0.0, 0.01], index=dates)
        result = mr.rolling_correlation_summary(stock, bench, window=60)
        assert np.isnan(result["mean"])
        assert np.isnan(result["min"])
        assert np.isnan(result["max"])

    def test_enough_observations_produces_a_real_summary(self, mr):
        rng = np.random.default_rng(42)
        dates = pd.date_range("2026-01-01", periods=100)
        bench = pd.Series(rng.normal(0, 0.01, 100), index=dates)
        stock = bench + pd.Series(rng.normal(0, 0.002, 100), index=dates)  # correlated w/ noise
        result = mr.rolling_correlation_summary(stock, bench, window=60)
        assert not np.isnan(result["mean"])
        assert -1.0 <= result["min"] <= result["max"] <= 1.0
