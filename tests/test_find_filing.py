"""
tests/test_find_filing.py

Regression tests for the "which filing is the latest / which period is true"
logic in scripts/00_find_filing.py, shared by 14_scan_universe.py and
download_historical.py.

The real bug: the archive (filings.xbrl.org) lists a Recordati FY2022 package
(filename ...-2022-12-31-it.zip) with period_end 2032-12-31. Every downloader
sorted "newest first" by period_end, so that OLD file ranked first and we
loaded FY2022 while the real FY2025 report (added 2026-04-07) sat right below
it. Carrefour's and Hermes's FY2025 reports are labelled 2026-12-31. The
fixtures below are those real archive records (only the fields the logic
reads); `today` is injected so the tests never depend on the calendar.
"""
import random
from datetime import date

import pytest

TODAY = date(2026, 9, 19)


@pytest.fixture(scope="module")
def ff(load_script):
    return load_script("00_find_filing.py")


def _f(period_end, date_added, package_url, *, errors=0, warnings=0, inconsistencies=0,
       country="IT", fxo=None):
    return {"id": package_url, "attributes": {
        "period_end": period_end, "date_added": date_added, "package_url": package_url,
        "country": country, "error_count": errors, "warning_count": warnings,
        "inconsistency_count": inconsistencies, "fxo_id": fxo or package_url}}


LEI = "815600FBF92FD3531704"
# --- Recordati, as the archive really lists it (newest-declared first) ---
R_TYPO = _f("2032-12-31", "2023-04-04 21:54:11.951129",
            f"/{LEI}/2032-12-31/ESEF/IT/0/{LEI}-2022-12-31-it.zip", warnings=5)
R_2025_A = _f("2025-12-31", "2026-04-07 13:18:37.597068",
              f"/{LEI}/2025-12-31/ESEF/IT/0/{LEI}-2025-12-31.zip", warnings=4)
R_2025_B = _f("2025-12-31", "2026-04-07 13:18:39.190951",
              f"/{LEI}/2025-12-31/ESEF/IT/1/{LEI}-2025-12-31.zip", warnings=10)
R_2024 = _f("2024-12-31", "2025-09-24 21:47:27.680191",
            f"/{LEI}/2024-12-31/ESEF/IT/0/{LEI}-2024-12-31-0-en.zip", warnings=5)
R_2022_REAL = _f("2022-12-31", "2023-04-04 21:54:11.951129",
                 f"/{LEI}/2022-12-31/ESEF/IT/0/{LEI}-2022-12-31-en.zip", warnings=5)
RECORDATI = [R_TYPO, R_2025_A, R_2025_B, R_2024, R_2022_REAL]

# --- Carrefour: FY2025 report labelled 2026-12-31 (added 2026-04-07) ---
C = "549300B8P6MUJ1YWTS08"
C_LABEL_2026 = _f("2026-12-31", "2026-04-07 12:10:25.502798",
                  f"/{C}/2026-12-31/ESEF/FR/0/{C}-2026-12-31.zip", country="FR", warnings=3)
C_2024 = _f("2024-12-31", "2025-04-15 09:00:00.000000",
            f"/{C}/2024-12-31/ESEF/FR/0/{C}-2024-12-31-0-fr.zip", country="FR")
C_2023 = _f("2023-12-31", "2024-04-02 09:00:00.000000",
            f"/{C}/2023-12-31/ESEF/FR/0/{C}-2023-12-31.zip", country="FR")
CARREFOUR = [C_LABEL_2026, C_2024, C_2023]


class TestPackageFilenameDate:
    def test_reads_the_date_in_the_package_filename(self, ff):
        assert ff.package_filename_date(f"/x/{LEI}-2022-12-31-it.zip") == date(2022, 12, 31)

    def test_lei_digits_are_not_mistaken_for_a_date(self, ff):
        assert ff.package_filename_date(f"/{LEI}/foo/{LEI}.zip") is None

    def test_none_and_empty(self, ff):
        assert ff.package_filename_date(None) is None
        assert ff.package_filename_date("") is None


