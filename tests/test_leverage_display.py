"""
tests/test_leverage_display.py

Checking LVMH against its own FY2024 report and results press release: our stored inputs are all right
(borrowings 12,091 / 10,851, leases 14,860 / 2,972, cash 9,631, operating profit 18,907 - each equals the printed
figure), but the dashboard showed "Net Debt/EBITDA 1.7x, Low leverage" where

  * "EBITDA" was really EBIT (LVMH prints D&A only inside two cash-flow lines, one of them bundled with
    provisions and impairment, so the pipeline correctly falls back to EBIT and flags is_da_fallback - which the
    landing-page screener ignored), and
  * net debt includes IFRS 16 leases (EUR 17.8bn), whereas LVMH's own net financial debt is EUR 9,228M.

Six companies rest entirely on that fallback (L'Oreal, Schneider, Recordati, EssilorLuxottica, LVMH, Kering).
Where EBITDA cannot be derived, the multiple, its band and its trend are now shown as n/a*, with a footnote that
also explains the lease difference. No database, no Streamlit: the helpers are extracted from webapp/app.py.
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

APP = Path(__file__).parent.parent / "webapp" / "app.py"
NAMES = {"_is_true", "ebitda_multiple_text", "leverage_label"}
CONSTANTS = {"NOT_SHOWN", "LEVERAGE_FOOTNOTE"}


@pytest.fixture(scope="module")
def app():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in NAMES)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in CONSTANTS for t in n.targets))]
    ns = {"pd": pd}
    exec(compile(ast.Module(body=body, type_ignores=[]), "app.py", "exec"), ns)
    return ns


class TestMultiple:
    def test_a_fallback_multiple_is_not_shown_whatever_its_value(self, app):
        assert app["ebitda_multiple_text"](1.65, True) == "n/a*"
        assert app["ebitda_multiple_text"](0.24, True, "{:.2f}x") == "n/a*"

    def test_a_real_ebitda_multiple_is_shown(self, app):
        assert app["ebitda_multiple_text"](2.41, False) == "2.4x"
        assert app["ebitda_multiple_text"](2.414, False, "{:.2f}x") == "2.41x"

    def test_missing_flag_or_value(self, app):
        f = app["ebitda_multiple_text"]
        assert f(2.0, None) == "2.0x" and f(2.0, float("nan")) == "2.0x" and f(2.0, pd.NA) == "2.0x"
        assert f(float("nan"), False) == "n/a" and f(None, None) == "n/a"

    def test_numpy_and_pandas_flags_work(self, app):
        assert app["ebitda_multiple_text"](1.0, np.True_) == "n/a*"
        assert app["ebitda_multiple_text"](1.0, np.False_) == "1.0x"


class TestBandAndTrend:
    def test_hidden_for_a_fallback_company(self, app):
        assert app["leverage_label"]("Low leverage", True) == "n/a*"
        assert app["leverage_label"]("Deteriorating", True) == "n/a*"

    def test_shown_otherwise(self, app):
        assert app["leverage_label"]("Moderate leverage", False) == "Moderate leverage"
        assert app["leverage_label"](None, False) == "n/a"
        assert app["leverage_label"]("Stable", None) == "Stable"


class TestFootnote:
    def test_it_explains_both_reasons_in_plain_words(self, app):
        note = app["LEVERAGE_FOOTNOTE"]
        assert note.startswith("* n/a*") and "EBITDA" in note and "EBIT" in note
        assert "lease" in note and "LVMH" in note and ".py" not in note

    def test_the_marker_and_the_footnote_agree(self, app):
        assert app["NOT_SHOWN"] == "n/a*" and app["LEVERAGE_FOOTNOTE"].startswith("* " + app["NOT_SHOWN"])


class TestWiring:
    """The helpers only matter if the screener and the credit tab actually use them."""
    SRC = APP.read_text(encoding="utf-8")

    @pytest.mark.parametrize("column", ["EV/EBITDA", "Net Debt/EBITDA", "Credit Band"])
    def test_every_screener_leverage_column_is_masked(self, column):
        line = next(l for l in self.SRC.splitlines() if f'"{column}": [' in l)
        assert "ebitda_multiple_text(" in line or "leverage_label(" in line

    def test_the_screener_passes_the_fallback_flag(self):
        assert self.SRC.count('screener_filtered["credit_is_da_fallback"]') >= 4

    def test_the_credit_tab_masks_multiple_band_and_trend(self):
        tab = self.SRC[self.SRC.index("def render_credit_profile"):self.SRC.index("def render_backtest")]
        assert "ebitda_multiple_text(" in tab and tab.count("leverage_label(") >= 4      # 2 metrics + 2 table columns
        assert "LEVERAGE_FOOTNOTE" in tab

    def test_the_old_wording_that_called_the_figure_merely_overstated_is_gone(self):
        assert "silently equals EBIT" not in self.SRC and "(overstated)" not in self.SRC
