"""
tests/test_note_facts.py

scripts/33_load_note_facts.py reads figures a company prints in its NOTES but does not tag in XBRL (LVMH, L'Oreal and Essity
print a clean depreciation and amortisation total there; their tagged cash-flow lines are bundled with provisions or absent,
so EBITDA was EBIT). The figure is read from the report's table by a REVIEWED specification, refused unless its checks
pass, and stored with raw_xbrl_tag "note:<id>". Synthetic reports below copy the exact row shapes of the real ones; the
golden tests at the end read the real reports and are skipped where the (git-ignored) packages are not present.
"""
import zipfile
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).parent.parent
NS = ('xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
      'xmlns:xbrli="http://www.xbrl.org/2003/instance"')


@pytest.fixture(scope="module")
def m33(load_script):
    return load_script("33_load_note_facts.py")


def report(tmp_path, tables, facts=(), name="rep.zip"):
    """A minimal ESEF-shaped package: `tables` = list of rows (list of cell strings); `facts` = (tag, start, end, text, scale)."""
    ctx, fx = [], []
    for i, (tag, start, end, txt, scale) in enumerate(facts):
        ctx.append(f'<xbrli:context id="c{i}"><xbrli:entity/><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>'
                   f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')
        fx.append(f'<ix:nonFraction name="{tag}" contextRef="c{i}" scale="{scale}" format="ixt:num-comma-decimal">{txt}</ix:nonFraction>')
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in tables)
    xhtml = (f'<html {NS}><body><ix:header><ix:resources>{"".join(ctx)}</ix:resources></ix:header>'
             f'<table>{body}</table><p>{"".join(fx)}</p></body></html>')
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pkg/reports/report.xhtml", xhtml)
    return path


def spec(**over):
    s = {"id": "t", "company": "X", "report": "rep.zip", "row": "^Depreciation$", "years": "header", "column": "all",
         "scale": 1_000_000, "decimal": ",", "currency": "EUR", "concept": "c", "statement": "cash_flow", "label": "L",
         "perimeter": "p", "checks": [{"sane_pct_of_revenue": [2, 8]}], "evidence": "e"}
    s.update(over)
    return s


def run(m33, path, sp):
    return m33.extract(sp, m33.read_rows(path), m33.report_facts(path))


REV = [("ifrs-full:Revenue", "2025-01-01", "2025-12-31", "44 052,0", 6),
       ("ifrs-full:Revenue", "2024-01-01", "2024-12-31", "43 486,8", 6),
       ("ifrs-full:Revenue", "2023-01-01", "2023-12-31", "41 182,5", 6)]


class TestParseCell:
    @pytest.mark.parametrize("text_,decimal,expected", [
        ("1 652,4", ",", 1652.4), ("1 652,4", ",", 1652.4), ("7 505", ",", 7505.0), ("1,652.4", ".", 1652.4),
        ("(837)", ",", -837.0), ("-1 099", ",", -1099.0), ("–1 099", ",", -1099.0), ("0,5", ",", 0.5)])
    def test_numbers(self, m33, text_, decimal, expected):
        assert m33.parse_cell(text_, decimal) == pytest.approx(expected)

    @pytest.mark.parametrize("text_", ["", "-", "–", "n.a.", "12-13", "En millions d'euros", "2025 (1)"])
    def test_not_numbers(self, m33, text_):
        assert m33.parse_cell(text_, ",") is None


class TestHeaderYears:
    """L'Oreal's row sits right under a header of years; the figure is a note row, not a tagged fact."""
    rows = [["En millions d'euros", "2025", "2024", "2023"],
            ["Dotations aux amortissements", "1 652,4", "1 586,7", "1 429,7"],
            ["dont droits d'utilisation IFRS 16", "482,5", "474,5", "426,0"]]

    def test_each_value_gets_its_header_year_and_is_scaled(self, m33, tmp_path):
        got = run(m33, report(tmp_path, self.rows, REV), spec(row="^Dotations aux amortissements$"))
        assert [(r["year"], r["value"]) for r in got] == [(2025, 1_652_400_000), (2024, 1_586_700_000), (2023, 1_429_700_000)]
        assert all("sane_pct_of_revenue" in r["checks"][0] for r in got)

    def test_two_matching_rows_are_ambiguous_and_refused(self, m33, tmp_path):
        with pytest.raises(m33.FigureError, match="exactly one"):
            run(m33, report(tmp_path, self.rows + self.rows, REV), spec(row="^Dotations aux amortissements$"))

    def test_no_header_of_years_is_refused(self, m33, tmp_path):
        rows = [["blah", "x", "y"], ["Dotations aux amortissements", "1 652,4", "1 586,7"]]
        with pytest.raises(m33.FigureError, match="header"):
            run(m33, report(tmp_path, rows, REV), spec(row="^Dotations aux amortissements$"))

    def test_an_implausible_share_of_revenue_is_refused(self, m33, tmp_path):
        rows = [["MEUR", "2025", "2024", "2023"], ["Depreciation", "16 524,0", "1 586,7", "1 429,7"]]      # 37.5% of revenue
        with pytest.raises(m33.FigureError, match="outside the reviewed range"):
            run(m33, report(tmp_path, rows, REV), spec())

    def test_a_row_that_is_absent_is_refused_not_guessed(self, m33, tmp_path):
        with pytest.raises(m33.FigureError, match="found 0"):
            run(m33, report(tmp_path, self.rows, REV), spec(row="^Something else$"))


