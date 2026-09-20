"""
tests/test_reconcile_reports.py

scripts/reconcile_reports.py checks the figures we store against the company's own published report,
line by line, using the company's own presentation linkbase to say which lines a statement has.

Real cases it must get right (each found while checking the first companies):
  * Recordati FY2022: the income-statement facts carry a ONE-DAY period (start == end), so a naive check
    finds "no lines" and reports nothing wrong. It must say BAD_PERIOD, never stay silent.
  * a statement whose role names are not recognised must read "not located", never "0 problems".
  * numbers as printed differ by language/format: 1,234.5 / 1.234,5 / '1 475' (Danone), scale 6, sign '-'.
No database, no network: packages are built in memory.
"""
import datetime
import io
import zipfile

import pytest

XHTML = """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"><body>
<ix:header><ix:resources>
 <xbrli:context id="i24"><xbrli:entity/><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:context id="i23"><xbrli:entity/><xbrli:period><xbrli:instant>2023-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:context id="d24"><xbrli:entity/><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="bad24"><xbrli:entity/><xbrli:period><xbrli:startDate>2024-12-31</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="dim24"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="d:x">m:y</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
   <xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
</ix:resources></ix:header>
<p>Assets <ix:nonFraction name="ifrs-full:Assets" contextRef="i24" scale="3" format="ixt:num-dot-decimal">5,134,455</ix:nonFraction>
 prior <ix:nonFraction name="ifrs-full:Assets" contextRef="i23" scale="3">4,208,131</ix:nonFraction></p>
<p>Loans <ix:nonFraction name="Rec:LoansDueAfterOneYear" contextRef="i24" scale="3">2,130,296</ix:nonFraction></p>
<p>Cash <ix:nonFraction name="ifrs-full:CashAndCashEquivalents" contextRef="i24" scale="6" format="ixt:num-comma-decimal">1.475</ix:nonFraction></p>
<p>Revenue <ix:nonFraction name="ifrs-full:Revenue" contextRef="d24" scale="3">2,618,383</ix:nonFraction>
 Other <ix:nonFraction name="ifrs-full:OtherIncome" contextRef="bad24" scale="3">9</ix:nonFraction>
 Loss <ix:nonFraction name="ifrs-full:LossX" contextRef="d24" scale="3" sign="-">1,000</ix:nonFraction>
 Seg <ix:nonFraction name="ifrs-full:Assets" contextRef="dim24" scale="3">999</ix:nonFraction></p>
</body></html>"""

XSD = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:link="http://www.xbrl.org/2003/linkbase"><xs:annotation><xs:appinfo>
 <link:roleType roleURI="http://x/role/BS"><link:definition>[210000] Statement of financial position</link:definition></link:roleType>
 <link:roleType roleURI="http://x/role/DISC"><link:definition>[800100] Disclosure of borrowings, financial position notes</link:definition></link:roleType>
