"""
tests/test_coverage_live.py

Phase 5 (coverage and alignment) against the live database: the goal agreed on 2026-09-23 was every company of the
registry in the app, each on its latest annual report, every figure from its printed report. Phase 4 found, running
every stage on 51 companies, the ways that silently stops being true - a year the engine no longer produces still in the
ratio table (Unilever's "2026" of a dividend date), the previous year's valuation beside the current one (Kering's P/E
9.4 beside 388.6), a missing multiple stored as the number 'NaN', a reviewed override whose tag no longer matches any
fact (Peab renamed its prefix between two reports). Each is a check here.

Skipped without DATABASE_URL (CI has none); read-only.
"""
import importlib.util
import os
from pathlib import Path

import pandas as pd
import pytest
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs the live database (DATABASE_URL)")

REPO = Path(__file__).parent.parent
REGISTRY = REPO / "data" / "companies.yaml"
OVERRIDES = REPO / "data" / "mappings" / "company_tag_overrides.yaml"
NOTE_FIGURES = REPO / "data" / "mappings" / "reviewed_note_figures.yaml"
ANALYTICS = ["ratio", "credit_profile", "forensics_flag", "forecast", "backtest", "valuation", "market_risk",
             "three_statement_projection", "dcf_valuation"]

# Companies whose latest loaded report is a year behind the universe, each with its reason (PLAN.md, Phase 4). A company
# that catches up - or one that falls behind - fails the test, so this list cannot rot.
EXPECTED_BEHIND = {
    "Syensqo": "FY2025 not on filings.xbrl.org; its own site refuses scripts (HTTP 403)",
    "Melexis": "FY2025 not on filings.xbrl.org; its own site sits behind a bot check",
    "Sandvik": "FY2025 not on filings.xbrl.org; its site publishes a plain .xbrl instance, not a report package",
    "Dometic Group": "FY2025 not on filings.xbrl.org; its site links the FY2024 package under 2025",
    "JM": "FY2025 not on filings.xbrl.org; no official link found",
    "Svenska Handelsbanken": "FY2025 not on filings.xbrl.org; no official link found",
}


@pytest.fixture(scope="module")
def engine():
    return create_engine(DATABASE_URL)


