"""
tests/test_forensics.py

Regression tests for scripts/15_forensics.py. These lock in the exact
bugs found and fixed during development - see ROADMAP.md's V2 entry.
Losing any of these silently (e.g. a future refactor that drops the
severity-downgrade logic) would mean a real earnings-quality signal gets
mis-reported as more (or less) severe than it actually is - not a
cosmetic regression.

The TestPersistence class at the end is a deliberate, narrow exception
to this file otherwise being fully DB-free (see PLAN.md WP2 and
tests/test_baseline_regression.py's docstring for the same pattern) -
it needs a live DATABASE_URL to mean anything, so it's skipped, not
failed, when one isn't set.
"""
import os

import pandas as pd
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")


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


class TestOffCalendarFyeIsDataDrivenNotHardcoded:
    """PLAN.md WP3c: compute_flags() used to carry a literal
    `pernod_companies = ["Pernod Ricard"]` list. It now takes an
    off_calendar_fye dict instead, sourced from company.fiscal_year_end_
    month/day - these tests prove the function itself no longer has ANY
    company name baked in, using a company that is deliberately NOT
    Pernod Ricard."""

    def test_fires_for_whichever_company_the_caller_names_off_calendar(self, forensics):
        wide = pd.DataFrame([
            {"company": "SomeOtherCo", "year": 2024, "operating_margin": 15.0,
             "cash_conversion": 100.0, "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide, off_calendar_fye={"SomeOtherCo": "March 31"})
        fye = flags[flags["flag_id"] == "PERNOD_FYE_WARNING"]
        assert len(fye) == 1
        assert fye.iloc[0]["company"] == "SomeOtherCo"
        assert "March 31" in fye.iloc[0]["detail"]

    def test_pernod_ricard_by_name_alone_triggers_nothing_without_the_dict_entry(self, forensics):
        """The exact hardcode this replaced would have fired on the name
        alone - confirms that's gone: naming a company "Pernod Ricard" in
        the data is no longer sufficient by itself."""
        wide = pd.DataFrame([
            {"company": "Pernod Ricard", "year": 2024, "operating_margin": 15.0,
             "cash_conversion": 100.0, "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide)  # no off_calendar_fye passed
        assert (flags["flag_id"] == "PERNOD_FYE_WARNING").sum() == 0 if not flags.empty else True

    def test_a_company_not_in_the_dict_gets_no_warning(self, forensics):
        wide = pd.DataFrame([
            {"company": "CalendarYearCo", "year": 2024, "operating_margin": 15.0,
             "cash_conversion": 100.0, "net_debt_ebitda_proxy": 1.0, "tax_rate": 25.0},
        ])
        flags = forensics.compute_flags(wide, off_calendar_fye={"SomeOtherCo": "March 31"})
        assert (flags["flag_id"] == "PERNOD_FYE_WARNING").sum() == 0 if not flags.empty else True


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


@pytest.fixture(scope="module")
def db_engine():
    return create_engine(DATABASE_URL) if DATABASE_URL else None


@pytest.mark.skipif(not DATABASE_URL, reason="needs a live DATABASE_URL - see module docstring")
class TestPersistence:
    """save_to_db() (PLAN.md WP2) against the live DB - table creation,
    row counts, the delete-then-insert-not-upsert behavior, and the
    skip-with-a-warning path for an unresolvable company name."""

    def test_ensure_forensics_table_is_idempotent(self, forensics, db_engine):
        forensics.ensure_forensics_table(db_engine)
        forensics.ensure_forensics_table(db_engine)  # must not raise the second time

    def test_save_to_db_matches_the_documented_flag_count(self, forensics, db_engine):
        """README documents 62 flags (26 high / 12 medium / 24 low) across
        the 11-company universe - the same number this test locks in, so a
        future ratio-engine change that silently shifts flag counts is
        caught here rather than only noticed by re-reading the README."""
        forensics.ensure_forensics_table(db_engine)
        ratio_df = forensics.fetch_ratios(db_engine)
        wide = forensics.pivot_ratios(ratio_df)
        off_calendar_fye = forensics.fetch_off_calendar_fye(db_engine)
        flags = forensics.compute_flags(wide, off_calendar_fye)
        rev_growth = forensics.fetch_revenue_growth(db_engine)
        if not rev_growth.empty:
            flags = forensics.add_revenue_flags(flags, rev_growth)

        n_saved = forensics.save_to_db(db_engine, flags)
        assert n_saved == len(flags)

        with db_engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM forensics_flag")).scalar()
            by_severity = dict(conn.execute(text(
                "SELECT severity, COUNT(*) FROM forensics_flag GROUP BY severity"
            )).fetchall())
            null_company_id = conn.execute(text(
                "SELECT COUNT(*) FROM forensics_flag WHERE company_id IS NULL"
            )).scalar()

        assert total == 62, f"expected 62 flags (see README), got {total}"
        assert by_severity == {"high": 26, "medium": 12, "low": 24}
        assert null_company_id == 0

    def test_rerunning_save_to_db_is_idempotent_not_additive(self, forensics, db_engine):
        """Delete-then-insert, not upsert (see save_to_db's docstring) -
        running it twice must leave the SAME row count, not double it."""
        ratio_df = forensics.fetch_ratios(db_engine)
        wide = forensics.pivot_ratios(ratio_df)
        flags = forensics.compute_flags(wide)

        forensics.save_to_db(db_engine, flags)
        with db_engine.connect() as conn:
            first_count = conn.execute(text("SELECT COUNT(*) FROM forensics_flag")).scalar()

        forensics.save_to_db(db_engine, flags)
        with db_engine.connect() as conn:
            second_count = conn.execute(text("SELECT COUNT(*) FROM forensics_flag")).scalar()

        assert first_count == second_count

    def test_unresolvable_company_name_is_skipped_not_written_as_null(self, forensics, db_engine):
        """A flag for a company with no matching company.name row must be
        silently dropped with a warning (see save_to_db's docstring),
        never written with a NULL company_id - the table's own NOT NULL
        constraint would reject it anyway, but the function should never
        attempt to."""
        fake_flags = pd.DataFrame([{
            "company": "Not A Real Company In The DB",
            "year": 2024, "flag_id": "TAX_RATE_ANOMALY", "label": "Tax Rate Anomaly",
            "severity": "medium", "value": 5.0, "detail": "test row",
            "what_to_check": "n/a",
        }])
        n_saved = forensics.save_to_db(db_engine, fake_flags)
        assert n_saved == 0

    def test_fetch_off_calendar_fye_finds_the_real_pernod_ricard_case(self, forensics, db_engine):
        """Live check that migration_002 + 09_batch_load.py's companies.yaml
        backfill actually left Pernod Ricard's company.fiscal_year_end_month/
        day populated - if this ever regresses to NULL, the PERNOD_FYE_WARNING
        flag silently stops firing for the one real off-calendar company in
        the universe, with no error to notice it by."""
        result = forensics.fetch_off_calendar_fye(db_engine)
        assert result.get("Pernod Ricard") == "June 30", (
            f"expected Pernod Ricard's fiscal_year_end_month/day to resolve to "
            f"'June 30', got {result.get('Pernod Ricard')!r} - full result: {result}"
        )
