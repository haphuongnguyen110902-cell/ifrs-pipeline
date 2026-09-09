"""
tests/test_migration_company_id.py

WHAT
----
Verifies the WP1 migration (sql/migration_001_company_id.sql, see
PLAN.md) actually left the database in the state it's supposed to:
every row in the five migrated tables (`valuation`, `dcf_valuation`,
`market_risk`, `credit_profile`, `three_statement_projection`) has a
non-null `company_id`, and each table has a real foreign-key constraint
to `company(company_id)`.

WHY THIS TEST IS SKIPPED WITHOUT DATABASE_URL
----------------------------------------------
Same deliberate exception as tests/test_baseline_regression.py (see its
docstring) - this test only means something against the live database
migration_001_company_id.sql actually ran against, so it's skipped, not
failed, when DATABASE_URL is absent, keeping tests.yml's no-DB-secret
CI run unaffected.
"""
import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "test_migration_company_id.py needs a live DATABASE_URL by design "
        "(see module docstring) - skipped, not failed, when absent."
    ),
)

MIGRATED_TABLES = [
    "valuation",
    "dcf_valuation",
    "market_risk",
    "credit_profile",
    "three_statement_projection",
]


@pytest.fixture(scope="module")
def engine():
    return create_engine(DATABASE_URL)


@pytest.mark.parametrize("table_name", MIGRATED_TABLES)
def test_no_null_company_id(engine, table_name):
    """Every row must have a company_id - the migration's own guard should
    have already enforced this at the DB level (NOT NULL constraint), but
    checking it here as well means a future manual INSERT that somehow
    bypasses the constraint is still caught by the test suite."""
    with engine.connect() as conn:
        count = conn.execute(
            text(f"SELECT COUNT(*) FROM {table_name} WHERE company_id IS NULL")
        ).scalar()
    assert count == 0, (
        f"{table_name}: {count} row(s) with a NULL company_id - the WP1 "
        f"backfill guard should have prevented this; investigate before "
        f"trusting any query against this table."
    )


@pytest.mark.parametrize("table_name", MIGRATED_TABLES)
def test_company_id_foreign_key_exists(engine, table_name):
    """Confirms the FK constraint from sql/migration_001_company_id.sql is
    actually in place, not just that the column happens to hold valid
    values right now."""
    with engine.connect() as conn:
        # to_regclass(:t) rather than :t::regclass - the :: cast syntax
        # collides with SQLAlchemy's own ':name' bind-param syntax.
        row = conn.execute(text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = to_regclass(:t) AND contype = 'f' "
            "AND pg_get_constraintdef(oid) LIKE '%company_id%REFERENCES company%'"
        ), {"t": table_name}).fetchone()
    assert row is not None, (
        f"{table_name}: no foreign key on company_id referencing company(company_id) found."
    )


@pytest.mark.parametrize("table_name", MIGRATED_TABLES)
def test_company_id_matches_company_name(engine, table_name):
    """Belt-and-braces: for every row, the still-present `company` TEXT
    column and the new `company_id` FK must point at the SAME company.
    A mismatch here would mean the backfill matched the wrong row -
    worse than a NULL, since it would silently look right."""
    with engine.connect() as conn:
        mismatches = conn.execute(text(
            f"SELECT t.company, c.name FROM {table_name} t "
            f"JOIN company c ON c.company_id = t.company_id "
            f"WHERE c.name != t.company"
        )).fetchall()
    assert not mismatches, (
        f"{table_name}: {len(mismatches)} row(s) where company_id points to a "
        f"different company than the `company` TEXT column says - e.g. "
        f"{mismatches[:5]}."
    )
