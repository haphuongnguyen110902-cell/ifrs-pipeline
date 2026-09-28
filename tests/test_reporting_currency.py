"""
tests/test_reporting_currency.py

19_valuation.py converted a company's figures to EUR from the currency its SHARES are quoted in ("true for the current
11-company universe"). Phase 4 adds Anheuser-Busch InBev and STMicroelectronics (report in USD, trade in EUR) and RELX
(reports in GBP): their USD / GBP figures would have been read as EUR. Figures are now converted from the reporting
currency - the currency most of a year's monetary facts are stored in - and only the market cap from the quote
currency; the 3-statement base year carries it for the DCF and the scenario tool. No database.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


@pytest.fixture(scope="module")
def v19(load_script):
    return load_script("19_valuation.py")


class TestReportingCurrencies:
    def test_the_currency_of_most_monetary_facts(self, r11):
        facts = pd.DataFrame({"company_id": [1] * 5, "year": [2025] * 5,
                              "currency": ["SEK", "SEK", "SEK", "EUR", "SEK / shares"]})
        assert r11.reporting_currencies(facts) == {(1, 2025): "SEK"}

    def test_share_and_pure_units_are_not_currencies(self, r11):
        facts = pd.DataFrame({"company_id": [2, 2, 2], "year": [2025] * 3, "currency": ["shares", "pure", "USD"]})
        assert r11.reporting_currencies(facts) == {(2, 2025): "USD"}

    def test_empty_is_empty(self, r11):
        assert r11.reporting_currencies(pd.DataFrame()) == {}


class TestForwardMultiples:
    def test_projected_figures_are_converted_from_the_reporting_currency(self, v19):
        """A USD reporter quoted in EUR: its projected USD revenue divided by the USD rate, not taken as EUR."""
        comps = pd.DataFrame([{"company": "ABI", "quote_ccy": "EUR", "report_ccy": "USD", "ev_eur": 150_000.0,
                               "ebitda_is_fallback": False}])
        history = pd.DataFrame({"company": ["ABI"] * 3, "year": [2023, 2024, 2025],
                                "_revenue": [60_000.0, 60_000.0, 60_000.0], "_ebitda": [20_000.0, 20_000.0, 20_000.0]})
        fx = {("USD", 2025): {"avg_rate": 1.10, "closing_rate": 1.15}}
        out = v19.compute_forward_multiples(comps, history, fx).iloc[0]
        assert out["fwd_ev_sales"] == pytest.approx(150_000.0 / (60_000.0 / 1.10))
        assert out["fwd_ev_ebitda"] == pytest.approx(150_000.0 / (20_000.0 / 1.10))


class TestValuationKeepsOneSnapshot:
    """A valuation is today's market value over the latest year. When that year moved on, the previous year's row stayed
    (Kering showed P/E 9.4 for 2023 beside 388.6 for 2025) and 20_precedents.py, which reads every row, counted both."""

    class _Result:
        def fetchall(self):
            return [(9, "Kering")]

    class _Conn:
        def __init__(self):
            self.calls = []

        def execute(self, stmt, params=None):
            self.calls.append((str(stmt), params or {}))
            return TestValuationKeepsOneSnapshot._Result()

    class _Engine:
        def __init__(self, conn):
            self.conn = conn

        def begin(self):
            conn = self.conn

            class _Ctx:
                def __enter__(self):
                    return conn

                def __exit__(self, *a):
                    return False
            return _Ctx()

    def test_older_years_are_removed_for_the_company_written(self, v19):
        conn = self._Conn()
        comps = pd.DataFrame([{"company": "Kering", "year": 2025, "ticker": "KER.PA", "ev_eur": 42e9, "market_cap_eur": 28e9,
                               "net_debt_eur": 14e9, "revenue_eur": 14.7e9, "ebitda_eur": 3e9, "net_income_eur": 72e6,
                               "ev_ebitda": 14.0, "ev_sales": 2.9, "pe": 388.6}])
        v19.save_to_db(self._Engine(conn), comps)
        deletes = [p for s, p in conn.calls if s.strip().startswith("DELETE FROM valuation")]
        assert deletes == [{"cid": 9, "year": 2025}]