class TestAssessPeriod:
    def test_a_normal_past_period_is_trusted(self, ff):
        a = ff.assess_period(R_2025_A["attributes"], TODAY)
        assert a["plausible"] and a["period"] == date(2025, 12, 31) and a["note"] == ""

    def test_future_declared_period_is_recovered_from_a_possible_filename_date(self, ff):
        """Recordati: declared 2032-12-31, filename says 2022-12-31."""
        a = ff.assess_period(R_TYPO["attributes"], TODAY)
        assert a["plausible"] and a["period"] == date(2022, 12, 31)
        assert "2032-12-31" in a["note"] and "2022-12-31" in a["note"]

    def test_impossible_label_with_no_recoverable_date_is_unknown_never_guessed(self, ff):
        """Carrefour: declared AND filename say 2026-12-31, but the report was
        published 2026-04-07. We do not invent 2025-12-31."""
        a = ff.assess_period(C_LABEL_2026["attributes"], TODAY)
        assert not a["plausible"] and a["period"] is None

    def test_a_period_ending_after_the_report_was_published_is_impossible(self, ff):
        a = ff.assess_period({"period_end": "2025-12-31", "date_added": "2025-06-01 10:00:00.000000",
                              "package_url": "/x/y.zip"}, TODAY)
        assert not a["plausible"]

    def test_a_possible_declared_period_is_not_overridden_by_the_filename(self, ff):
        a = ff.assess_period({"period_end": "2023-12-31", "date_added": "2024-04-02 09:00:00.000000",
                              "package_url": f"/x/{LEI}-2022-12-31.zip"}, TODAY)
        assert a["plausible"] and a["period"] == date(2023, 12, 31)

    def test_missing_date_added_falls_back_to_the_future_check_only(self, ff):
        assert ff.assess_period({"period_end": "2024-12-31", "package_url": "/x.zip"}, TODAY)["plausible"]
        assert not ff.assess_period({"period_end": "2031-12-31", "package_url": "/x.zip"}, TODAY)["plausible"]

    def test_unparseable_period_is_unknown(self, ff):
        a = ff.assess_period({"period_end": "not a date", "package_url": "/x.zip"}, TODAY)
        assert not a["plausible"] and a["period"] is None


