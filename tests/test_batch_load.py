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
