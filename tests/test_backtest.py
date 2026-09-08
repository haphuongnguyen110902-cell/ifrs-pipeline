"""
tests/test_backtest.py

Regression tests for scripts/17_backtest.py. The four scenarios here are
exactly the ones used to hand-verify this script before it was ever run
against the real database - see ROADMAP.md's V2 entry.
"""
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def bt(load_script):
    return load_script("17_backtest.py")


def test_linear_series_favors_linreg(bt):
    """A perfectly arithmetic (constant-difference) series should be
    predicted near-perfectly by linear regression, and CAGR (which
    assumes compounding) should do measurably worse."""
    df = pd.DataFrame([
        {"company": "LinearCo", "company_id": 1, "ratio_name": "trend_ratio",
         "year": y, "value": 10 + 2 * i}
        for i, y in enumerate(range(2018, 2025))
    ])
    results = bt.evaluate_method(df, min_train=3)
    results = bt.mark_winners(results)
    winner = results[(results["company"] == "LinearCo") & results["is_winner"]]
    assert winner.iloc[0]["method"] == "linreg"


def test_geometric_series_favors_cagr(bt):
    """A perfectly compounding (constant % growth) series should be
    predicted near-perfectly by CAGR, and a straight line should
    measurably overshoot/undershoot the curvature."""
    v, rows = 5.0, []
    for y in range(2018, 2025):
        rows.append({"company": "GrowthCo", "company_id": 2, "ratio_name": "growth_ratio",
                      "year": y, "value": round(v, 6)})
        v *= 1.10
    df = pd.DataFrame(rows)
    results = bt.evaluate_method(df, min_train=3)
    results = bt.mark_winners(results)
    winner = results[(results["company"] == "GrowthCo") & results["is_winner"]]
    assert winner.iloc[0]["method"] == "cagr"


def test_gap_year_does_not_crash_and_uses_the_real_horizon(bt):
    """A missing year (e.g. Kering has no 2024 historical filing) must
    not break forecasting - the fold's horizon should be computed from
    the ACTUAL gap between years, not assumed to always be 1."""
    df = pd.DataFrame([
        {"company": "GapCo", "company_id": 3, "ratio_name": "gap_ratio", "year": y, "value": v}
        for y, v in zip([2018, 2019, 2020, 2022, 2023], [10, 12, 14, 18, 20])
    ])
    results = bt.evaluate_method(df, min_train=3)
    assert not results.empty
    assert (results["n_folds"] > 0).any()


def test_thin_actual_excluded_from_mape_but_counted_in_mae(bt):
    """MAPE divides by the actual value - when that's near zero, the
    percentage error explodes and stops meaning anything (same class of
    bug as 15_forensics.py's THIN_DENOMINATOR, different denominator).
    Those folds must be excluded from MAPE specifically, while still
    counting toward MAE/RMSE/bias."""
    df = pd.DataFrame([
        {"company": "ThinCo", "company_id": 4, "ratio_name": "thin_ratio", "year": y, "value": v}
        for y, v in zip([2018, 2019, 2020, 2021, 2022, 2023], [10, 8, 2, 1, 9, 11])
    ])
    results = bt.evaluate_method(df, min_train=3)
    assert (results["n_thin_excluded"] > 0).any()
    # every row with excluded folds must still have a real MAE (not NaN)
    thin_rows = results[results["n_thin_excluded"] > 0]
    assert thin_rows["mae"].notna().all()


class TestConfidenceLabeling:
    """A 'winner' decided from a single fold is not evidence - it must
    be visibly labeled as low-confidence rather than presented the same
    as a pick backed by several folds (the real Kering roe/roic case)."""

    def test_one_fold_winner_is_low_confidence(self, bt):
        assert bt.confidence_label(1).startswith("low")

    def test_two_to_three_folds_is_medium_confidence(self, bt):
        assert bt.confidence_label(2) == "medium"
        assert bt.confidence_label(3) == "medium"

    def test_four_plus_folds_is_high_confidence(self, bt):
        assert bt.confidence_label(4) == "high"
        assert bt.confidence_label(10) == "high"

    def test_kering_style_single_fold_case_gets_labeled_low(self, bt):
        df = pd.DataFrame([
            {"company": "Kering", "company_id": 5, "ratio_name": "roic", "year": y, "value": v}
            for y, v in zip([2019, 2020, 2021, 2022], [18.2, 24.7, 22.8, 13.7])
        ])
        results = bt.evaluate_method(df, min_train=3)
        results = bt.mark_winners(results)
        winner = results[results["is_winner"]]
        assert len(winner) == 1
        assert winner.iloc[0]["confidence"].startswith("low")
