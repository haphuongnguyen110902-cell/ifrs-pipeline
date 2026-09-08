"""
tests/test_fx_convert.py

Regression tests for scripts/18_fx_convert.py.
"""
import math

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def fx(load_script):
    return load_script("18_fx_convert.py")


def test_compute_yearly_rates_avg_and_closing(fx):
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2023-01-15", "2023-06-15", "2023-12-29"]),
        "rate": [1.06, 1.08, 1.10],
        "currency": "USD",
    })
    yearly = fx.compute_yearly_rates(daily)
    row = yearly.iloc[0]
    assert row["avg_rate"] == pytest.approx(1.08, abs=1e-9)
    assert row["closing_rate"] == pytest.approx(1.10)  # last observation of the year
    assert row["n_observations"] == 3


class TestToEur:
    """Per IAS 21: average rate for P&L/flow items, closing rate for
    balance-sheet/stock items - using the wrong one for either is a
    real accounting error, not a style choice."""

    @pytest.fixture
    def fx_lookup(self):
        return {("USD", 2023): {"avg_rate": 1.08, "closing_rate": 1.10}}

    def test_pl_item_uses_average_rate(self, fx, fx_lookup):
        result = fx.to_eur(1000, "USD", 2023, fx_lookup, "avg")
        assert result == pytest.approx(1000 / 1.08)

    def test_balance_sheet_item_uses_closing_rate(self, fx, fx_lookup):
        result = fx.to_eur(1000, "USD", 2023, fx_lookup, "closing")
        assert result == pytest.approx(1000 / 1.10)

    def test_eur_passes_through_unchanged(self, fx, fx_lookup):
        assert fx.to_eur(500, "EUR", 2023, fx_lookup, "avg") == 500

    def test_missing_year_returns_nan_never_guesses(self, fx, fx_lookup):
        result = fx.to_eur(100, "USD", 1999, fx_lookup, "avg")
        assert math.isnan(result)


class TestDetectCurrencies:
    """fact_value.currency also stores non-currency XBRL units (EPS's
    'EUR / shares', share counts as 'shares', dimensionless ratios as
    'pure') - found by running this against the real database, where a
    naive DISTINCT query returned all of these alongside SEK/USD."""

    def test_filters_out_non_iso_values(self, fx, monkeypatch):
        garbage = ["EUR / shares", "SEK", "SEK / shares", "USD",
                   "USD / shares", "pure", "shares", "EUR"]
        monkeypatch.setattr(
            pd, "read_sql",
            lambda *a, **k: pd.DataFrame({"currency": garbage}))
        result = fx.detect_currencies(engine=None)
        assert result == ["SEK", "USD"]

    def test_excludes_eur_even_if_sql_filter_somehow_missed_it(self, fx, monkeypatch):
        """Defense in depth: EUR must never appear in the result even if
        the SQL WHERE clause is ever changed or bypassed."""
        monkeypatch.setattr(
            pd, "read_sql",
            lambda *a, **k: pd.DataFrame({"currency": ["EUR", "GBP"]}))
        result = fx.detect_currencies(engine=None)
        assert "EUR" not in result
        assert result == ["GBP"]
