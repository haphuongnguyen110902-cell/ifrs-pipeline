"""
tests/test_engine_concepts_exist.py

Every concept the ratio engine reads must be a concept the mapping can fill. Two silent gaps were found by hand on
2026-09-28:
  * `bank_operating_expenses` and `operating_profit_before_impairment` were read for the banks' cost/income ratio but
    defined nowhere in ifrs_concepts_v0.yaml, so no bank could ever supply them (BNP's and KBC's cost/income stayed
    blank);
  * Danone's "profit before tax, before associates" had been read under a name that stopped existing when duplicate
    concepts were removed (PR #69): its facts live under the `_x` key and under `dan_...`.
A name read but never mapped is a figure that is always blank without saying so. The names are recorded by running the
engine with its two readers wrapped (as scripts/35_trace_figures.py does), plus the debt lines it reads by name. No
database.
"""
from pathlib import Path

import pandas as pd
import pytest
import yaml

MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


def names_the_engine_reads(r11) -> set:
    asked = set()
    orig_col, orig_best = r11.get_col, r11.get_best

    def get_col(w, name):
        asked.add(name)
        return orig_col(w, name)

    def get_best(w, *names):
        asked.update(names)
        return orig_best(w, *names)

    r11.get_col, r11.get_best = get_col, get_best
    try:
        r11._compute_ratios(pd.DataFrame([{"company": "X", "company_id": 1, "year": 2025}]))
    finally:
        r11.get_col, r11.get_best = orig_col, orig_best
    asked |= set(r11.NONCURRENT_DEBT_LINES + r11.CURRENT_DEBT_LINES)
    asked |= set(r11.FINANCIAL_LIABILITY_TOTALS) | set(r11.UNCLASSIFIED_DEBT_TOTALS)
    return asked


def test_the_recorder_sees_the_engine_s_reads(r11):
    """Guard on the guard: a handful of names every company path reads must be recorded."""
    names = names_the_engine_reads(r11)
    assert {"revenue", "cost_of_sales", "cash_and_cash_equivalents", "revenue_and_operating_income",
            "bank_operating_expenses", "longterm_borrowings"} <= names and len(names) > 50


def test_every_concept_the_engine_reads_is_in_the_mapping(r11):
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    known = {name for concepts in mapping.values() for name in concepts}
    assert sorted(names_the_engine_reads(r11) - known) == []