class TestOccurrencesAndCrossCheck:
    """LVMH: one segment table per year (newest first, no year in a header), total column last; the IFRS 16 row under the
    figure equals a tagged cash-flow fact, which proves the year mapping."""
    tables = [["Charges d'amortissement et de dépréciation", "(310)", "(2 922)", "(6 702)"], ["Dont : Droits d'utilisation", "(34)", "(1 637)", "(3 228)"],
              ["Charges d'amortissement et de dépréciation", "(274)", "(2 599)", "(6 018)"], ["Dont : Droits d'utilisation", "(31)", "(1 475)", "(3 031)"],
              ["Charges d'amortissement et de dépréciation", "(261)", "(2 431)", "(5 772)"], ["Dont : Droits d'utilisation", "(34)", "(1 422)", "(3 007)"]]
    facts = [("ifrs-full:Revenue", "2024-01-01", "2024-12-31", "84 683", 6), ("ifrs-full:Revenue", "2023-01-01", "2023-12-31", "86 153", 6),
             ("ifrs-full:Revenue", "2022-01-01", "2022-12-31", "79 184", 6),
             ("LVM:Rou", "2024-01-01", "2024-12-31", "3 228", 6), ("LVM:Rou", "2023-01-01", "2023-12-31", "3 031", 6),
             ("LVM:Rou", "2022-01-01", "2022-12-31", "3 007", 6)]

    def sp(self, years=(2024, 2023, 2022), **over):
        return spec(row="^Charges d.amortissement et de dépréciation$", years=list(years), column="last",
                    checks=[{"sane_pct_of_revenue": [3, 12]},
                            {"cross_row_equals_tagged_fact": {"row": "^Dont : Droits d.utilisation$", "tag": "LVM:Rou", "row_offset": 1}}], **over)

    def test_values_years_and_the_cross_check(self, m33, tmp_path):
        got = run(m33, report(tmp_path, self.tables, self.facts), self.sp())
        assert [(r["year"], r["value"]) for r in got] == [(2024, 6_702e6), (2023, 6_018e6), (2022, 5_772e6)]
        assert "tagged Rou" in got[0]["checks"][1]

    def test_a_wrong_year_order_is_caught_by_the_tagged_fact(self, m33, tmp_path):
        with pytest.raises(m33.FigureError, match="cannot be trusted"):
            run(m33, report(tmp_path, self.tables, self.facts), self.sp(years=(2022, 2023, 2024)))

    def test_the_wrong_number_of_tables_is_refused(self, m33, tmp_path):
        with pytest.raises(m33.FigureError, match="expected 2 rows"):
            run(m33, report(tmp_path, self.tables, self.facts), self.sp(years=(2024, 2023)))

    def test_the_row_under_the_figure_must_be_the_expected_one(self, m33, tmp_path):
        tables = [self.tables[0], ["Autres", "(1)", "(2)", "(3)"]] + self.tables[2:]
        with pytest.raises(m33.FigureError, match="not"):
            run(m33, report(tmp_path, tables, self.facts), self.sp())

    def test_a_year_the_report_does_not_tag_is_allowed_only_with_min_verified(self, m33, tmp_path):
        tables = self.tables + [["Charges d'amortissement et de dépréciation", "(228)", "(2 142)", "(5 253)"], ["Dont : Droits d'utilisation", "(32)", "(1 291)", "(2 698)"]]
        four = self.sp(years=(2024, 2023, 2022, 2021))
        with pytest.raises(m33.FigureError, match="cannot be trusted"):
            run(m33, report(tmp_path, tables, self.facts), four)
        four["checks"][1]["cross_row_equals_tagged_fact"]["min_verified"] = 3
        got = run(m33, report(tmp_path, tables, self.facts), four)
        assert got[-1]["year"] == 2021 and "no tagged fact" in got[-1]["checks"][-1]
        four["checks"][1]["cross_row_equals_tagged_fact"]["min_verified"] = 4
        with pytest.raises(m33.FigureError, match="only 3 year"):
            run(m33, report(tmp_path, tables, self.facts), four)