</xs:appinfo></xs:annotation></xs:schema>"""


def pre(role, concepts):
    locs = "".join(f'<link:loc xlink:type="locator" xlink:href="a.xsd#{c.replace(":", "_", 1)}" xlink:label="l{i}"/>' for i, c in enumerate(concepts))
    arcs = "".join(f'<link:presentationArc xlink:arcrole="http://www.xbrl.org/2003/arcrole/parent-child" xlink:from="l0" xlink:to="l{i}" order="{i}"/>'
                   for i in range(1, len(concepts)))
    return (f'<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">'
            f'<link:presentationLink xlink:role="{role}">{locs}{arcs}</link:presentationLink></link:linkbase>').encode()


@pytest.fixture(scope="module")
def rr(load_script):
    return load_script("reconcile_reports.py")


def make_zip(path, statement_role="http://x/role/BS", with_pre=True):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pkg/reports/r.xhtml", XHTML)
        z.writestr("pkg/x/ext.xsd", XSD)
        if with_pre:
            z.writestr("pkg/x/ext_pre.xml", pre(statement_role, ["ifrs-full:StatementOfFinancialPositionAbstract", "ifrs-full:Assets",
                                                                 "Rec:LoansDueAfterOneYear", "ifrs-full:CashAndCashEquivalents",
                                                                 "ifrs-full:Revenue"]))
    return str(path)


class TestNumbersAsPrinted:
    @pytest.mark.parametrize("text,fmt,scale,sign,expected", [
        ("5,134,455", "ixt:num-dot-decimal", "3", "", 5_134_455_000.0),
        ("1.234,5", "ixt:num-comma-decimal", "0", "", 1234.5),
        ("445.193", "ixt:numcommadecimal", "3", "", 445_193_000.0),          # Italian thousands separator
        ("1 475", "", "6", "", 1_475_000_000.0),                          # Danone: NBSP thousands
        ("1 000", "", "3", "-", -1_000_000.0),
        ("-", "ixt:zerodash", "0", "", 0.0),
        ("", "", "0", "", None),
        ("n/a", "", "0", "", None),
    ])
    def test_cases(self, rr, text, fmt, scale, sign, expected):
        assert rr.parse_number(text, fmt, scale, sign) == expected


class TestReport:
    def test_dimensional_facts_are_left_out_and_numbers_are_scaled_and_signed(self, rr):
        facts = rr.read_inline_facts(XHTML.encode())
        tags = [(f["tag"], f["instant"] or f["end"], f["value"]) for f in facts]
        assert ("ifrs-full:Assets", "2024-12-31", 5_134_455_000.0) in tags
        assert ("ifrs-full:Assets", "2024-12-31", 999_000.0) not in tags, "a segment figure is not the headline line"
        assert ("ifrs-full:LossX", "2024-12-31", -1_000_000.0) in tags
        assert ("ifrs-full:CashAndCashEquivalents", "2024-12-31", 1_475_000_000.0) in tags

    def test_reporting_date_is_the_latest_well_populated_instant(self, rr):
        assert rr.reporting_date(rr.read_inline_facts(XHTML.encode())) == "2024-12-31"


class TestStructure:
    def test_qname_from_fragment(self, rr):
        assert rr.qname_from_fragment("a.xsd#ifrs-full_Assets") == "ifrs-full:Assets"
        assert rr.qname_from_fragment("Rec_LoansDueAfterOneYear") == "Rec:LoansDueAfterOneYear"

    def test_statement_lines_come_from_the_companys_own_presentation_in_order(self, rr):
        defs = rr.role_definitions([XSD.encode()])
        trees = rr.read_presentation([pre("http://x/role/BS", ["ifrs-full:StatementAbstract", "ifrs-full:Assets", "Rec:Loans"])])
        assert rr.statement_lines(trees, defs, "balance") == ["ifrs-full:StatementAbstract", "ifrs-full:Assets", "Rec:Loans"]

    def test_a_disclosure_note_role_is_not_a_primary_statement(self, rr):
        defs = rr.role_definitions([XSD.encode()])
        trees = rr.read_presentation([pre("http://x/role/DISC", ["ifrs-full:A", "ifrs-full:B"])])
        assert rr.statement_lines(trees, defs, "balance") == []

    def test_a_standard_ifrs_role_is_recognised_from_its_uri_alone(self, rr):
        trees = rr.read_presentation([pre("http://xbrl.ifrs.org/role/ifrs/ifrs-full_210000_StatementOfFinancialPosition", ["x:A", "x:B"])])
        assert rr.statement_lines(trees, {}, "balance") == ["x:A", "x:B"]

    def test_italian_role_names_are_recognised(self, rr):
        trees = rr.read_presentation([pre("http://r/ContoEconomico", ["x:A", "x:B"]), pre("http://r/StatoPatrimonialePassivo", ["x:C", "x:D"])])
        assert rr.statement_lines(trees, {}, "income") == ["x:A", "x:B"]
        assert rr.statement_lines(trees, {}, "balance") == ["x:C", "x:D"]


class TestRolesInTheFilersLanguage:
    """Role names seen on the first real runs; the URIs carry them with the accents stripped."""

    @pytest.mark.parametrize("definition,kind", [
        ("[0000001] État du résultat global, résultat net, charges par fonction (Statement)", "income"),      # L'Oreal, Kering
        ("[0000003] État de la situation financière, courant/non courant (Statement)", "balance"),
        ("[0000005] État des flux de trésorerie, méthode indirecte (Statement)", "cashflow"),
        ("[0000001] Estado de situación financiera, corriente / no corriente (Statement)", "balance"),         # Puig
        ("[0000002] Estado del resultado global, de resultados, por naturaleza del gasto", "income"),
        ("[0000004] Estado de flujos de efectivo, método indirecto (Statement)", "cashflow"),
        ("03 - Rapport över finansiell ställning", "balance"),                                                 # Essity
        ("06 - Rapport över kassaflöden", "cashflow"),
        ("01 - Resultat", "income"),
        ("03 - Actifs", "balance"),                                                                           # Schneider
        ("04 - Capitaux propres et passifs", "balance"),
    ])
    def test_recognised(self, rr, definition, kind):
        trees = {"http://r/x": ["x:A", "x:B"]}
        assert rr.statement_lines(trees, {"http://r/x": definition}, kind) == ["x:A", "x:B"]

    @pytest.mark.parametrize("definition", [
        "[0000006] Balises qui doivent être appliquées si les informations correspondantes figurent dans les états financiers",
        "[0000012] Lista de notas", "07 - Notes and Mandatory Items", "[0000007] Liste des notes"])
    def test_notes_and_mandatory_tag_lists_are_not_statements(self, rr, definition):
        for kind in ("balance", "income", "cashflow"):
            assert rr.statement_lines({"http://r/x": ["x:A"]}, {"http://r/x": definition}, kind) == []


class TestReconcile:
    D = "2024-12-31"

    def facts(self, rr):
        return rr.read_inline_facts(XHTML.encode())

    def test_ok_different_and_missing(self, rr):
        lines = ["ifrs-full:Assets", "Rec:LoansDueAfterOneYear", "ifrs-full:CashAndCashEquivalents"]
        stored = {("ifrs-full:Assets", self.D): 5_134_455_000.0, ("ifrs-full:CashAndCashEquivalents", self.D): 999.0}
        got = {r["tag"]: r["status"] for r in rr.reconcile(lines, self.facts(rr), self.D, stored)}
        assert got == {"ifrs-full:Assets": "OK", "Rec:LoansDueAfterOneYear": "MISSING", "ifrs-full:CashAndCashEquivalents": "DIFFERENT"}

    def test_rounding_to_the_unit_is_tolerated_a_real_difference_is_not(self, rr):
        lines = ["ifrs-full:Assets"]
        assert rr.reconcile(lines, self.facts(rr), self.D, {("ifrs-full:Assets", self.D): 5_134_455_000.4})[0]["status"] == "OK"
        assert rr.reconcile(lines, self.facts(rr), self.D, {("ifrs-full:Assets", self.D): 5_134_456_000.0})[0]["status"] == "DIFFERENT"

    def test_a_filers_one_day_period_is_reported_not_dropped(self, rr):
        """Recordati tags its income statement and cash flow with start == end, every year."""
        row = rr.reconcile(["ifrs-full:OtherIncome"], self.facts(rr), self.D, {})[0]
        assert row["status"] == "BAD_PERIOD" and row["printed"] == 9_000.0

    def test_a_malformed_period_still_matches_when_we_hold_the_printed_figure(self, rr):
        row = rr.reconcile(["ifrs-full:OtherIncome"], self.facts(rr), self.D, {("ifrs-full:OtherIncome", self.D): 9_000.0})[0]
        assert row["status"] == "OK_BAD_PERIOD"
        row = rr.reconcile(["ifrs-full:OtherIncome"], self.facts(rr), self.D, {("ifrs-full:OtherIncome", self.D): 1.0})[0]
        assert row["status"] == "BAD_PERIOD" and row["stored"] == 1.0

    def test_summary_counts_a_held_figure_as_matching_even_with_a_malformed_period(self, rr):
        s = rr.summarize([{"status": "OK"}, {"status": "OK_BAD_PERIOD"}, {"status": "BAD_PERIOD"}, {"status": "MISSING"}])
        assert s["share_ok"] == 50.0 and s["OK_BAD_PERIOD"] == 1

    def test_abstract_and_unprinted_lines_are_not_counted_as_missing(self, rr):
        assert rr.reconcile(["ifrs-full:StatementAbstract", "x:NeverPrinted"], self.facts(rr), self.D, {}) == []

    def test_only_the_reporting_date_is_compared_not_the_comparative(self, rr):
        rows = rr.reconcile(["ifrs-full:Assets"], self.facts(rr), self.D, {("ifrs-full:Assets", "2023-12-31"): 4_208_131_000.0})
        assert rows[0]["status"] == "MISSING"

    def test_summary(self, rr):
        s = rr.summarize([{"status": "OK"}, {"status": "OK"}, {"status": "MISSING"}, {"status": "BAD_PERIOD"}])
        assert (s["lines"], s["OK"], s["MISSING"], s["BAD_PERIOD"], s["share_ok"]) == (4, 2, 1, 1, 50.0)


class TestPackage:
    def test_a_package_is_read_end_to_end_without_a_database(self, rr, tmp_path):
        rows = rr.run_package(make_zip(tmp_path / "p.zip"), ["balance"])
        assert {r["tag"]: r["status"] for r in rows} == {"ifrs-full:Assets": "MISSING", "Rec:LoansDueAfterOneYear": "MISSING",
                                                        "ifrs-full:CashAndCashEquivalents": "MISSING", "ifrs-full:Revenue": "MISSING"}
        assert all(r["date"] == "2024-12-31" and r["statement"] == "balance" for r in rows)

    def test_an_unrecognised_statement_says_so_instead_of_reporting_zero_problems(self, rr, tmp_path):
        rows = rr.run_package(make_zip(tmp_path / "p.zip"), ["income"])
        assert [r["status"] for r in rows] == ["NO_STATEMENT"]

    def test_a_package_that_is_not_in_the_database_is_not_loaded_not_a_wall_of_missing_lines(self, rr, tmp_path):
        """35 downloaded-but-never-loaded packages once produced ~3,200 'MISSING' lines that meant nothing."""
        rows = rr.run_package(make_zip(tmp_path / "p.zip"), ["balance"], FakeEngine([]))
        assert [r["status"] for r in rows] == ["NOT_LOADED"]

    def test_no_presentation_linkbase_is_also_not_located(self, rr, tmp_path):
        rows = rr.run_package(make_zip(tmp_path / "q.zip", with_pre=False), ["balance"])
        assert [r["status"] for r in rows] == ["NO_STATEMENT"]


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params=None):
        self.params = params
        return iter(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    def __init__(self, rows):
        self.conn = FakeConn(rows)

    def connect(self):
        return self.conn


class TestStored:
    def test_arelles_one_day_shift_is_undone_and_matching_uses_the_filename(self, rr):
        eng = FakeEngine([("ifrs-full:Assets", None, datetime.date(2025, 1, 1), 5_134_455_000),
                          ("ifrs-full:Revenue", datetime.date(2024, 1, 1), datetime.date(2025, 1, 1), 2_618_383_000),
                          ("ifrs-full:Nothing", None, datetime.date(2025, 1, 1), None)])
        out = rr.fetch_stored(eng, "recordati_2024-12-31.zip")
        assert out == {("ifrs-full:Assets", "2024-12-31"): 5_134_455_000.0, ("ifrs-full:Revenue", "2024-12-31"): 2_618_383_000.0}
        assert eng.conn.params == {"pat": "%recordati_2024-12-31.zip"}
