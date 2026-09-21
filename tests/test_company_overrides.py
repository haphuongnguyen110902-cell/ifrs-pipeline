"""
tests/test_company_overrides.py

Puig Brands swapped two tags in its FY2024 balance sheet: its TRADE PAYABLES ("Proveedores y acreedores", 229.5M) are
tagged ifrs-full:CurrentTaxLiabilitiesCurrent and its INCOME-TAX PAYABLE ("Impuesto sobre sociedades a pagar", 47.6M)
is tagged ifrs-full:TradeAndOtherCurrentPayables. The engine read 47.6M as trade payables: DPO 14 days instead of about
64. Only the label the company prints reveals it, so the correction is a reviewed per-company override with evidence
(data/mappings/company_tag_overrides.yaml), applied by the loader and by the fact re-pointing tool. No database.
"""
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

OVERRIDES = Path(__file__).parent.parent / "data" / "mappings" / "company_tag_overrides.yaml"
MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"
TRADE, TAX = "ifrs-full:CurrentTaxLiabilitiesCurrent", "ifrs-full:TradeAndOtherCurrentPayables"


@pytest.fixture(scope="module")
def m09(load_script):
    return load_script("09_batch_load.py")


@pytest.fixture(scope="module")
def m32(load_script):
    return load_script("32_remap_facts.py")


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