class TestEbitdaBridge:
    """Essity: the company's own EBITDA reconciliation - operating profit + components = EBITDA - confirms the D&A row."""
    hdr = ["MSEK", "2024", "2023", "2022"]
    bridge = [["Rörelseresultat", "18 295", "15 148", "8 491"], ["Avskrivningar på förvärvsrelaterade", "1 110", "1 109", "1 111"],
              ["Avskrivningar", "5 028", "5 000", "4 779"], ["Avskrivningar nyttjanderätt", "1 089", "1 061", "948"],
              ["Nedskrivningar", "56", "65", "41"], ["IAC netto", "152", "413", "1 858"], ["IAC förvärv", "70", "350", "274"],
              ["EBITDA", "25 800", "23 146", "17 502"]]
    cash = [hdr, ["Av- och nedskrivningar av anläggningstillgångar", "7 505", "7 998", "9 012"]]
    facts = [("ifrs-full:Revenue", f"{y}-01-01", f"{y}-12-31", t, 6) for y, t in ((2024, "145 546"), (2023, "147 147"), (2022, "131 320"))]

    def sp(self):
        return spec(row="^Av- och nedskrivningar av anläggningstillgångar$", currency="SEK",
                    checks=[{"sane_pct_of_revenue": [3, 9]}, {"ebitda_bridge": {"start": "^Rörelseresultat$", "end": "^EBITDA$", "tolerance": 1}}])

    def test_bridge_confirms_every_year(self, m33, tmp_path):
        got = run(m33, report(tmp_path, self.cash + [self.hdr] + self.bridge, self.facts), self.sp())
        assert [(r["year"], r["value"]) for r in got] == [(2024, 7_505e6), (2023, 7_998e6), (2022, 9_012e6)]
        assert "EBITDA 25,800 - Rörelseresultat 18,295 = 7,505" in got[0]["checks"][1]

    def test_a_figure_that_does_not_bridge_is_refused(self, m33, tmp_path):
        bad = [list(r) for r in self.bridge]
        bad[-1][1] = "25 700"                                     # EBITDA no longer = operating profit + components
        with pytest.raises(m33.FigureError, match="bridge"):
            run(m33, report(tmp_path, self.cash + [self.hdr] + bad, self.facts), self.sp())

    def test_a_row_that_differs_from_ebitda_minus_operating_profit_is_refused(self, m33, tmp_path):
        cash = [self.hdr, ["Av- och nedskrivningar av anläggningstillgångar", "7 000", "7 998", "9 012"]]
        with pytest.raises(m33.FigureError):
            run(m33, report(tmp_path, cash + [self.hdr] + self.bridge, self.facts), self.sp())


class TestSpecFile:
    specs = None

    def test_every_entry_is_complete_reviewed_and_reads_a_concept_the_engine_uses(self, m33):
        mapping = yaml.safe_load((ROOT / "data" / "mappings" / "ifrs_concepts_v0.yaml").read_text(encoding="utf-8"))
        known = {(n, st) for st, cs in mapping.items() for n in cs}
        specs = m33.load_specs()
        assert {s["company"] for s in specs} == {"LVMH", "L'Oreal", "Essity"}
        for s in specs:
            assert (s["concept"], s["statement"]) in known, s["id"]
            assert s["checks"] and len(s["evidence"]) > 40 and s["perimeter"]
            assert s["report"].endswith(".zip")

    def test_the_concepts_are_the_ones_ratio_engine_reads_as_d_and_a(self, m33, load_script):
        r11 = load_script("11_ratio_engine.py")
        import pandas as pd
        for s in m33.load_specs():
            w = pd.DataFrame([{"company": "X", "company_id": 1, "year": 2024, "revenue": 1000.0,
                               "profit_loss_from_operating_activities": 100.0, s["concept"]: 50.0}])
            assert r11.compute_ratios(w)["_da_total"].iloc[0] == 50.0, s["id"]

    def test_a_figure_without_evidence_or_a_duplicate_id_is_refused(self, m33, tmp_path):
        base = {"id": "a", "company": "X", "report": "r.zip", "row": "x", "years": "header", "column": "all", "scale": 1,
                "decimal": ",", "currency": "EUR", "concept": "c", "statement": "cash_flow", "label": "L", "perimeter": "p",
                "checks": [{"sane_pct_of_revenue": [1, 2]}]}
        f = tmp_path / "s.yaml"
        f.write_text(yaml.safe_dump({"figures": [base]}), encoding="utf-8")
        with pytest.raises(ValueError, match="evidence"):
            m33.load_specs(f)
        f.write_text(yaml.safe_dump({"figures": [dict(base, evidence="e"), dict(base, evidence="e")]}), encoding="utf-8")
        with pytest.raises(ValueError, match="twice"):
            m33.load_specs(f)


