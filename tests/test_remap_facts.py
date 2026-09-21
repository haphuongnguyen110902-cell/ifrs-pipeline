"""
tests/test_remap_facts.py

scripts/32_remap_facts.py re-points already-loaded facts when the mapping file assigns their tag to a different
concept, and the mapping file now sends the five extension "purchase of PP&E and intangibles" tags of EssilorLuxottica,
Kering, L'Oreal and Pernod Ricard to one canonical concept the capex logic reads (their capex had been falling back to a
flat 3% of revenue: Kering 2023 587M assumed vs 2,611M printed). In-memory SQLite, no live database.
"""
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"
CANON = "purchase_of_property_plant_and_equipment_and_intangible_asse_etc"
TAG_EL = "el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles"
OLD_EL = "acquisitions_dimmobilisations_corporelles_et_incorporelles_el_x"
LOOKUP = {TAG_EL: (CANON, "cash_flow", "Purchase of PP&E and intangibles"),
          "ifrs-full:Inventories": ("inventories", "balance_sheet", "Inventories")}


@pytest.fixture(scope="module")
def m32(load_script):
    return load_script("32_remap_facts.py")


@pytest.fixture
def db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        for ddl in (
            "CREATE TABLE company (company_id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE filing (filing_id INTEGER PRIMARY KEY, company_id INTEGER)",
            "CREATE TABLE ifrs_concept (concept_id INTEGER PRIMARY KEY, normalized_name TEXT UNIQUE, "
            "statement TEXT, display_label TEXT)",
            "CREATE TABLE concept_mapping (mapping_id INTEGER PRIMARY KEY, concept_id INTEGER, xbrl_tag TEXT)",
            "CREATE TABLE fact_value (value_id INTEGER PRIMARY KEY, filing_id INTEGER, period_id INTEGER, "
            "concept_id INTEGER, raw_xbrl_tag TEXT, value REAL)"):
            c.execute(text(ddl))
        c.execute(text("INSERT INTO company VALUES (1,'EssilorLuxottica'),(2,'Other')"))
        c.execute(text("INSERT INTO filing VALUES (10,1),(11,1),(20,2)"))
        c.execute(text(f"INSERT INTO ifrs_concept VALUES (1,'{OLD_EL}','cash_flow','old'),(2,'inventories','balance_sheet','Inv')"))
        c.execute(text(f"INSERT INTO concept_mapping VALUES (1,1,'{TAG_EL}')"))
        c.execute(text(f"""INSERT INTO fact_value VALUES
            (1,10,100,1,'{TAG_EL}',1522.0), (2,11,110,1,'{TAG_EL}',1400.0), (3,20,200,1,'{TAG_EL}',5.0),
            (4,10,100,2,'ifrs-full:Inventories',3152.0), (5,10,100,2,'x:NotInTheMapping',9.0)"""))
    return e


def concepts_of(engine):
    with engine.connect() as c:
        return {r[0]: r[1] for r in c.execute(text(
            "SELECT value_id, (SELECT normalized_name FROM ifrs_concept WHERE concept_id = f.concept_id) FROM fact_value f"))}


class TestFindDrift:
    def test_lists_only_facts_whose_tag_maps_elsewhere(self, m32, db):
        with db.connect() as c:
            d = m32.find_drift(c, LOOKUP)
        assert set(d["tag"]) == {TAG_EL}
        assert set(d["company"]) == {"EssilorLuxottica", "Other"}
        assert d["n_facts"].sum() == 3 and set(d["target_concept"]) == {CANON}

    def test_a_tag_absent_from_the_mapping_is_never_touched(self, m32, db):
        with db.connect() as c:
            assert "x:NotInTheMapping" not in set(m32.find_drift(c, LOOKUP)["tag"])

    def test_company_scope(self, m32, db):
        with db.connect() as c:
            d = m32.find_drift(c, LOOKUP, "Other")
        assert d["n_facts"].sum() == 1


