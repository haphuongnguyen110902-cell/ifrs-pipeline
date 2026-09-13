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


def make_concept(qname, period_type="duration", label="Some Label", is_monetary=True):
    return SimpleNamespace(qname=qname, periodType=period_type, label=lambda: label, isMonetary=is_monetary)


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


def test_assess_materiality_flags_a_small_duration_tag(bp):
    concept = make_concept("x:Tiny", period_type="duration")
    is_immaterial, ratio, denom = bp.assess_materiality(concept, tag_value=500, revenue=1_000_000, assets=None)
    assert is_immaterial is True
    assert denom == "revenue"
    assert ratio == pytest.approx(0.0005)


def test_assess_materiality_does_not_flag_a_large_duration_tag(bp):
    concept = make_concept("x:Big", period_type="duration")
    is_immaterial, ratio, denom = bp.assess_materiality(concept, tag_value=50_000, revenue=1_000_000, assets=None)
    assert is_immaterial is False
    assert ratio == pytest.approx(0.05)


def test_assess_materiality_uses_assets_for_instant_concepts(bp):
    concept = make_concept("x:SmallBalance", period_type="instant")
    is_immaterial, ratio, denom = bp.assess_materiality(concept, tag_value=100, revenue=1_000_000, assets=10_000_000)
    assert is_immaterial is True
    assert denom == "assets"


def test_assess_materiality_never_guesses_with_no_data(bp):
    """No tag value, or no denominator to compare against - never silently
    call it immaterial (rule 7: an honest 'not available' state, not a
    guess dressed up as a screen)."""
    concept = make_concept("x:NoValue", period_type="duration")
    assert bp.assess_materiality(concept, tag_value=None, revenue=1_000_000, assets=None) == (False, None, None)

    concept2 = make_concept("x:NoRevenueFound", period_type="duration")
    assert bp.assess_materiality(concept2, tag_value=500, revenue=None, assets=None) == (False, None, None)


def test_find_scale_takes_the_largest_of_the_fallback_tags(bp):
    value_map = {"ifrs-full:Revenue": 900, "ifrs-full:RevenueFromContractsWithCustomers": 1200}
    assert bp.find_scale(value_map, bp.REVENUE_TAGS) == 1200


def test_find_scale_returns_none_when_nothing_found(bp):
    assert bp.find_scale({}, bp.REVENUE_TAGS) is None


def test_assess_materiality_never_compares_a_non_monetary_fact_to_revenue(bp):
    """Real bug found running this live: a share-count concept (unit=shares)
    was being divided by EUR revenue - a meaningless cross-unit ratio that
    happened to read as 0.00% and get silently waved through as
    'immaterial'. A non-monetary fact (share count, per-share ratio, pure
    number) must get NO materiality opinion at all, however large its raw
    numeric value, rather than a spurious currency comparison."""
    shares_concept = make_concept("x:SharesOutstandingChange", period_type="duration", is_monetary=False)
    is_immaterial, ratio, denom = bp.assess_materiality(shares_concept, tag_value=50_000_000, revenue=1_000_000, assets=None)
    assert is_immaterial is False
    assert ratio is None
    assert denom is None


def test_scan_zips_signature_accepts_tag_to_statement(bp):
    """scan_zips() must accept the new tag_to_statement kwarg without
    breaking - a regression check for callers that don't pass it (the
    default None -> {} path), and return the 4-tuple including
    all_immaterial (PLAN.md's WP4b materiality screen)."""
    all_auto, all_review, all_immaterial, per_company = bp.scan_zips([], existing_tags=set())
    assert all_auto == {}
    assert all_review == {}
    assert all_immaterial == {}
    assert per_company == []
