"""
tests/test_load_from_archive.py

scripts/load_from_archive.py streams a company's annual reports from filings.xbrl.org through a temporary folder into
the database (the drive is nearly full; PLAN.md WP7 planned "download -> parse -> discard the zip, keep URL + SHA256").
The loader records such a package under its canonical data/raw/historical/<name> with its URL, so reconciliation and
--skip-loaded still find it. No database, no network.
"""
import datetime
from pathlib import Path

import pytest

D = datetime.date


@pytest.fixture(scope="module")
def lfa(load_script):
    return load_script("load_from_archive.py")


@pytest.fixture(scope="module")
def ld(load_script):
    return load_script("load_historical.py")


def filing(period, published, country="FR", fname=None):
    return {"attributes": {"period_end": period, "date_added": published, "country": country,
                           "package_url": f"/x/{fname or period}.zip", "sha256": "0" * 64,
                           "error_count": 0, "warning_count": 0, "inconsistency_count": 0}}


class TestPeriodsToLoad:
    def test_newest_first_from_min_year_and_not_loaded(self, lfa):
        fs = [filing(f"{y}-12-31", f"{y + 1}-03-20") for y in (2019, 2021, 2022, 2023, 2024, 2025)]
        todo = lfa.periods_to_load(fs, loaded_ends=[D(2025, 12, 31)], min_year=2021)
        assert [p for p, _ in todo] == [D(2024, 12, 31), D(2023, 12, 31), D(2022, 12, 31), D(2021, 12, 31)]

    def test_limit(self, lfa):
        fs = [filing(f"{y}-12-31", f"{y + 1}-03-20") for y in (2022, 2023, 2024)]
        assert [p for p, _ in lfa.periods_to_load(fs, [], 2021, limit=1)] == [D(2024, 12, 31)]


class TestReconcileSummary:
    def test_keeps_the_statement_lines(self, lfa):
        out = "header\nx.zip   balance   lines= 34 OK= 34 DIFFERENT= 0\nfooter\nx.zip income lines= 5 OK= 5 DIFFERENT= 0"
        assert len(lfa.reconcile_summary(out)) == 2


class TestRecordedSource:
    def test_a_temporary_package_is_recorded_under_its_canonical_name(self, ld):
        tmp = Path("C:/Temp/ifrs_pkg_abc/carrefour_2024-12-31.zip")
        assert Path(ld.recorded_source(tmp, "data/raw/historical")).as_posix() == \
            "data/raw/historical/carrefour_2024-12-31.zip"

    def test_without_record_as_the_real_path(self, ld):
        p = Path("data/raw/historical/carrefour_2024-12-31.zip")
        assert ld.recorded_source(p, None) == str(p)