class TestOverrideFile:
    def test_puig_entries_map_the_swapped_tags_to_what_the_company_prints(self, m09):
        ov = m09.load_overrides(OVERRIDES)
        assert ov[("Puig Brands", TRADE)][0] == "trade_and_other_current_payables_to_trade_suppliers"
        assert ov[("Puig Brands", TAX)][0] == "current_tax_liabilities_current"

    def test_every_entry_names_a_concept_that_exists_and_carries_its_evidence(self):
        import yaml
        known = {(n, st) for st, cs in yaml.safe_load(MAPPING.read_text(encoding="utf-8")).items() for n in cs}
        for e in yaml.safe_load(OVERRIDES.read_text(encoding="utf-8"))["overrides"]:
            assert (e["concept"], e["statement"]) in known, e
            assert e["printed_label"].strip() and len(e["evidence"]) > 60

    def test_the_override_applies_to_its_company_only(self, m09):
        lookup = m09.load_mapping(str(MAPPING))
        ov = m09.load_overrides(OVERRIDES)
        puig = m09.tag_lookup_for("Puig Brands", lookup, ov)
        assert puig[TRADE][0] == "trade_and_other_current_payables_to_trade_suppliers"
        other = m09.tag_lookup_for("Danone", lookup, ov)
        assert other is lookup and other[TRADE][0] == "current_tax_liabilities_current"
        assert lookup[TAX][0] == "trade_and_other_current_payables"          # the global mapping is untouched

    def test_an_entry_without_evidence_is_refused(self, m09, tmp_path):
        f = tmp_path / "o.yaml"
        f.write_text("overrides:\n- {company: X, tag: 'a:B', concept: c, statement: balance_sheet, label: L, printed_label: P}\n",
                     encoding="utf-8")
        with pytest.raises(ValueError, match="evidence"):
            m09.load_overrides(f)

    def test_a_duplicate_entry_is_refused(self, m09, tmp_path):
        e = "{company: X, tag: 'a:B', concept: c, statement: balance_sheet, label: L, printed_label: P, evidence: E}"
        f = tmp_path / "o.yaml"
        f.write_text(f"overrides:\n- {e}\n- {e}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="twice"):
            m09.load_overrides(f)

    def test_no_file_means_no_overrides(self, m09, tmp_path):
        assert m09.load_overrides(tmp_path / "missing.yaml") == {}


class TestEngineReadsTheCorrectedPayables:
    def test_puig_dpo_is_about_64_days_not_14(self, r11):
        base = {"company": "Puig", "company_id": 1, "year": 2024, "revenue": 4_790.0, "cost_of_sales": -1_300.0,
                "profit_loss_from_operating_activities": 700.0}
        before = r11.compute_ratios(pd.DataFrame([{**base, "trade_and_other_current_payables": 47.625}]))
        after = r11.compute_ratios(pd.DataFrame([{**base, "trade_and_other_current_payables_to_trade_suppliers": 229.492,
                                                  "current_tax_liabilities_current": 47.625}]))
        assert before["dpo"].iloc[0] == pytest.approx(13.4, abs=0.1)
        assert after["dpo"].iloc[0] == pytest.approx(64.4, abs=0.1)


class TestRemapWithOverrides:
    LOOKUP = {TRADE: ("current_tax_liabilities_current", "balance_sheet", "Current Tax Liabilities"),
              TAX: ("trade_and_other_current_payables", "balance_sheet", "Trade and Other Current Payables")}
    OV = {("Puig Brands", TRADE): ("trade_and_other_current_payables_to_trade_suppliers", "balance_sheet", "Trade Payables"),
          ("Puig Brands", TAX): ("current_tax_liabilities_current", "balance_sheet", "Current Tax Liabilities")}

    @pytest.fixture
    def db(self):
        e = create_engine("sqlite://")
        with e.begin() as c:
            for ddl in ("CREATE TABLE company (company_id INTEGER PRIMARY KEY, name TEXT)",
                        "CREATE TABLE filing (filing_id INTEGER PRIMARY KEY, company_id INTEGER)",
                        "CREATE TABLE ifrs_concept (concept_id INTEGER PRIMARY KEY, normalized_name TEXT UNIQUE, "
                        "statement TEXT, display_label TEXT)",
                        "CREATE TABLE concept_mapping (mapping_id INTEGER PRIMARY KEY, concept_id INTEGER, xbrl_tag TEXT)",
                        "CREATE TABLE fact_value (value_id INTEGER PRIMARY KEY, filing_id INTEGER, period_id INTEGER, "
                        "concept_id INTEGER, raw_xbrl_tag TEXT, value REAL)"):
                c.execute(text(ddl))
            c.execute(text("INSERT INTO company VALUES (1,'Puig Brands'),(2,'Danone')"))
            c.execute(text("INSERT INTO filing VALUES (10,1),(20,2)"))
            # as loaded from the global mapping: TRADE tag -> tax concept, TAX tag -> trade-and-other concept
            c.execute(text("INSERT INTO ifrs_concept VALUES (1,'current_tax_liabilities_current','balance_sheet','tax'),"
                           "(2,'trade_and_other_current_payables','balance_sheet','t&o')"))
            c.execute(text(f"INSERT INTO concept_mapping VALUES (1,1,'{TRADE}'),(2,2,'{TAX}')"))
            c.execute(text(f"""INSERT INTO fact_value VALUES
                (1,10,100,1,'{TRADE}',229492.0), (2,10,101,1,'{TRADE}',212072.0),
                (3,10,100,2,'{TAX}',47625.0),   (4,10,101,2,'{TAX}',55319.0),
                (5,20,200,1,'{TRADE}',7.0)"""))            # Danone: same tag, genuinely a tax line
        return e

    @staticmethod
    def concepts(db):
        with db.connect() as c:
            return dict(c.execute(text("SELECT value_id, (SELECT normalized_name FROM ifrs_concept WHERE "
                                       "concept_id = f.concept_id) FROM fact_value f")).fetchall())

    def test_drift_is_found_for_the_override_company_only(self, m32, db):
        with db.connect() as c:
            d = m32.find_drift(c, self.LOOKUP, overrides=self.OV)
        assert set(d["company"]) == {"Puig Brands"} and d["n_facts"].sum() == 4

    def test_the_preview_shows_the_swap_resolves_and_nothing_stays_stuck(self, m32, db):
        with db.connect() as c:
            d = m32.find_drift(c, self.LOOKUP, overrides=self.OV)
            prev = m32.preview_drift(c, self.LOOKUP, d, overrides=self.OV)
        assert prev["would_move"].sum() == 4 and prev["would_stay_clash"].sum() == 0
        assert prev["of_which_after_a_swap_partner"].sum() == 2          # the tax rows wait for the trade rows to leave

    def test_apply_swaps_the_two_lines_and_leaves_other_companies_and_the_global_mapping_alone(self, m32, db):
        with db.begin() as c:
            out = m32.apply_drift(c, self.LOOKUP, m32.find_drift(c, self.LOOKUP, overrides=self.OV), overrides=self.OV)
        assert out["moved"].sum() == 4 and out["still_blocked"].sum() == 0
        cc = self.concepts(db)
        assert cc[1] == cc[2] == "trade_and_other_current_payables_to_trade_suppliers"     # 229,492 / 212,072 = trade payables
        assert cc[3] == cc[4] == "current_tax_liabilities_current"                          # 47,625 / 55,319 = tax payable
        assert cc[5] == "current_tax_liabilities_current"                                   # Danone untouched
        with db.connect() as c:
            assert c.execute(text(f"SELECT concept_id FROM concept_mapping WHERE xbrl_tag = '{TRADE}'")).scalar() == 1
            assert c.execute(text(f"SELECT concept_id FROM concept_mapping WHERE xbrl_tag = '{TAX}'")).scalar() == 2

    def test_a_second_run_finds_nothing_to_do(self, m32, db):
        with db.begin() as c:
            m32.apply_drift(c, self.LOOKUP, m32.find_drift(c, self.LOOKUP, overrides=self.OV), overrides=self.OV)
        with db.connect() as c:
            assert m32.find_drift(c, self.LOOKUP, overrides=self.OV).empty


class TestLvmhOperatingInvestmentsAsCapex:
    """LVMH prints one net line, "Investissements d'exploitation" (5,531M in 2024). Its note 15.3 gives the gross split, so
    the line can be checked against the IAS 7.16(a) gross payments for acquiring PP&E and intangibles: within 1% in each of
    2022-2024. That is what justifies reading it as LVMH's capex (company-defined), and it is not XBRL-tagged in the note."""

    TAG = "LVM:OperatingInvestmentsClassifiedAsInvestingActivities"
    CANON = "purchase_of_property_plant_and_equipment_and_intangible_asse_etc"

    # EUR millions from note 15.3: (operating investments, cash effect of acquisitions, disposals, deposits and other)
    NOTE_15_3 = {2024: (5_531, 5_519, 21, -33), 2023: (7_478, 7_536, 136, -78), 2022: (4_969, 4_948, 73, -94)}

    def test_the_note_adds_up_and_the_line_is_within_one_percent_of_the_gross_payments(self):
        for year, (line, gross, disposals, deposits) in self.NOTE_15_3.items():
            assert -gross + disposals + deposits == -line, year          # (5,519) + 21 + (-33) = (5,531)
            assert abs(line - gross) / gross < 0.01, year

    def test_the_override_applies_to_lvmh_only(self, m09):
        lookup = m09.load_mapping(str(MAPPING))
        ov = m09.load_overrides(OVERRIDES)
        assert m09.tag_lookup_for("LVMH", lookup, ov)[self.TAG][0] == self.CANON
        assert m09.tag_lookup_for("Kering", lookup, ov)[self.TAG][0] == "operating_investments_classified_as_investing_activities"

    def test_the_entry_states_its_evidence(self, m09):
        import yaml
        entry = next(e for e in yaml.safe_load(OVERRIDES.read_text(encoding="utf-8"))["overrides"] if e["company"] == "LVMH")
        assert "5,519" in entry["evidence"] and "5,531" in entry["evidence"] and "IAS 7.16(a)" in entry["evidence"]
        assert entry["printed_label"] == "Investissements d'exploitation"

    def test_the_capex_logic_reads_it(self, load_script):
        m21 = load_script("21_three_statement_model.py")
        w = pd.DataFrame([{"year": 2024, self.CANON: 5_531e6}])
        r = m21.resolve_capex(w)
        assert r["capex"].iloc[0] == pytest.approx(5_531e6) and r["basis"].iloc[0] == self.CANON
