"""
tests/test_batch_load.py

Regression tests for scripts/09_batch_load.py's get_or_create_company().

NOTE ON THIS FILE SPECIFICALLY: 09_batch_load.py imports arelle at module
level (needed for XBRL parsing), which is a heavy dependency not
installed in the sandbox this test file was originally written in - so
unlike every other file in tests/, this one could NOT be self-verified
before being committed. It WILL run wherever arelle is already installed
(i.e. this project's normal dev environment, per requirements.txt) - but
if this file specifically fails on first CI run, start by checking
whether it's an arelle-availability problem in the CI environment before
assuming the test logic itself is wrong.

A minimal FakeCursor stands in for a real psycopg2 cursor, so this
doesn't need a live database - it only checks WHICH SQL statements get
issued (SELECT/INSERT/UPDATE), not that a real Postgres accepts them.
"""
import pytest


class FakeCursor:
    """Minimal psycopg2-cursor look-alike: tracks which SQL statement
    type was issued and simulates an in-memory `company` table keyed by
    name, just enough for get_or_create_company()'s logic to run against."""

    def __init__(self):
        self.calls = []
        self.existing = {}  # name -> company_id
        self.next_id = 1
        self._last_result = None

    def execute(self, sql, params=None):
        kind = sql.strip().split()[0]
        self.calls.append((kind, params))
        if "SELECT company_id FROM company WHERE name" in sql:
            self._last_result = self.existing.get(params[0])
        elif "INSERT INTO company" in sql:
            name = params[0]
            self.existing[name] = self.next_id
            self._last_result = ("insert", self.next_id)
            self.next_id += 1
        elif "UPDATE company" in sql:
            self._last_result = None

    def fetchone(self):
        if isinstance(self._last_result, tuple) and self._last_result[0] == "insert":
            return (self._last_result[1],)
        return (self._last_result,) if self._last_result else None


@pytest.fixture(scope="module")
def b09(load_script):
    return load_script("09_batch_load.py")


def test_new_company_is_inserted_with_sector_and_country(b09):
    cur = FakeCursor()
    company_id = b09.get_or_create_company(cur, "TestCo", sector="Luxury Goods", country="France")
    assert company_id == 1
    kinds = [c[0] for c in cur.calls]
    assert kinds == ["SELECT", "INSERT"]


def test_existing_company_with_new_metadata_gets_backfilled_via_update(b09):
    """The exact bug this fixes: sector/country existed in schema.sql and
    companies.yaml since V0, but get_or_create_company() only ever wrote
    `name` - every company's sector/country had been NULL since the first
    load. Re-running the loader after the fix must UPDATE existing rows,
    not silently do nothing because the company already exists."""
    cur = FakeCursor()
    id1 = b09.get_or_create_company(cur, "TestCo", sector="Luxury Goods", country="France")
    cur.calls.clear()
    id2 = b09.get_or_create_company(cur, "TestCo", sector="Luxury Goods", country="France")
    assert id1 == id2
    kinds = [c[0] for c in cur.calls]
    assert kinds == ["SELECT", "UPDATE"]


def test_no_metadata_given_does_not_issue_a_pointless_update(b09):
    """Callers with no sector/country to offer (or future callers that
    forget to pass it) shouldn't trigger an UPDATE with nothing new to
    write - avoids an unnecessary write on every single load run."""
    cur = FakeCursor()
    b09.get_or_create_company(cur, "TestCo", sector="Luxury Goods", country="France")
    cur.calls.clear()
    b09.get_or_create_company(cur, "TestCo")  # no sector/country this time
    kinds = [c[0] for c in cur.calls]
    assert kinds == ["SELECT"]
    assert "UPDATE" not in kinds


class TestFiscalYearEndParsing:
    """_parse_fiscal_year_end() (PLAN.md WP3c) turns companies.yaml's
    human-written "Month Day" string into (month, day) - the real case
    this was built for: Pernod Ricard's "June 30", which companies.yaml
    had documented since V0 but nothing ever read (same "data existed,
    never wired through" pattern as V2.6's sector/country fix)."""

    def test_parses_the_real_pernod_ricard_case(self, b09):
        assert b09._parse_fiscal_year_end("June 30") == (6, 30)

    def test_none_input_returns_none_none(self, b09):
        """Every OTHER company in companies.yaml simply has no
        fiscal_year_end key at all - config.get() returns None, which
        must mean "unknown", not crash."""
        assert b09._parse_fiscal_year_end(None) == (None, None)

    def test_malformed_string_returns_none_none_not_a_crash(self, b09):
        assert b09._parse_fiscal_year_end("not a date") == (None, None)


def test_new_company_with_fiscal_year_end_is_inserted_with_it(b09):
    cur = FakeCursor()
    b09.get_or_create_company(cur, "Pernod Ricard", fiscal_year_end="June 30")
    insert_params = [p for kind, p in cur.calls if kind == "INSERT"][0]
    assert insert_params == ("Pernod Ricard", None, None, 6, 30)


def test_existing_company_gets_fiscal_year_end_backfilled_via_update(b09):
    """The real bug this closes: get_or_create_filing() never sets
    filing.fiscal_year_end for a company loaded only through
    09_batch_load.py's single-filing path (not load_historical.py, which
    deliberately excludes Pernod Ricard) - found live: Pernod Ricard's
    company.fiscal_year_end_month/day came back NULL after
    migration_002's filing-derived backfill, because its one filing row
    has fiscal_year_end = NULL. This UPDATE path is what actually fixes
    it, from the same companies.yaml source V2.6 already trusts for
    sector/country."""
    cur = FakeCursor()
    b09.get_or_create_company(cur, "TestCo")
    cur.calls.clear()
    b09.get_or_create_company(cur, "TestCo", fiscal_year_end="June 30")
    kinds = [c[0] for c in cur.calls]
    assert kinds == ["SELECT", "UPDATE"]


def test_clear_company_facts_never_touches_historical_filings(b09):
    """Regression test for a REAL data-loss incident: an earlier,
    unscoped version of clear_company_facts() deleted ALL of a company's
    fact_value rows regardless of which filing loaded them. Running
    `09_batch_load.py --reset-facts` (recommended for an unrelated
    sector/country backfill) silently wiped 2017-2020 data that
    load_historical.py had loaded - discovered only when
    11_ratio_engine.py's year coverage collapsed from 2017-2025 back to
    2021-2025. Fixed by excluding any filing whose source_file contains
    'historical' - this test locks in that exclusion so a future
    refactor can't silently drop it again."""

    class SqlCapturingCursor:
        def __init__(self):
            self.last_sql = None
            self.last_params = None

        def execute(self, sql, params=None):
            self.last_sql = sql
            self.last_params = params

        rowcount = 0

    cur = SqlCapturingCursor()
    b09.clear_company_facts(cur, "Shell")
    assert "NOT LIKE" in cur.last_sql
    assert cur.last_params == ("Shell", "%historical%"), (
        "clear_company_facts must exclude filings whose source_file "
        "contains 'historical' - without this, --reset-facts wipes "
        "multi-year data loaded by load_historical.py"
    )
