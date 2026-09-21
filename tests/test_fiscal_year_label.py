"""
tests/test_fiscal_year_label.py

A fact's fiscal year is the year its period ENDS. Durations used to be labelled by the year they START, which is the
same thing for a calendar-year filer but split a broken fiscal year in two. Found in the inventory / payables / D&A /
capex audit against the companies' own reports: Pernod Ricard (FYE 30 June) had FY2025 (1 Jul 2024 - 30 Jun 2025)
revenue and EBIT labelled 2024 while its 30 Jun 2025 inventories and debt were labelled 2025, so the row for "2024"
paired FY2025 flows with FY2024 balances (DIO, DPO, ROIC and net debt / EBITDA all one year out) and the latest year
had no flows at all. Arelle stores every end date one day late, hence the 1-July dates below. No database.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"


@pytest.fixture(scope="module")
def r11(load_script):
    return load_script("11_ratio_engine.py")


@pytest.fixture(scope="module")
def f15(load_script):
    return load_script("15_forensics.py")


class TestHelper:
    def test_pernod_fy2025_duration_is_2025_like_its_balance_sheet(self, r11):
        assert r11.fiscal_year_label("duration", date(2024, 7, 1), date(2025, 7, 1)) == 2025
        assert r11.fiscal_year_label("instant", None, date(2025, 7, 1)) == 2025

    def test_pernod_fy2024(self, r11):
        assert r11.fiscal_year_label("duration", date(2023, 7, 1), date(2024, 7, 1)) == 2024

    def test_calendar_year_filer_is_unchanged(self, r11):
        assert r11.fiscal_year_label("duration", date(2024, 1, 1), date(2025, 1, 1)) == 2024
        assert r11.fiscal_year_label("instant", None, date(2025, 1, 1)) == 2024

    def test_a_52_week_year_is_still_a_fiscal_year(self, r11):
        assert r11.fiscal_year_label("duration", date(2023, 12, 31), date(2024, 12, 29)) == 2024

    def test_a_stub_keeps_its_old_start_year_label(self, r11):
        assert r11.fiscal_year_label("duration", date(2024, 1, 1), date(2024, 7, 1)) == 2024

    def test_a_filer_defect_multi_year_period_does_not_create_a_phantom_year(self, r11):
        """Recordati's filing carries a duration 2021-01-01 -> 2033-01-01; Schneider one of two years."""
        assert r11.fiscal_year_label("duration", date(2021, 1, 1), date(2033, 1, 1)) == 2021
        assert r11.fiscal_year_label("duration", date(2021, 1, 1), date(2023, 1, 1)) == 2021

    def test_timestamps_work_too(self, r11):
        assert r11.fiscal_year_label("duration", pd.Timestamp("2024-07-01"), pd.Timestamp("2025-07-01")) == 2025


class TestFetchFactsOnABrokenFiscalYear:
    @staticmethod
    def _rows():
        def row(name, ptype, start, end, value):
            return {"company": "Pernod", "company_id": 1, "filing_id": 10, "normalized_name": name,
                    "period_type": ptype, "start_date": start, "end_date": end, "value": value, "currency": "EUR"}
        return pd.DataFrame([
            row("revenue", "duration", date(2023, 7, 1), date(2024, 7, 1), 11_598e6),
            row("revenue", "duration", date(2024, 7, 1), date(2025, 7, 1), 10_959e6),
            row("profit_loss_from_operating_activities", "duration", date(2024, 7, 1), date(2025, 7, 1), 2_743e6),
            row("inventories", "instant", None, date(2024, 7, 1), 8_255e6),
            row("inventories", "instant", None, date(2025, 7, 1), 8_418e6),
        ])

    def test_flows_and_balances_of_one_fiscal_year_share_a_row(self, r11, monkeypatch):
        monkeypatch.setattr(pd, "read_sql", lambda *a, **k: self._rows())
        wide = r11.pivot_to_wide(r11.fetch_facts(engine=None))
        row25 = wide[wide["year"] == 2025].iloc[0]
        assert row25["revenue"] == pytest.approx(10_959e6)
        assert row25["profit_loss_from_operating_activities"] == pytest.approx(2_743e6)
        assert row25["inventories"] == pytest.approx(8_418e6)
        assert sorted(wide["year"]) == [2024, 2025]          # not 2023/2024/2025

    def test_ratios_no_longer_mix_a_year_of_flows_with_the_previous_balance(self, r11, monkeypatch):
        monkeypatch.setattr(pd, "read_sql", lambda *a, **k: self._rows())
        wide = r11.pivot_to_wide(r11.fetch_facts(engine=None))
        wide["cost_of_sales"] = -4_000e6
        ratios = r11.compute_ratios(wide).set_index("year")
        assert ratios.loc[2025, "dio"] == pytest.approx(8_418e6 / 4_000e6 * 365)


class TestForensicsRevenueYear:
    @staticmethod
    def rows(*specs):
        return pd.DataFrame([{"company": "P", "start_date": sd, "end_date": ed, "revenue": rev,
                              "filing_id": 1, "filing_end": "2025-07-01"} for sd, ed, rev in specs])

    def test_fiscal_year_is_the_year_the_period_ends(self, f15):
        out = f15.pick_revenue_per_year(self.rows(("2023-07-01", "2024-07-01", 11_598.0),
                                                  ("2024-07-01", "2025-07-01", 10_959.0)))
        assert dict(zip(out["year"], out["revenue"])) == {2024: 11_598.0, 2025: 10_959.0}

    def test_a_calendar_year_is_unchanged(self, f15):
        out = f15.pick_revenue_per_year(self.rows(("2023-01-01", "2024-01-01", 1.0), ("2024-01-01", "2025-01-01", 2.0)))
        assert dict(zip(out["year"], out["revenue"])) == {2023: 1.0, 2024: 2.0}


class TestNoScriptLabelsYearsByStartDateAnymore:
    """The rule lives in one place (11_ratio_engine.fiscal_year_label); a second copy is how 07 and 11 drifted."""

    @pytest.mark.parametrize("script", ["07_generate_statements.py", "11_ratio_engine.py", "15_forensics.py"])
    def test_no_start_date_year_labelling(self, script):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        assert 'row["start_date"].year' not in text
        assert '["start_date"]).dt.year' not in text
