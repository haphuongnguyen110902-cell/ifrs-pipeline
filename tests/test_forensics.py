"""
tests/test_forensics.py

Regression tests for scripts/15_forensics.py. These lock in the exact
bugs found and fixed during development - see ROADMAP.md's V2 entry.
Losing any of these silently (e.g. a future refactor that drops the
severity-downgrade logic) would mean a real earnings-quality signal gets
mis-reported as more (or less) severe than it actually is - not a
cosmetic regression.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def forensics(load_script):
    return load_script("15_forensics.py")


def test_thin_denominator_fires_on_the_real_essilorluxottica_2020_case(forensics):
    """The case this flag was built from: op margin 3.1% (COVID year)
    inflated cash_conversion to 653.3% - verified against
    EssilorLuxottica's actual published 2020 results (452M EUR operating
    profit / 14,429M EUR revenue = 3.13%)."""
    wide = pd.DataFrame([
        {"company": "EssilorLuxottica", "year": 2019, "operating_margin": 24.0,
         "cash_conversion": 120.0, "net_debt_ebitda_proxy": 2.0, "tax_rate": 25.0},
        {"company": "EssilorLuxottica", "year": 2020, "operating_margin": 3.1,
         "cash_conversion": 653.3, "net_debt_ebitda_proxy": 2.8, "tax_rate": 52.4},
    ])
    flags = forensics.compute_flags(wide)
    thin = flags[flags["flag_id"] == "THIN_DENOMINATOR"]
    assert len(thin) == 1
    assert thin.iloc[0]["company"] == "EssilorLuxottica"
    assert thin.iloc[0]["year"] == 2020


def test_thin_denominator_does_not_false_positive_on_normal_data(forensics):
    wide = pd.DataFrame([
        {"company": "L'Oreal", "year": 2023, "operating_margin": 18.7,
         "cash_conversion": 98.8, "net_debt_ebitda_proxy": 0.3, "tax_rate": 22.6},
        {"company": "L'Oreal", "year": 2024, "operating_margin": 19.0,
         "cash_conversion": 100.4, "net_debt_ebitda_proxy": 0.3, "tax_rate": 23.9},
    ])
    flags = forensics.compute_flags(wide)
    # clean data may trigger zero flags of ANY kind, in which case
    # compute_flags() returns a fully empty DataFrame with no columns -
    # that's correct behavior (the module stays silent on clean data),
    # so guard against it before checking for THIN_DENOMINATOR specifically.
    if flags.empty:
        return
    assert (flags["flag_id"] == "THIN_DENOMINATOR").sum() == 0


class TestCashConversionDropDowngrade:
    """A YoY cash-conversion drop that lands on (or comes from) a
    thin-denominator year overstates real deterioration - severity must
    downgrade to 'low', but the flag must NOT disappear (still worth
    seeing, just not trusted at face value)."""

    @pytest.fixture
    def three_year_essilorluxottica(self):
        return pd.DataFrame([
            {"company": "EssilorLuxottica", "year": 2019, "operating_margin": 24.0,
             "cash_conversion": 120.0, "net_debt_ebitda_proxy": 2.0, "tax_rate": 25.0},
            {"company": "EssilorLuxottica", "year": 2020, "operating_margin": 3.1,
             "cash_conversion": 653.3, "net_debt_ebitda_proxy": 2.8, "tax_rate": 52.4},
            {"company": "EssilorLuxottica", "year": 2021, "operating_margin": 11.7,
             "cash_conversion": 195.4, "net_debt_ebitda_proxy": 2.9, "tax_rate": 26.7},
            {"company": "EssilorLuxottica", "year": 2022, "operating_margin": 12.9,
             "cash_conversion": 151.5, "net_debt_ebitda_proxy": 2.2, "tax_rate": 24.8},
        ])

    def test_drop_off_a_thin_baseline_is_downgraded_to_low(self, forensics, three_year_essilorluxottica):
        flags = forensics.compute_flags(three_year_essilorluxottica)
        drop_2021 = flags[(flags["flag_id"] == "CASH_CONVERSION_DROP") & (flags["year"] == 2021)]
        assert len(drop_2021) == 1
        assert drop_2021.iloc[0]["severity"] == "low"
        assert "distorted" in drop_2021.iloc[0]["detail"].lower()

    def test_a_normal_drop_not_touching_a_thin_year_stays_high(self, forensics, three_year_essilorluxottica):
        flags = forensics.compute_flags(three_year_essilorluxottica)
        drop_2022 = flags[(flags["flag_id"] == "CASH_CONVERSION_DROP") & (flags["year"] == 2022)]
        assert len(drop_2022) == 1
        assert drop_2022.iloc[0]["severity"] == "high"

    def test_a_genuine_drop_with_no_thin_year_anywhere_stays_high(self, forensics):
        """Guards against the fix over-firing: a real deterioration with
        no denominator distortion involved must not get downgraded."""
        wide = pd.DataFrame([
            {"company": "RealDrop", "year": 2023, "operating_margin": 15.0,
             "cash_conversion": 150.0, "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
            {"company": "RealDrop", "year": 2024, "operating_margin": 14.0,
             "cash_conversion": 100.0, "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide)
        drop = flags[flags["flag_id"] == "CASH_CONVERSION_DROP"]
        assert len(drop) == 1
        assert drop.iloc[0]["severity"] == "high"


class TestSingleCompanyMissingColumn:
    """Found via app.py's per-company forensics view (a real bug caught
    by actually running the Streamlit app, not by reading code): running
    the full 11-company universe together always has an
    'operating_margin' column in the pivoted wide table, because SOME
    company almost always has it. Filtered down to a SINGLE company that
    happens to have zero rows for a given ratio_name, pivot_table()
    doesn't create that column at all - and the old code accessed it
    with unsafe bracket indexing (co[...]['operating_margin']), which
    raises KeyError instead of just finding no data."""

    def test_missing_operating_margin_column_does_not_crash(self, forensics):
        # deliberately no 'operating_margin' key anywhere - simulates a
        # company whose ratio history never includes that ratio_name
        wide = pd.DataFrame([
            {"company": "SparseCo", "year": 2022, "cash_conversion": 90.0,
             "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
            {"company": "SparseCo", "year": 2023, "cash_conversion": 60.0,
             "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide)  # must not raise KeyError
        assert isinstance(flags, pd.DataFrame)

    def test_safe_year_col_missing_column_returns_empty_series(self, forensics):
        wide = pd.DataFrame([{"company": "X", "year": 2023, "cash_conversion": 90.0}])
        result = forensics.safe_year_col(wide, 2022, "operating_margin")
        assert result.empty

    def test_safe_year_col_existing_column_still_works_normally(self, forensics):
        wide = pd.DataFrame([
            {"company": "X", "year": 2022, "operating_margin": 15.0},
            {"company": "X", "year": 2023, "operating_margin": 18.0},
        ])
        result = forensics.safe_year_col(wide, 2022, "operating_margin")
        assert result.iloc[0] == 15.0


class TestHighLeverageDowngrade:
    """Same denominator-distortion logic as CASH_CONVERSION_DROP, applied
    to HIGH_LEVERAGE - net_debt_ebitda_proxy divides by the same
    Operating Profit, so it needs the same caution when that year's
    margin is thin. Found as a design INCONSISTENCY (not yet manifesting
    in real data) while auditing the fix above - the point of this test
    is to make sure it never quietly regresses back to inconsistent."""

    def test_high_leverage_on_a_thin_margin_year_is_downgraded(self, forensics):
        wide = pd.DataFrame([
            {"company": "ThinLeverageCo", "year": 2023, "operating_margin": 2.5,
             "cash_conversion": 90.0, "net_debt_ebitda_proxy": 6.2, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide)
        lev = flags[flags["flag_id"] == "HIGH_LEVERAGE"]
        assert len(lev) == 1
        assert lev.iloc[0]["severity"] == "low"

    def test_high_leverage_on_a_normal_margin_year_stays_high(self, forensics):
        """The real Amplifon 2025 case: genuinely high leverage (6.8x),
        normal margin - must not be downgraded just because SOME company
        somewhere can have a thin margin."""
        wide = pd.DataFrame([
            {"company": "Amplifon", "year": 2025, "operating_margin": 20.0,
             "cash_conversion": 211.0, "net_debt_ebitda_proxy": 6.8, "tax_rate": 30.5},
        ])
        flags = forensics.compute_flags(wide)
        lev = flags[flags["flag_id"] == "HIGH_LEVERAGE"]
        assert len(lev) == 1
        assert lev.iloc[0]["severity"] == "high"
