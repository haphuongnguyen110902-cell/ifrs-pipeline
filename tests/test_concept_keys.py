"""
tests/test_concept_keys.py

New concepts used to be named by appending "_x" while the name was taken in the SAME statement. That produced 69
"_x"/"_x_x" near-duplicates (removed 2026-09-22) and could reuse a name from ANOTHER statement - concept names are
unique across the whole ifrs_concept table, so the loader would have filed the new tag under that other concept.
One rule now lives in scripts/concept_keys.py. No database.
"""
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"


@pytest.fixture(scope="module")
def ck(load_script):
    return load_script("concept_keys.py")


EXISTING = {"balance_sheet": {"autres_reserves": {}, "autres_reserves_el": {}},
            "cash_flow": {"tresorerie_nette": {}},
            "income_statement": {}}


def test_a_free_name_is_kept(ck):
    assert ck.unique_concept_key(EXISTING, "revenue_new", "ifrs-full:RevenueNew") == "revenue_new"


def test_a_name_taken_in_another_statement_is_not_reused(ck):
    """The silent-merge case: 'tresorerie_nette' exists under cash_flow, the new tag is a balance-sheet one."""
    assert ck.unique_concept_key(EXISTING, "tresorerie_nette", "dan:TresorerieNette2") == "tresorerie_nette_dan"


def test_a_taken_name_gets_the_filers_prefix_then_a_number(ck):
    assert ck.unique_concept_key(EXISTING, "autres_reserves", "el:AutresReserves.") == "autres_reserves_el_2"
    assert ck.unique_concept_key(EXISTING, "autres_reserves", "ifrs-full:OtherReserves") == "autres_reserves_ifrsfull"


def test_punctuation_from_a_tag_name_never_reaches_a_concept_name(ck):
    assert ck.unique_concept_key(EXISTING, "autres_reserves.", "el:AutresReserves.") == "autres_reserves_el_2"
    assert ck.unique_concept_key(EXISTING, "changes_in_other_non-_financial_assets", "el:X") == \
        "changes_in_other_non_financial_assets"


def test_no_script_that_writes_the_mapping_appends_x_any_more():
    for name in ("12_apply_review.py", "12_prep_company.py", "13_batch_prep.py"):
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert '+= "_x"' not in src and "unique_concept_key" in src, name
