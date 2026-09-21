"""
tests/test_forensics_revenue_determinism.py

15_forensics.fetch_revenue_growth picked one revenue per (company, year) with `.first()` over a
query ordered only by (company, start_date). When several filings report the same year the
winner depended on row order. Measured on the live data: 10 repeated calls agreed, but
(Essity, 2022) holds two different revenues (131,320M and 156,173M SEK), so the choice was left
to chance - and deeper history (every year now appears in two filings for the universe
companies) makes such conflicts more likely.

pick_revenue_per_year() sorts explicitly, so the result cannot depend on row order. No database.
"""
import itertools

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def f15(load_script):
    return load_script("15_forensics.py")


def rows(*specs):
    return pd.DataFrame([{"company": c, "start_date": sd, "revenue": rev, "filing_id": fid, "filing_end": fe}
                         for c, sd, rev, fid, fe in specs])


ESSITY_CONFLICT = [
    ("Essity", "2022-01-01", 131_320.0, 11, "2022-12-31"),     # the year's own filing, as first reported
    ("Essity", "2022-01-01", 156_173.0, 12, "2023-12-31"),     # a later filing's comparative (restated basis)
    ("Essity", "2023-01-01", 160_000.0, 12, "2023-12-31"),
]


class TestPickRevenuePerYear:
    def test_the_latest_filing_wins(self, f15):
        out = f15.pick_revenue_per_year(rows(*ESSITY_CONFLICT))
        assert dict(zip(out["year"], out["revenue"])) == {2022: 156_173.0, 2023: 160_000.0}

    def test_the_result_is_the_same_for_every_row_order(self, f15):
        """The point of the fix: all 6 permutations of the input give one answer."""
        answers = set()
        for perm in itertools.permutations(ESSITY_CONFLICT):
            out = f15.pick_revenue_per_year(rows(*perm))
            answers.add(tuple(zip(out["year"], out["revenue"])))
        assert len(answers) == 1

    def test_a_tie_on_filing_end_falls_to_the_higher_filing_id_then_the_larger_value(self, f15):
        out = f15.pick_revenue_per_year(rows(("A", "2022-01-01", 1.0, 5, "2022-12-31"),
                                             ("A", "2022-01-01", 2.0, 6, "2022-12-31")))
        assert out["revenue"].iloc[0] == 2.0
        out = f15.pick_revenue_per_year(rows(("A", "2022-01-01", 1.0, 5, "2022-12-31"),
                                             ("A", "2022-01-01", 9.0, 5, "2022-12-31")))
        assert out["revenue"].iloc[0] == 9.0

    def test_a_missing_value_never_beats_a_real_one(self, f15):
        out = f15.pick_revenue_per_year(rows(("A", "2022-01-01", None, 9, "2023-12-31"),
                                             ("A", "2022-01-01", 5.0, 3, "2022-12-31")))
        assert out["revenue"].iloc[0] == 5.0

    def test_companies_and_years_stay_separate(self, f15):
        out = f15.pick_revenue_per_year(rows(("A", "2021-01-01", 1.0, 1, "2021-12-31"),
                                             ("A", "2022-01-01", 2.0, 1, "2022-12-31"),
                                             ("B", "2022-01-01", 7.0, 2, "2022-12-31")))
        assert sorted(zip(out["company"], out["year"], out["revenue"])) == [("A", 2021, 1.0), ("A", 2022, 2.0), ("B", 2022, 7.0)]

    def test_growth_is_computed_on_the_picked_series(self, f15, monkeypatch):
        """End to end through fetch_revenue_growth with a faked query result."""
        frame = rows(*ESSITY_CONFLICT)
        monkeypatch.setattr(f15.pd, "read_sql", lambda *a, **k: frame.sample(frac=1, random_state=7))
        g = f15.fetch_revenue_growth(engine=None)
        assert g.loc[g["year"] == 2023, "revenue_growth"].iloc[0] == pytest.approx((160_000 / 156_173 - 1) * 100)
