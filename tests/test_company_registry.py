"""
tests/test_company_registry.py

data/companies.yaml is the one registry: download_historical.COMPANIES and load_historical.COMPANY_MAP are built from
it (scripts/company_registry.py). They used to be hand-written copies - three files to edit, and to keep in step, for
every company added; Phase 4 adds 35. Switching was checked to leave both maps exactly as they were (16 entries each).
No database.
"""
import pytest


@pytest.fixture(scope="module")
def reg(load_script):
    return load_script("company_registry.py")


def write(tmp_path, body):
    p = tmp_path / "companies.yaml"
    p.write_text("companies:\n" + body, encoding="utf-8")
    return p


ENTRY = '''  {stem}:
    name: "{name}"
    expected_currency: EUR
    sector: X
    country: France
{extra}'''


class TestHistoricalCompanies:
    def test_only_entries_with_a_key_take_part(self, reg, tmp_path):
        p = write(tmp_path, ENTRY.format(stem="a", name="A", extra="    key: alpha\n    lei: 969500Y4IJGHJE2MTJ13\n")
                  + ENTRY.format(stem="b", name="B", extra=""))
        assert list(reg.historical_companies(p)) == ["alpha"]

    @pytest.mark.parametrize("extra, message", [
        ("    key: Alpha2\n    lei: 969500Y4IJGHJE2MTJ13\n", "lower-case"),
        ("    key: alpha\n    lei: NOTALEI\n", "20-character LEI"),
    ])
    def test_a_malformed_key_or_lei_is_refused(self, reg, tmp_path, extra, message):
        with pytest.raises(ValueError, match=message):
            reg.historical_companies(write(tmp_path, ENTRY.format(stem="a", name="A", extra=extra)))

    def test_a_key_or_lei_used_twice_is_refused(self, reg, tmp_path):
        twice_key = (ENTRY.format(stem="a", name="A", extra="    key: alpha\n    lei: 969500Y4IJGHJE2MTJ13\n")
                     + ENTRY.format(stem="b", name="B", extra="    key: alpha\n    lei: 529900FNDVTQJOVVPZ19\n"))
        with pytest.raises(ValueError, match="used twice"):
            reg.historical_companies(write(tmp_path, twice_key))
        twice_lei = (ENTRY.format(stem="a", name="A", extra="    key: alpha\n    lei: 969500Y4IJGHJE2MTJ13\n")
                     + ENTRY.format(stem="b", name="B", extra="    key: beta\n    lei: 969500Y4IJGHJE2MTJ13\n"))
        with pytest.raises(ValueError, match="used by both"):
            reg.historical_companies(write(tmp_path, twice_lei))


class TestBothScriptsReadIt:
    def test_the_downloader_and_loader_maps_come_from_the_registry(self, reg, load_script):
        dh, ld = load_script("download_historical.py"), load_script("load_historical.py")
        entries = reg.historical_companies()
        assert dh.COMPANIES == {k: (e["lei"], e["name"]) for k, e in entries.items()}
        assert ld.COMPANY_MAP == {k: (e["name"], e["expected_currency"], e["sector"], e["country"])
                                  for k, e in entries.items()}