@pytest.fixture(scope="module")
def r11():
    spec = importlib.util.spec_from_file_location("r11_cov", REPO / "scripts" / "11_ratio_engine.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def registry():
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["companies"]


@pytest.fixture(scope="module")
def facts(engine, r11):
    return r11.fetch_facts(engine)


def test_the_database_holds_exactly_the_registry(engine, registry):
    db = pd.read_sql(text("SELECT name, lei FROM company"), engine)
    reg = {(e["name"], e["lei"]) for e in registry.values()}
    assert sorted(set(db["name"]) ^ {n for n, _ in reg}) == []
    assert sorted(set(zip(db["name"], db["lei"])) - reg) == [], "a company's LEI differs from the registry"


def test_every_company_has_facts_and_ratios(engine, registry):
    counts = pd.read_sql(text("""
        SELECT c.name,
               (SELECT COUNT(*) FROM fact_value v JOIN filing f ON f.filing_id = v.filing_id
                 WHERE f.company_id = c.company_id) AS facts,
               (SELECT COUNT(*) FROM ratio r WHERE r.company_id = c.company_id) AS ratios
        FROM company c"""), engine)
    assert counts[(counts["facts"] == 0) | (counts["ratios"] == 0)]["name"].tolist() == []


def test_each_company_latest_year_is_its_newest_report(facts, engine, r11):
    """The latest year shown is the fiscal year of the newest report loaded - not a later stray year, not an earlier
    one (a latest year that stops being computed would leave the dashboard on an old year)."""
    own = r11.filing_reporting_years(facts)                      # {filing_id: the year it is about}
    newest = facts.assign(own=facts["filing_id"].map(own)).groupby("company")["own"].max()
    latest = pd.read_sql(text("SELECT c.name AS company, MAX(r.year) AS year FROM ratio r "
                              "JOIN company c USING (company_id) GROUP BY c.name"), engine).set_index("company")["year"]
    off = {c: (int(latest[c]), int(newest[c])) for c in latest.index if int(latest[c]) != int(newest[c])}
    assert off == {}, f"latest ratio year != newest report's year: {off}"


def test_the_companies_behind_are_exactly_the_documented_ones(engine):
    latest = pd.read_sql(text("SELECT c.name, MAX(r.year) AS year FROM ratio r JOIN company c USING (company_id) "
                              "GROUP BY c.name"), engine)
    reference = int(latest["year"].mode().iloc[0])              # the year most companies' latest report covers
    behind = set(latest.loc[latest["year"] < reference, "name"])
    assert behind == set(EXPECTED_BEHIND), (
        f"behind {reference}: caught up {sorted(set(EXPECTED_BEHIND) - behind)}, "
        f"newly behind {sorted(behind - set(EXPECTED_BEHIND))} - update EXPECTED_BEHIND with the reason")


def test_the_ratio_table_holds_only_what_the_engine_produces(facts, engine, r11):
    wide = r11.pivot_to_wide(facts)
    produced = set(zip(wide["company_id"].astype(int), wide["year"].astype(int)))
    stored = pd.read_sql(text("SELECT DISTINCT company_id, year FROM ratio"), engine)
    stale = set(zip(stored["company_id"].astype(int), stored["year"].astype(int))) - produced
    assert sorted(stale) == []


def test_no_number_is_stored_as_nan(engine):
    """PostgreSQL keeps a NaN written into a NUMERIC column as the value 'NaN'; every reader testing for NULL takes it
    for a figure. A missing number must be NULL."""
    insp = inspect(engine)
    hits = {}
    with engine.connect() as conn:
        for table in ANALYTICS:
            for col in insp.get_columns(table):
                kind = str(col["type"]).upper()
                if kind.startswith("NUMERIC") or kind.startswith("DOUBLE") or kind in ("REAL", "FLOAT"):
                    literal = "'NaN'::numeric" if kind.startswith("NUMERIC") else "'NaN'::float8"
                    n = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col['name']} = {literal}")).scalar()
                    if n:
                        hits[f"{table}.{col['name']}"] = n
    assert hits == {}


def test_one_current_valuation_and_dcf_per_company(engine):
    """A valuation is today's market value over the latest year; an older year's row is a stale snapshot
    (20_precedents.py reads every row)."""
    for table, year in (("valuation", "year"), ("dcf_valuation", "base_year")):
        dup = pd.read_sql(text(f"SELECT company_id, COUNT(*) AS n FROM {table} GROUP BY company_id HAVING COUNT(*) > 1"),
                          engine)
        assert dup.empty, f"{table}: several rows for company ids {dup['company_id'].tolist()}"


def test_every_reviewed_override_matches_a_stored_fact(engine):
    """An entry whose tag matches no fact of its company corrects nothing - a renamed prefix (Peab: peabab: -> peab:) or
    a typo leaves the line under the wrong concept without a word."""
    entries = yaml.safe_load(OVERRIDES.read_text(encoding="utf-8"))["overrides"]
    stored = pd.read_sql(text("""SELECT DISTINCT c.name AS company, v.raw_xbrl_tag AS tag FROM fact_value v
                                 JOIN filing f ON f.filing_id = v.filing_id JOIN company c ON c.company_id = f.company_id"""),
                         engine)
    have = set(zip(stored["company"], stored["tag"]))
    dead = [(e["company"], e["tag"]) for e in entries if (e["company"], e["tag"]) not in have]
    assert dead == []


def test_every_reviewed_note_figure_is_stored(engine):
    figures = yaml.safe_load(NOTE_FIGURES.read_text(encoding="utf-8"))["figures"]
    stored = set(pd.read_sql(text("SELECT DISTINCT raw_xbrl_tag FROM fact_value WHERE raw_xbrl_tag LIKE 'note:%'"),
                             engine)["raw_xbrl_tag"])
    missing = [f["id"] for f in figures if f"note:{f['id']}" not in stored]
    assert missing == []
