"""
tests/test_batch_prep_anchoring.py

Regression tests for scripts/13_batch_prep.py's ESEF anchoring support
(PLAN.md's WP4b) - resolve_via_anchor() is pure (no Arelle model needed,
just a stub concept object with .qname/.periodType/.label()), so it's
testable without a real filing. build_anchor_map()/the wider-narrower
relationship-set reading itself is deliberately NOT unit-tested here -
that needs a real Arelle model with a real definition linkbase, and is
exercised live via `python scripts/13_batch_prep.py --dry-run --raw-dir
data/raw/gate40` instead (PLAN.md's WP4b write-up records the real
result), matching this project's established pattern of not mocking
Arelle/XBRL parsing in tests (see test_entity_resolution.py's docstring
for the same reasoning re: network calls).
"""
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def bp(load_script):
    return load_script("13_batch_prep.py")


def make_concept(qname, period_type="duration", label="Some Label"):
    return SimpleNamespace(qname=qname, periodType=period_type, label=lambda: label)


def test_prefers_existing_mapping_over_everything_else(bp):
    anchor = make_concept("ifrs-full:DividendsPaid", period_type="duration")
    stmt, reason = bp.resolve_via_anchor(anchor, pres_map={}, tag_to_statement={"ifrs-full:DividendsPaid": "cash_flow"})
    assert stmt == "cash_flow"
    assert "already in mapping" in reason


def test_falls_back_to_anchors_own_presentation_role(bp):
    anchor = make_concept("ifrs-full:SomeNewConcept", period_type="duration")
    pres_map = {"ifrs-full:SomeNewConcept": ("income_statement", "IFRS role [3xxxxx] - AUTHORITATIVE")}
    stmt, reason = bp.resolve_via_anchor(anchor, pres_map=pres_map, tag_to_statement={})
    assert stmt == "income_statement"
    assert "presentation role" in reason


def test_falls_back_to_anchors_own_period_type(bp):
    anchor = make_concept("ifrs-full:SomeBalanceConcept", period_type="instant")
    stmt, reason = bp.resolve_via_anchor(anchor, pres_map={}, tag_to_statement={})
    assert stmt == "balance_sheet"
    assert "periodType=instant" in reason


def test_falls_back_to_anchors_own_name_keyword(bp):
    anchor = make_concept("ifrs-full:ProceedsFromSomething", period_type="duration", label="Proceeds from something")
    stmt, reason = bp.resolve_via_anchor(anchor, pres_map={}, tag_to_statement={})
    assert stmt == "cash_flow"


def test_unresolvable_anchor_returns_empty_statement_not_a_guess(bp):
    anchor = make_concept("ifrs-full:SomethingGenericDuration", period_type="duration", label="Something generic")
    stmt, reason = bp.resolve_via_anchor(anchor, pres_map={}, tag_to_statement={})
    assert stmt == ""
    assert "unclassifiable" in reason


def test_scan_zips_signature_accepts_tag_to_statement(bp):
    """scan_zips() must accept the new tag_to_statement kwarg without
    breaking - a regression check for callers that don't pass it (the
    default None -> {} path)."""
    all_auto, all_review, per_company = bp.scan_zips([], existing_tags=set())
    assert all_auto == {}
    assert all_review == {}
    assert per_company == []
