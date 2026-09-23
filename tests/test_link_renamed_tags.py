"""
tests/test_link_renamed_tags.py

scripts/34_link_renamed_tags.py links a company's renamed extension element to its predecessor's concept when the
values prove they are the same line. Found 2026-09-23: EssilorLuxottica's FY2025 report renamed its capex line
(el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles -> el:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets);
classified as a brand-new concept, FY2025 capex would have vanished from the engine. No database.
"""
import datetime

import pytest

D = datetime.date
CANON = "purchase_of_property_plant_and_equipment_and_intangible_asse_etc"
OLD, NEW = "el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles", "el:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets"
FY24, FY25 = (D(2024, 1, 1), D(2025, 1, 1)), (D(2025, 1, 1), D(2026, 1, 1))


@pytest.fixture(scope="module")
def lk(load_script):
    return load_script("34_link_renamed_tags.py")


def facts(*extra):
    """The FY2025 report: revenue for scale, the renamed capex line for FY2025 and its FY2024 comparative."""
    return [("ifrs-full:Revenue", *FY25, 28_491e6), ("ifrs-full:Revenue", *FY24, 26_508e6),
            (NEW, *FY25, 1_525e6), (NEW, *FY24, 1_522e6), *extra]


LOOKUP = {OLD: (CANON, "cash_flow", "x"), "ifrs-full:Revenue": ("revenue", "income_statement", "x")}
STORED = [(OLD, CANON, *FY24, 1_522e6), ("ifrs-full:Revenue", "revenue", *FY24, 26_508e6)]


def test_the_renamed_capex_line_is_linked_to_its_predecessor(lk):
    links, skipped = lk.propose_links(facts(), LOOKUP, STORED)
    assert links[NEW][0] == CANON and links[NEW][1][0][2:] == (1_522e6, OLD)


def test_a_value_shared_by_two_concepts_is_ambiguous(lk):
    stored = STORED + [("el:SomethingElse", "something_else", *FY24, 1_522e6)]
    links, skipped = lk.propose_links(facts(), LOOKUP, stored)
    assert NEW not in links and "ambiguous" in skipped[NEW]


def test_a_tiny_value_never_links(lk):
    """0.1% of the filing's revenue scale (28.5bn -> 28.5M) is the floor: a 7M coincidence is not evidence."""
    small = [("el:TinyNew", *FY24, 7e6)]
    stored = STORED + [("el:TinyOld", "tiny_old", *FY24, 7e6)]
    links, skipped = lk.propose_links(facts(*small), {**LOOKUP, "el:TinyOld": ("tiny_old", "x", "x")}, stored)
    assert "el:TinyNew" not in links and "no overlapping" in skipped["el:TinyNew"]


def test_a_standard_ifrs_tag_is_never_linked_to_a_company_concept(lk):
    std = [("ifrs-full:PurchaseOfPropertyPlantAndEquipment", *FY24, 1_522e6)]
    links, skipped = lk.propose_links(facts(*std), LOOKUP, STORED)
    assert "ifrs-full:PurchaseOfPropertyPlantAndEquipment" not in links and \
        "ifrs-full:PurchaseOfPropertyPlantAndEquipment" not in skipped


def test_periods_that_disagree_are_not_linked(lk):
    new = [("el:Moved", *FY24, 900e6), ("el:Moved", (D(2023, 1, 1)), D(2024, 1, 1), 800e6)]
    stored = STORED + [("el:A", "a", *FY24, 900e6), ("el:B", "b", D(2023, 1, 1), D(2024, 1, 1), 800e6)]
    links, skipped = lk.propose_links(facts(*new), LOOKUP, stored)
    assert "el:Moved" not in links and "disagree" in skipped["el:Moved"]