class TestStore:
    @pytest.fixture
    def db(self):
        e = create_engine("sqlite://")
        with e.begin() as c:
            for ddl in ("CREATE TABLE company (company_id INTEGER PRIMARY KEY, name TEXT)",
                        "CREATE TABLE filing (filing_id INTEGER PRIMARY KEY, company_id INTEGER, source_file TEXT)",
                        "CREATE TABLE period (period_id INTEGER PRIMARY KEY, filing_id INTEGER, start_date TEXT, end_date TEXT, period_type TEXT)",
                        "CREATE TABLE ifrs_concept (concept_id INTEGER PRIMARY KEY, normalized_name TEXT UNIQUE, statement TEXT, display_label TEXT)",
                        "CREATE TABLE fact_value (value_id INTEGER PRIMARY KEY, filing_id INTEGER, period_id INTEGER, concept_id INTEGER, "
                        "raw_xbrl_tag TEXT, value REAL, currency TEXT, decimals INTEGER, context_ref TEXT, dimensions TEXT, "
                        "UNIQUE(filing_id, period_id, concept_id))"):
                c.execute(text(ddl))
            c.execute(text("INSERT INTO company VALUES (1, 'X')"))
            c.execute(text(r"INSERT INTO filing VALUES (10, 1, 'data\raw\historical\rep.zip')"))
        return e

    sp = spec(concept="da")
    got = [{"year": 2024, "value": 1_586.7e6, "row_index": 2155}, {"year": 2023, "value": 1_429.7e6, "row_index": 2155}]

    def test_stores_with_provenance_and_the_loaders_period_convention(self, m33, db):
        with db.begin() as c:
            assert m33.store(c, self.sp, self.got) == [(2024, "stored"), (2023, "stored")]
        with db.connect() as c:
            row = c.execute(text("SELECT f.value, f.raw_xbrl_tag, f.context_ref, f.currency, p.start_date, p.end_date "
                                 "FROM fact_value f JOIN period p ON p.period_id = f.period_id WHERE p.start_date = '2024-01-01'")).fetchone()
        assert row == (1_586_700_000.0, "note:t", "rep.zip#row2155", "EUR", "2024-01-01", "2025-01-01")

    def test_running_twice_stores_nothing_new(self, m33, db):
        with db.begin() as c:
            m33.store(c, self.sp, self.got)
        with db.begin() as c:
            assert m33.store(c, self.sp, self.got) == [(2024, "exists"), (2023, "exists")]
        with db.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM fact_value")).scalar() == 2

    def test_an_xbrl_fact_is_never_overwritten(self, m33, db):
        with db.begin() as c:
            c.execute(text("INSERT INTO ifrs_concept VALUES (1, 'da', 'cash_flow', 'L')"))
            c.execute(text("INSERT INTO period VALUES (5, 10, '2024-01-01', '2025-01-01', 'duration')"))
            c.execute(text("INSERT INTO fact_value VALUES (1, 10, 5, 1, 'ifrs-full:Tagged', 111.0, 'EUR', NULL, NULL, NULL)"))
        with db.begin() as c:
            assert m33.store(c, self.sp, self.got)[0] == (2024, "exists")
        with db.connect() as c:
            assert c.execute(text("SELECT value, raw_xbrl_tag FROM fact_value WHERE value_id = 1")).fetchone() == (111.0, "ifrs-full:Tagged")

    def test_no_single_matching_filing_stores_nothing(self, m33, db):
        with db.begin() as c:
            out = m33.store(c, dict(self.sp, report="other.zip"), self.got)
        assert all("no single filing" in s for _, s in out)
        with db.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM fact_value")).scalar() == 0


def _real(m33, name):
    return m33.locate_report(name)


class TestRealReports:
    """Golden values read from the companies' own reports (skipped where the packages are not on this machine)."""

    @pytest.mark.parametrize("fid,expected", [
        ("lvmh_da_note_fy2024", {2024: 6_702e6, 2023: 6_018e6, 2022: 5_772e6}),
        ("lvmh_da_note_fy2023", {2023: 6_018e6, 2022: 5_772e6, 2021: 5_253e6}),
        ("loreal_da_note_fy2025", {2025: 1_652.4e6, 2024: 1_586.7e6, 2023: 1_429.7e6}),
        ("loreal_da_note_fy2024", {2024: 1_586.7e6, 2023: 1_429.7e6, 2022: 1_474.2e6}),
        ("essity_da_note_fy2024", {2024: 7_505e6, 2023: 7_998e6, 2022: 9_012e6}),
    ])
    def test_the_reviewed_figure_reads_and_passes_its_checks(self, m33, fid, expected):
        s = next(x for x in m33.load_specs() if x["id"] == fid)
        path = _real(m33, s["report"])
        if path is None:
            pytest.skip(f"{s['report']} is not on this machine")
        got = run(m33, path, s)
        assert {r["year"]: r["value"] for r in got} == pytest.approx(expected)
