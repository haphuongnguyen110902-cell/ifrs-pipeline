"""
tests/test_mapping_consistency_live.py

The stored facts must agree with the mapping file. fact_value is unique per (filing, period, CONCEPT), not per tag, so
re-loading a filing after a tag's mapping changed adds a row under the new concept and leaves the old row behind (the
loader never deletes). Found 2026-09-23: Recordati's two borrowings tags had 8 such leftovers - each an exact copy of a
row already under longterm/shortterm_borrowings - which 32_remap_facts.py could not move (the copy was in the way).
concept_mapping (one row per tag, first mapping wins) had drifted the same way and was then left incomplete by a
cleanup that deleted rows instead of re-pointing them. Read-only; skipped without DATABASE_URL, like test_screener.
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")
MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs a live DATABASE_URL - see module docstring")


@pytest.fixture(scope="module")
def engine():
    return create_engine(DATABASE_URL)


@pytest.fixture(scope="module")
def m09(load_script):
    return load_script("09_batch_load.py")


@pytest.fixture(scope="module")
def lookup(m09):
    return m09.load_mapping(str(MAPPING))


def test_every_fact_is_under_the_concept_the_mapping_gives_its_tag(engine, lookup, m09, load_script):
    m32 = load_script("32_remap_facts.py")
    with engine.connect() as conn:
        drift = m32.find_drift(conn, lookup, overrides=m09.load_overrides())
    assert drift.empty, drift.to_string()


def test_no_filing_period_and_tag_is_stored_under_two_concepts(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT filing_id, period_id, raw_xbrl_tag, COUNT(DISTINCT concept_id) FROM fact_value
            WHERE raw_xbrl_tag NOT LIKE 'note:%'
            GROUP BY filing_id, period_id, raw_xbrl_tag HAVING COUNT(DISTINCT concept_id) > 1""")).fetchall()
    assert not rows, rows


def test_concept_mapping_is_complete_and_agrees_with_the_mapping_file(engine, lookup, m09):
    overrides = m09.load_overrides()
    with engine.connect() as conn:
        registry = dict(conn.execute(text("""SELECT cm.xbrl_tag, ic.normalized_name FROM concept_mapping cm
                                             JOIN ifrs_concept ic ON ic.concept_id = cm.concept_id""")).fetchall())
        loaded = conn.execute(text("""SELECT DISTINCT c.name, fv.raw_xbrl_tag FROM fact_value fv
                                      JOIN filing f ON f.filing_id = fv.filing_id
                                      JOIN company c ON c.company_id = f.company_id
                                      WHERE fv.raw_xbrl_tag NOT LIKE 'note:%'""")).fetchall()
    disagree = {t: (n, lookup[t][0]) for t, n in registry.items() if t in lookup and lookup[t][0] != n}
    missing = sorted({t for co, t in loaded if (co, t) not in overrides and t not in registry})
    assert not disagree, disagree
    assert not missing, missing