class TestChooseLatestFiling:
    def test_recordati_gets_fy2025_not_the_typo_dated_fy2022_file(self, ff):
        """The exact bug: the old code took filings[0] of a period_end-descending
        list, i.e. the 2032-dated FY2022 package."""
        choice = ff.choose_latest_filing(RECORDATI, today=TODAY)
        assert choice["filing"] is R_2025_A
        assert choice["period"] == date(2025, 12, 31)
        assert not choice["label_unreliable"]

    def test_of_two_packages_for_the_same_period_the_better_validated_wins(self, ff):
        choice = ff.choose_latest_filing(RECORDATI, today=TODAY)
        assert choice["filing"] is R_2025_A            # 4 warnings, not 10
        assert choice["alternatives"] == [R_2025_B]

    def test_carrefour_newest_report_is_chosen_but_flagged_unreliable(self, ff):
        """The impossible-label filing WAS published after every filing with a
        possible label, so it is the newest report - chosen, never silently."""
        choice = ff.choose_latest_filing(CARREFOUR, today=TODAY)
        assert choice["filing"] is C_LABEL_2026
        assert choice["label_unreliable"] is True and choice["period"] is None
        assert any("published" in n for n in choice["notes"])

    def test_an_old_typo_never_qualifies_as_newest(self, ff):
        """Recordati's typo was added in 2023 - long before the newest real
        filing - so the 'published later' exception must not apply to it."""
        assert not ff.choose_latest_filing(RECORDATI, today=TODAY)["label_unreliable"]

    def test_an_unrecoverable_old_typo_does_not_beat_a_newer_real_filing(self, ff):
        """Recordati's own typo is recoverable from its filename. This is the
        harder variant: an impossible label (2032) whose filename gives no
        usable date either - but it was added in 2023, long before the newest
        real filing, so the 'published later' exception must not apply."""
        typo = _f("2032-12-31", "2023-04-04 21:54:11.951129", "/old/report.zip")
        real = _f("2025-12-31", "2026-04-07 13:18:37.597068", "/new/report-2025-12-31.zip")
        choice = ff.choose_latest_filing([typo, real], today=TODAY)
        assert choice["filing"] is real
        assert not choice["label_unreliable"] and choice["period"] == date(2025, 12, 31)

    def test_result_does_not_depend_on_input_order(self, ff):
        for listing in (RECORDATI, CARREFOUR):
            expected = ff.choose_latest_filing(listing, today=TODAY)["filing"]
            for seed in range(20):
                shuffled = listing[:]
                random.Random(seed).shuffle(shuffled)
                assert ff.choose_latest_filing(shuffled, today=TODAY)["filing"] is expected

    def test_only_impossible_labels_picks_the_most_recently_published(self, ff):
        older = _f("2040-12-31", "2020-01-01 00:00:00.000000", "/a/x.zip")
        newer = _f("2041-12-31", "2024-01-01 00:00:00.000000", "/b/y.zip")
        choice = ff.choose_latest_filing([older, newer], today=TODAY)
        assert choice["filing"] is newer and choice["label_unreliable"]

    def test_all_trusted_picks_the_newest_period(self, ff):
        choice = ff.choose_latest_filing([R_2022_REAL, R_2024], today=TODAY)
        assert choice["filing"] is R_2024

    def test_entries_without_a_package_are_ignored(self, ff):
        no_pkg = _f("2025-12-31", "2026-04-07 00:00:00.000000", None)
        assert ff.choose_latest_filing([no_pkg], today=TODAY)["filing"] is None
        assert ff.choose_latest_filing([], today=TODAY)["filing"] is None
        assert ff.choose_latest_filing([no_pkg, R_2024], today=TODAY)["filing"] is R_2024

    def test_fewer_errors_beats_fewer_warnings(self, ff):
        a = _f("2025-12-31", "2026-04-07 10:00:00.000000", "/a.zip", errors=0, warnings=9)
        b = _f("2025-12-31", "2026-04-07 10:00:00.000000", "/b.zip", errors=2, warnings=0)
        assert ff.pick_best_package([b, a]) is a


class TestFilingsByPeriod:
    def test_one_filing_per_trusted_period_and_the_typo_is_recovered(self, ff):
        by_period, skipped = ff.filings_by_period(RECORDATI, today=TODAY)
        assert sorted(by_period) == [date(2022, 12, 31), date(2024, 12, 31), date(2025, 12, 31)]
        assert by_period[date(2025, 12, 31)] is R_2025_A
        # the typo'd file (really FY2022) and the real FY2022 file share a period -> one wins
        assert by_period[date(2022, 12, 31)] in (R_TYPO, R_2022_REAL)
        assert skipped == []

    def test_a_filing_whose_period_cannot_be_established_is_skipped_not_mislabelled(self, ff):
        by_period, skipped = ff.filings_by_period(CARREFOUR, today=TODAY)
        assert sorted(by_period) == [date(2023, 12, 31), date(2024, 12, 31)]
        assert len(skipped) == 1 and skipped[0][0] is C_LABEL_2026
        assert "unknown" in skipped[0][1]

    def test_preferred_country_wins_a_shared_period_then_quality(self, ff):
        fr = _f("2024-12-31", "2025-04-15 09:00:00.000000", "/fr.zip", country="FR", warnings=50)
        nl = _f("2024-12-31", "2025-04-15 09:00:00.000000", "/nl.zip", country="NL", warnings=0)
        by_period, _ = ff.filings_by_period([nl, fr], today=TODAY, country_priority={"FR": 0, "NL": 1})
        assert by_period[date(2024, 12, 31)] is fr


class TestSharedWiring:
    """The scan and historical-download scripts must use THIS module's rules
    (loaded by importlib per the project convention) - a broken path would
    silently fall back to nothing at import time."""

    def test_scan_universe_uses_the_shared_selection(self, load_script):
        scan = load_script("14_scan_universe.py")
        assert scan.ff.choose_latest_filing is not None

    def test_download_historical_uses_the_shared_selection(self, load_script):
        dl = load_script("download_historical.py")
        assert dl.ff.filings_by_period is not None
