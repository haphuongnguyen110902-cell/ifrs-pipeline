"""
tests/test_valuation_financial_and_ticker.py

Two defects seen while running valuation for the companies that had just been loaded from the universe:
  * a missing ticker (NaN from pandas is truthy) went to yfinance and came back as "market data failed:
    'float' object has no attribute 'upper'" instead of "no ticker mapped";
  * Adyen, a payment processor, would get an enterprise value built on about -EUR 10.5bn of "net cash" that is
    merchants' funds - a plausible-looking multiple on a meaningless number. Financial companies are skipped,
    using the classification that already blanks their ratios.
No database, no network.
"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def v19(load_script):
    return load_script("19_valuation.py")


class TestTicker:
    def test_a_nan_ticker_means_no_ticker_not_a_crash_in_yfinance(self, v19):
        assert v19.resolve_ticker_currency("Some New Co", np.nan, np.nan) == (None, None)
        assert v19.resolve_ticker_currency("Some New Co", None, None) == (None, None)
        assert v19.resolve_ticker_currency("Some New Co", "", "EUR") == (None, None)
        assert v19.resolve_ticker_currency("Some New Co", "   ", "EUR") == (None, None)

    def test_a_db_ticker_is_used_for_a_company_outside_the_hand_verified_map(self, v19):
        assert v19.resolve_ticker_currency("Heineken", "HEIA.AS", "EUR") == ("HEIA.AS", "EUR")
        assert v19.resolve_ticker_currency("Heineken", " HEIA.AS ", "EUR") == ("HEIA.AS", "EUR")

    def test_a_missing_currency_stays_missing_it_is_not_guessed(self, v19):
        assert v19.resolve_ticker_currency("X", "X.AS", np.nan) == ("X.AS", None)

    def test_the_hand_verified_map_still_wins(self, v19):
        assert v19.resolve_ticker_currency("Essity", "ESSITY-A.ST", "SEK") == v19.TICKER_MAP["Essity"]


class TestFinancialCompanies:
    def frame(self):
        return pd.DataFrame({"company": ["Adyen", "Danone", "Heineken"], "year": [2025, 2024, 2025]})

    def test_only_the_financial_company_is_dropped(self, v19):
        kept, dropped = v19.drop_financial(self.frame(), {"Adyen": "reporting_model: financial"})
        assert list(kept["company"]) == ["Danone", "Heineken"] and dropped == ["Adyen"]

    def test_nothing_financial_means_nothing_dropped(self, v19):
        kept, dropped = v19.drop_financial(self.frame(), {})
        assert len(kept) == 3 and dropped == []

    def test_a_financial_name_not_in_the_run_is_ignored(self, v19):
        kept, dropped = v19.drop_financial(self.frame(), {"Bank X": "x"})
        assert len(kept) == 3 and dropped == []

    def test_an_empty_frame_is_returned_as_is(self, v19):
        kept, dropped = v19.drop_financial(pd.DataFrame(columns=["company"]), {"Adyen": "x"})
        assert kept.empty and dropped == []

    def test_the_main_block_drops_them_before_building_comps(self, v19):
        src = open(v19.__file__, encoding="utf-8").read()
        main = src[src.index('if __name__ == "__main__":'):]
        assert main.index("drop_financial(") < main.index("build_comps(")
