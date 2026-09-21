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


def _pipeline_flags(forensics, engine):
    """The flags exactly as scripts/15_forensics.py's main() builds them:
    ratio flags WITH the off-calendar-FYE dict, plus the revenue flags.

    These tests write to the LIVE forensics_flag table (save_to_db commits),
    so what they save must be what the pipeline would save. One of them used
    to save `compute_flags(wide)` alone - no revenue flags, no Pernod FYE
    warning - which left the live table 13 flags short after every local
    test run, until the next real pipeline run."""
    wide = forensics.pivot_ratios(forensics.fetch_ratios(engine))
    flags = forensics.compute_flags(wide, forensics.fetch_off_calendar_fye(engine))
    rev_growth = forensics.fetch_revenue_growth(engine)
    if not rev_growth.empty:
        flags = forensics.add_revenue_flags(flags, rev_growth)
    return flags, wide


@pytest.mark.skipif(not DATABASE_URL, reason="needs a live DATABASE_URL - see module docstring")
class TestPersistence:
    """save_to_db() (PLAN.md WP2) against the live DB - table creation,
    row counts, the delete-then-insert-not-upsert behavior, and the
    skip-with-a-warning path for an unresolvable company name."""

    def test_ensure_forensics_table_is_idempotent(self, forensics, db_engine):
        forensics.ensure_forensics_table(db_engine)
        forensics.ensure_forensics_table(db_engine)  # must not raise the second time

    def test_save_to_db_stores_exactly_the_flags_computed(self, forensics, db_engine):
        """What is stored is what was computed: total, severity split and no NULL company_id. This used to assert a
        hard-coded 62 (26/12/24) 'documented in the README' - the count of the 11-company universe. With 16 companies
        it had been failing since the universe grew (86-87 flags), and the README's number is generated from the
        database now (scripts/doc_facts.py), so a second copy here could only go stale again."""
        forensics.ensure_forensics_table(db_engine)
        flags, wide = _pipeline_flags(forensics, db_engine)

        n_saved = forensics.save_to_db(db_engine, flags, evaluated_companies=wide["company"].unique())
        assert n_saved == len(flags)

        with db_engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM forensics_flag")).scalar()
            by_severity = dict(conn.execute(text(
                "SELECT severity, COUNT(*) FROM forensics_flag GROUP BY severity"
            )).fetchall())
            null_company_id = conn.execute(text(
                "SELECT COUNT(*) FROM forensics_flag WHERE company_id IS NULL"
            )).scalar()

        assert total == len(flags), f"stored {total} flags, computed {len(flags)}"
        assert by_severity == {k: int(v) for k, v in flags["severity"].value_counts().items()}
        assert null_company_id == 0

    def test_rerunning_save_to_db_is_idempotent_not_additive(self, forensics, db_engine):
        """Delete-then-insert, not upsert (see save_to_db's docstring) -
        running it twice must leave the SAME row count, not double it."""
        flags, wide = _pipeline_flags(forensics, db_engine)
        evaluated = wide["company"].unique()

        forensics.save_to_db(db_engine, flags, evaluated_companies=evaluated)
        with db_engine.connect() as conn:
            first_count = conn.execute(text("SELECT COUNT(*) FROM forensics_flag")).scalar()

        forensics.save_to_db(db_engine, flags, evaluated_companies=evaluated)
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


# ---------------------------------------------------------------- save_to_db scope (DB-free)
#
# Real bug: save_to_db() deleted old rows only for companies PRESENT in the new
# flag list. A company whose flags all stopped triggering is absent from that
# list, so its stale flags were never deleted - Adyen kept three (one HIGH)
# computed from ratios that had since been blanked, through a clean re-run.

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, companies):
        self.companies = companies
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "FROM company" in sql:
            return _Rows([(cid, name) for name, cid in self.companies.items()])
        return _Rows([])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, companies):
        self.conn = _FakeConn(companies)

    def begin(self):
        return self.conn


def _flag(company, year=2024):
    return {"company": company, "year": year, "flag_id": "HIGH_LEVERAGE", "label": "x",
            "severity": "high", "value": 1.0, "detail": "d", "what_to_check": "w"}


class TestSaveToDbScope:
    COMPANIES = {"Alpha": 1, "Adyen": 2, "Gamma": 3}

    def deleted_ids(self, engine):
        return [p["ids"] for s, p in engine.conn.calls if s.startswith("DELETE FROM forensics_flag")]

    def test_a_company_that_now_has_no_flags_is_still_cleared(self, forensics):
        engine = _FakeEngine(self.COMPANIES)
        n = forensics.save_to_db(engine, pd.DataFrame([_flag("Alpha")]),
                                 evaluated_companies=["Alpha", "Adyen", "Gamma"])
        assert [sorted(ids) for ids in self.deleted_ids(engine)] == [[1, 2, 3]]   # Adyen, Gamma: no new flags
        assert n == 1                                                              # ...only Alpha's is inserted

    def test_default_scope_is_unchanged_for_existing_callers(self, forensics):
        engine = _FakeEngine(self.COMPANIES)
        forensics.save_to_db(engine, pd.DataFrame([_flag("Alpha")]))
        assert self.deleted_ids(engine) == [[1]]

    def test_no_flags_at_all_still_clears_the_evaluated_companies(self, forensics):
        engine = _FakeEngine(self.COMPANIES)
        empty = pd.DataFrame(columns=["company", "year", "flag_id", "label", "severity",
                                      "value", "detail", "what_to_check"])
        assert forensics.save_to_db(engine, empty, evaluated_companies=["Adyen"]) == 0
        assert self.deleted_ids(engine) == [[2]]

    def test_nothing_to_do_touches_nothing(self, forensics):
        engine = _FakeEngine(self.COMPANIES)
        assert forensics.save_to_db(engine, pd.DataFrame(), evaluated_companies=[]) == 0
        assert engine.conn.calls == []

    def test_flags_are_only_written_for_companies_with_flags(self, forensics):
        engine = _FakeEngine(self.COMPANIES)
        forensics.save_to_db(engine, pd.DataFrame([_flag("Alpha"), _flag("Gamma", 2023)]),
                             evaluated_companies=["Alpha", "Adyen", "Gamma"])
        inserted = [p["company_id"] for s, p in engine.conn.calls if s.lstrip().startswith("INSERT INTO forensics_flag")]
        assert sorted(inserted) == [1, 3]

    def test_an_unknown_evaluated_company_is_skipped_loudly_not_fatal(self, forensics, capsys):
        engine = _FakeEngine(self.COMPANIES)
        forensics.save_to_db(engine, pd.DataFrame([_flag("Alpha")]), evaluated_companies=["Ghost"])
        assert self.deleted_ids(engine) == [[1]]
        assert "no company_id found for 'Ghost'" in capsys.readouterr().out
