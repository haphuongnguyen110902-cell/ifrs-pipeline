"""
tests/test_download_bdif.py

scripts/download_bdif.py fetches French issuers' ESEF packages from the AMF's BDIF, because filings.xbrl.org had no
FY2025 for LVMH / Danone / EssilorLuxottica and nothing after FY2023 for Kering (checked 2026-09-23). Every rule here
comes from a real case met while downloading: a text search for "Danone" also returns GENERIX GROUP; BDIF's path hash
is NOT the file's SHA-256; four of five packages ignore the <LEI>-<date> naming; Pernod Ricard's FY2021 and FY2022
reports declare an unused 31 December context that named the wrong year. No network, no database.
"""
import datetime
import io
import zipfile

import pytest


@pytest.fixture(scope="module")
def bd(load_script):
    return load_script("download_bdif.py")


def result(issuer, numero, files):
    return {"numero": numero, "societes": [{"raisonSociale": issuer}],
            "documents": [{"nomFichier": f, "path": f"2026/{numero}/X.{f.split('.')[-1]}"} for f in files]}


class TestSelection:
    def test_only_the_issuers_own_zip_packages_newest_first(self, bd):
        res = [result("DANONE", "D.25-0085", ["2025-0085.pdf", "2025-008500.zip"]),
               result("GENERIX GROUP", "D.21-0732", ["2021-0732.pdf", "2021-073200.zip"]),
               result("DANONE", "D.26-0063", ["2026-0063.pdf", "2026-006300.zip"]),
               result("DANONE", "D.21-0151", ["2021-0151.pdf", "2021-015100.pdf"])]           # PDF only: no ESEF
        got = [n for n, _ in bd.esef_packages(res, "Danone")]
        assert got == ["D.26-0063", "D.25-0085"]

    def test_candidate_year_for_december_and_june_year_ends(self, bd):
        assert bd.candidate_year("D.26-0195") == 2025            # LVMH, filed spring 2026 for FY2025
        assert bd.candidate_year("D.25-0638", fye_month=6) == 2025   # Pernod, filed autumn 2025 for FY June 2025


def xhtml(lei, contexts, facts):
    ctx = "".join(f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/'
                  f'17442">{lei}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{s}</xbrli:startDate>'
                  f'<xbrli:endDate>{e}</xbrli:endDate></xbrli:period></xbrli:context>' for cid, s, e in contexts)
    fx = "".join(f'<ix:nonFraction name="ifrs-full:Revenue" contextRef="{cid}" unitRef="EUR">1</ix:nonFraction>'
                 for cid, n in facts for _ in range(n))
    return (f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            f'xmlns:xbrli="http://www.xbrl.org/2003/instance"><body><ix:header><ix:resources>{ctx}</ix:resources>'
            f'</ix:header>{fx}</body></html>').encode()


class TestPeriodFromTheFacts:
    LEI = "52990097YFPX9J0H5D87"

    def test_an_unused_december_context_does_not_name_the_year(self, bd):
        """Pernod Ricard FY2022 (June year end), as filed: facts on Jul-Jun years, plus a 2022 calendar-year context
        that carries no fact. The latest end of ANY annual context would say 2022-12-31."""
        doc = xhtml(self.LEI, [("c1", "2021-07-01", "2022-06-30"), ("c0", "2020-07-01", "2021-06-30"),
                               ("stray", "2022-01-01", "2022-12-31")], [("c1", 144), ("c0", 134)])
        assert bd.report_period(doc, self.LEI) == datetime.date(2022, 6, 30)

    def test_another_issuers_report_is_refused(self, bd):
        doc = xhtml("IOG4E947OATN0KJYSD45", [("c1", "2025-01-01", "2025-12-31")], [("c1", 5)])
        with pytest.raises(ValueError, match="is not"):
            bd.report_period(doc, self.LEI)

    def test_a_package_without_a_reports_folder_is_not_an_esef_package(self, bd, tmp_path):
        p = tmp_path / "x.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("somewhere/report.xhtml", "<html/>")
        with pytest.raises(ValueError, match="reports/"):
            bd.package_period(p, self.LEI)


class TestIntegrity:
    def test_a_short_read_or_a_corrupt_member_is_rejected(self, bd, tmp_path):
        good = tmp_path / "g.zip"
        with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("a/reports/r.xhtml", "x" * 5000)
        size = good.stat().st_size
        assert bd.intact(good, size, size) == ""
        assert "received" in bd.intact(good, size, size - 10)
        raw = bytearray(good.read_bytes())
        pos = raw.find(b"PK\x03\x04") + 30 + len("a/reports/r.xhtml") + 5    # inside the compressed data
        raw[pos] ^= 0xFF
        bad = tmp_path / "b.zip"
        bad.write_bytes(bytes(raw))
        assert bd.intact(bad, len(raw), len(raw)) != ""