class TestApply:
    def test_moves_facts_creates_the_target_and_follows_with_the_mapping_row(self, m32, db):
        with db.begin() as c:
            out = m32.apply_drift(c, LOOKUP, m32.find_drift(c, LOOKUP))
        assert out["moved"].sum() == 3 and out["skipped_clash"].sum() == 0
        cc = concepts_of(db)
        assert cc[1] == cc[2] == cc[3] == CANON and cc[4] == "inventories" and cc[5] == "inventories"
        with db.connect() as c:
            assert c.execute(text(f"SELECT c.normalized_name FROM concept_mapping m JOIN ifrs_concept c "
                                  f"ON c.concept_id = m.concept_id WHERE m.xbrl_tag = '{TAG_EL}'")).scalar() == CANON
            assert c.execute(text(f"SELECT COUNT(*) FROM ifrs_concept WHERE normalized_name = '{OLD_EL}'")).scalar() == 1

    def test_is_idempotent(self, m32, db):
        with db.begin() as c:
            m32.apply_drift(c, LOOKUP, m32.find_drift(c, LOOKUP))
        with db.connect() as c:
            assert m32.find_drift(c, LOOKUP).empty

    def test_a_fact_that_would_collide_is_skipped_and_reported_not_deleted(self, m32, db):
        with db.begin() as c:
            c.execute(text("INSERT INTO ifrs_concept VALUES (3,:n,'cash_flow','new')"), {"n": CANON})
            c.execute(text(f"INSERT INTO fact_value VALUES (6,10,100,3,'other:Twin',1522.0)"))   # same filing+period, target concept
        with db.begin() as c:
            out = m32.apply_drift(c, LOOKUP, m32.find_drift(c, LOOKUP))
        assert out["skipped_clash"].sum() == 1 and out["moved"].sum() == 2
        assert concepts_of(db)[1] == OLD_EL                       # left where it was, still there
        with db.connect() as c:                                   # the mapping row stays: a fact still needs the old concept
            assert c.execute(text(f"SELECT concept_id FROM concept_mapping WHERE xbrl_tag = '{TAG_EL}'")).scalar() == 1

    def test_company_scope_moves_only_that_company_and_leaves_the_mapping_row(self, m32, db):
        with db.begin() as c:
            m32.apply_drift(c, LOOKUP, m32.find_drift(c, LOOKUP, "EssilorLuxottica"), "EssilorLuxottica")
        cc = concepts_of(db)
        assert cc[1] == cc[2] == CANON and cc[3] == OLD_EL
        with db.connect() as c:
            assert c.execute(text(f"SELECT concept_id FROM concept_mapping WHERE xbrl_tag = '{TAG_EL}'")).scalar() == 1


class TestTheRealMappingFile:
    @pytest.fixture(scope="class")
    def lookup(self, m32):
        return m32.load_lookup(MAPPING)

    @pytest.mark.parametrize("tag", [
        "el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles",
        "el:PurchaseOfPpeAndIntangible",
        "kering:PurchaseOfPropertyPlantEquipmentAndIntangibleAssetsOtherThanGoodwill",
        "loreal:AcquisitionsDImmobilisationsCorporellesEtIncorporelles",
        "pernod:AcquisitionDimmobilisationsCorporellesEtIncorporellesAutresQueLeGoodwill",
        "puig:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssetsClassifiedAsInvestingActivities"])
    def test_combined_capex_lines_share_one_concept(self, lookup, tag):
        assert lookup[tag][0] == CANON

    def test_no_one_off_capex_concept_shadows_them_any_more(self):
        import yaml
        d = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
        names = {n for sec in d.values() for n in sec}
        for gone in (OLD_EL, "el_purchase_of_ppe_and_intangible", "purchase_of_property_plant_equipment_and_intangible_assets_o_etc",
                     "purchase_of_property_plant_equipment_and_intangible_assets_o_etc_x",
                     "acquisitions_d_immobilisations_corporelles_et_incorporelles",
                     "acquisition_dimmobilisations_corporelles_et_incorporelles_au_etc_x"):
            assert gone not in names

    def test_the_capex_logic_reads_the_canonical_concept(self, load_script):
        assert CANON in load_script("21_three_statement_model.py").CAPEX_CONCEPTS
