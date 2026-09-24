"""
tests/test_dcf_beta.py

The DCF built its cost of equity on yfinance's info["beta"] - Yahoo's beta against the S&P 500 - next to a German
Bund risk-free rate and a mature-market equity risk premium. Found when Shell's first DCF came out (Phase 3): beta
-0.22, cost of equity 2.5%, WACC 2.47%, enterprise value EUR 3.6 trillion against a EUR 240bn market cap. The beta is
now the pipeline's own regression against STOXX Europe 600 (23_market_risk.py, the Market Risk tab's number),
Blume-adjusted; a regression beta that is not positive refuses the DCF. Shell's regression itself had been run on its
NYSE listing (beta -0.03, correlation -0.02 against a European index closing hours earlier): it now uses Euronext
Amsterdam. No database.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def dcf(load_script):
    return load_script("22_dcf.py")


@pytest.fixture(scope="module")
def mr(load_script):
    return load_script("23_market_risk.py")


class _Conn:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        return SimpleNamespace(fetchone=lambda: self.row)


class _Engine:
    def __init__(self, row):
        self.row = row

    def connect(self):
        return _Conn(self.row)


def row(beta, ticker="HEIA.AS"):
    return SimpleNamespace(beta=beta, correlation=0.27, n_observations=247, ticker=ticker, benchmark="^STOXX",
                           period_start="2025-09-22", period_end="2026-09-21")


class TestBlume:
    def test_pulls_toward_one(self, dcf):
        assert dcf.blume_adjusted(0.48) == pytest.approx(0.67 * 0.48 + 0.33)
        assert dcf.blume_adjusted(1.0) == pytest.approx(1.0)
        assert dcf.blume_adjusted(1.95) < 1.95


class TestDcfBeta:
    def test_the_stored_regression_beta_is_used_adjusted(self, dcf):
        b = dcf.dcf_beta(_Engine(row(0.48)), "Heineken")
        assert b["raw"] == 0.48 and b["adjusted"] == pytest.approx(dcf.blume_adjusted(0.48))
        assert "STOXX Europe 600" in b["source"] and "HEIA.AS" in b["source"]

    def test_a_beta_that_is_not_positive_refuses_the_dcf(self, dcf):
        b = dcf.dcf_beta(_Engine(row(-0.03, "SHEL")), "Shell")
        assert "adjusted" not in b and "not positive" in b["reason"]

    def test_no_stored_regression_refuses_the_dcf(self, dcf):
        assert "run 23_market_risk.py" in dcf.dcf_beta(_Engine(None), "X")["reason"]

    def test_no_third_party_beta_is_fetched(self, dcf):
        assert not hasattr(dcf, "fetch_beta")


class TestRegressionListing:
    def test_shell_is_regressed_on_its_european_listing(self, mr):
        assert mr.REGRESSION_LISTING["Shell"] == "SHELL.AS"


class TestDashboardReason:
    @pytest.fixture
    def reason(self):
        tree = ast.parse((REPO / "webapp" / "app.py").read_text(encoding="utf-8"))
        parts = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "dcf_absence_reason"]
        assert len(parts) == 1
        ns = {"pd": pd}
        exec(compile(ast.Module(body=parts, type_ignores=[]), "app.py", "exec"), ns)
        return ns["dcf_absence_reason"]

    def test_a_refused_dcf_says_why(self, reason):
        mr = pd.DataFrame([{"beta": -0.34, "ticker": "SHELL.AS", "period_start": "2025-09-24", "period_end": "2026-09-24"}])
        text_ = reason(mr)
        assert "Not built on purpose" in text_ and "-0.34" in text_

    def test_otherwise_the_generic_message(self, reason):
        assert "hasn't been computed" in reason(pd.DataFrame([{"beta": 0.48, "ticker": "X",
                                                              "period_start": "a", "period_end": "b"}]))
        assert "hasn't been computed" in reason(pd.DataFrame())
