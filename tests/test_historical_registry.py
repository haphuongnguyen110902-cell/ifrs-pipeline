"""
tests/test_historical_registry.py

The historical downloader (download_historical.py) and loader (load_historical.py) each
keep a hand-written company registry, and both duplicate data/companies.yaml. When the
5 universe companies got depth, all three had to agree or the loader would skip a
downloaded file ("did not match a known company") or create a second company row.

Also covers the downloader's safety behaviour: it now streams to a .part file, checks the
archive's sha256, refuses to fill the disk, and skips periods already loaded. The drive
this project lives on was found 100% full (1.2 GB free) with ~700 MB of packages to fetch,
and the old downloader wrote a file in one call - so an interrupted download left a corrupt
zip that every later run skipped as "already exists".
No database, no network: requests and disk_usage are faked.
"""
import datetime
import hashlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def dl(load_script):
    return load_script("download_historical.py")


@pytest.fixture(scope="module")
def ld(load_script):
    return load_script("load_historical.py")


@pytest.fixture(scope="module")
def yaml_companies():
    doc = yaml.safe_load((ROOT / "data" / "companies.yaml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in doc["companies"].values()}


class TestRegistriesAgree:
    def test_downloader_and_loader_know_the_same_companies(self, dl, ld):
        """Pernod Ricard used to be downloadable but not loadable (June year end). fiscal_year_label labels every
        fiscal year by the year it ENDS, so its history now loads like everyone's (it showed 2 years while 5 more
        reports were published)."""
        assert set(dl.COMPANIES) == set(ld.COMPANY_MAP)

    def test_a_key_with_an_underscore_still_parses(self, ld, tmp_path):
        for n in ("pernod_ricard_2024-06-30.zip", "loreal_2025-12-31.zip"):
            (tmp_path / n).write_bytes(b"")
        matched, unmatched = ld.discover_files(tmp_path)
        assert not unmatched
        assert {(m["company"], m["fiscal_year_end"]) for m in matched} == {("Pernod Ricard", "2024-06-30"),
                                                                          ("L'Oreal", "2025-12-31")}

    def test_the_names_match_so_rows_land_on_the_same_company(self, dl, ld):
        for key, (_, name) in dl.COMPANIES.items():
            if key in ld.COMPANY_MAP:
                assert ld.COMPANY_MAP[key][0] == name, key

    def test_every_loader_entry_matches_companies_yaml(self, ld, yaml_companies):
        """get_or_create_company matches on exact name: a spelling drift creates a second company."""
        for key, (name, currency, sector, country) in ld.COMPANY_MAP.items():
            assert name in yaml_companies, f"{key}: {name!r} is not in companies.yaml"
            y = yaml_companies[name]
            assert (y["expected_currency"], y["sector"], y["country"]) == (currency, sector, country), key

    def test_keys_survive_the_loaders_filename_pattern(self, ld):
        """The loader's regex is letters only - a slug like asm_international would never match."""
        for key in ld.COMPANY_MAP:
            assert ld.FILENAME_RE.match(f"{key}_2024-12-31.zip"), key

    def test_leis_are_well_formed_and_unique(self, dl):
        leis = [lei for lei, _ in dl.COMPANIES.values()]
        assert len(set(leis)) == len(leis)
        assert all(len(l) == 20 and l.isalnum() and l == l.upper() for l in leis)


class TestPureHelpers:
    D = datetime.date

    def test_room_check(self, dl):
        assert dl.has_room(free_bytes=1000, needed_bytes=400, reserve_bytes=500)
        assert not dl.has_room(free_bytes=1000, needed_bytes=600, reserve_bytes=500)

    def test_a_period_is_loaded_when_a_filings_own_end_is_within_days(self, dl):
        assert dl.already_loaded(self.D(2025, 12, 31), [self.D(2025, 12, 31)])
        assert dl.already_loaded(self.D(2025, 12, 31), [self.D(2026, 1, 1)])     # Arelle's +1 day
        assert not dl.already_loaded(self.D(2024, 12, 31), [self.D(2025, 12, 31)])

    def test_still_needed_is_newest_first_minus_loaded_and_limited(self, dl):
        periods = [self.D(y, 12, 31) for y in (2021, 2022, 2023, 2024, 2025)]
        assert dl.periods_still_needed(periods, [self.D(2025, 12, 31)]) == periods[3::-1]
        assert dl.periods_still_needed(periods, [self.D(2025, 12, 31)], limit=2) == [self.D(2024, 12, 31), self.D(2023, 12, 31)]
        assert dl.periods_still_needed(periods, []) == sorted(periods, reverse=True)

    def test_a_comparative_year_inside_a_loaded_filing_does_not_hide_that_years_own_filing(self, dl):
        """FY2025's filing carries FY2024 figures, but only FY2025 counts as loaded."""
        assert dl.periods_still_needed([self.D(2024, 12, 31), self.D(2025, 12, 31)], [self.D(2025, 12, 31)]) == [self.D(2024, 12, 31)]


class FakeResponse:
    def __init__(self, body: bytes, length=None):
        self.body, self.headers = body, {"Content-Length": str(len(body) if length is None else length)}

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestVerifiedDownload:
    BODY = b"PK-fake-package" * 1000

    def filing(self, sha=None):
        return {"attributes": {"package_url": "/x/y.zip", "sha256": sha if sha is not None else hashlib.sha256(self.BODY).hexdigest()}}

    @pytest.fixture
    def net(self, dl, monkeypatch):
        state = {"resp": FakeResponse(self.BODY), "free": 10 * 2**30}
        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: state["resp"])
        monkeypatch.setattr(dl.shutil, "disk_usage", lambda p: type("U", (), {"free": state["free"]})())
        return state

    def test_a_good_package_is_saved_and_no_part_file_remains(self, dl, net, tmp_path):
        out = tmp_path / "heineken_2024-12-31.zip"
        assert dl.download_filing(self.filing(), out)
        assert out.read_bytes() == self.BODY and not list(tmp_path.glob("*.part"))

    def test_a_checksum_mismatch_is_rejected_and_leaves_nothing(self, dl, net, tmp_path):
        out = tmp_path / "heineken_2024-12-31.zip"
        assert not dl.download_filing(self.filing(sha="0" * 64), out)
        assert not out.exists() and not list(tmp_path.glob("*.part"))

    def test_not_enough_disk_refuses_before_writing_anything(self, dl, net, tmp_path):
        net["free"] = 100 * 2**20                     # 100 MB free, reserve 500 MB
        out = tmp_path / "a.zip"
        assert not dl.download_filing(self.filing(), out)
        assert not out.exists() and not list(tmp_path.glob("*.part"))

    def test_an_interrupted_download_is_not_left_as_a_file_later_runs_would_skip(self, dl, monkeypatch, tmp_path):
        class Boom(FakeResponse):
            def iter_content(self, n):
                yield b"partial"
                raise dl.requests.ConnectionError("connection reset")
        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: Boom(self.BODY))
        monkeypatch.setattr(dl.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 * 2**30})())
        out = tmp_path / "a.zip"
        assert not dl.download_filing(self.filing(), out)
        assert not out.exists() and not list(tmp_path.glob("*.part"))

    def test_an_existing_file_is_kept_without_a_request(self, dl, monkeypatch, tmp_path):
        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: pytest.fail("must not fetch"))
        out = tmp_path / "a.zip"
        out.write_bytes(b"already here")
        assert dl.download_filing(self.filing(), out) and out.read_bytes() == b"already here"

    def test_a_filing_without_a_package_url_is_skipped(self, dl, net, tmp_path):
        assert not dl.download_filing({"attributes": {}}, tmp_path / "a.zip")
