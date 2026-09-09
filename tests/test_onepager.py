"""
tests/test_onepager.py

Regression tests for scripts/27_onepager.py (PLAN.md WP6 / ROADMAP.md
Phase 10). Two kinds of test here:

1. Pure-function tests for build_reconciliation() and the format
   helpers - synthetic data, no DB, run in every CI build.
2. A live, DB-guarded end-to-end smoke test that actually builds a PDF
   for every current company and checks it's exactly one page - skipped,
   not failed, when DATABASE_URL is absent, same deliberate exception as
   tests/test_baseline_regression.py. This is the test that would have
   caught the real bugs found building this (a NaN crashing reportlab's
   renderer, an un-escaped "&" garbling text, a precedent section
   spilling to a second page) if it had existed first.

NOTE: TestLiveGeneration needs `pypdf` (page-count checking) locally -
deliberately NOT added to requirements.txt, since it's only imported
inside that one live-DB-guarded test method, which never runs in CI (no
DATABASE_URL secret there - see tests.yml). `pip install pypdf` once if
running this class locally.
"""
import os

import pandas as pd
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def onepager(load_script):
    return load_script("27_onepager.py")


# ---------------------------------------------------------------- pure functions

def test_reconciliation_shows_all_three_methods_when_all_present(onepager):
    data = {
        "valuation": {"ev_eur": 200.0, "implied_ev_from_peers": 100.0, "n_peers_in_sector": 4},
        "dcf": {"enterprise_value": 150.0, "wacc": 0.07, "base_year": 2024},
    }
    reco = onepager.build_reconciliation(data)
    labels = [label for label, _, _ in reco["rows"]]
    assert labels == ["Actual market EV", "DCF Enterprise Value", "Peer-implied EV"]
    assert all(v is not None for _, v, _ in reco["rows"])
    assert "diverge" in reco["narrative"] or "converge" in reco["narrative"]


def test_reconciliation_never_crashes_on_a_nan_value(onepager):
    """The real bug found running this live: a genuine float NaN (not SQL
    NULL) in implied_ev_from_peers is NOT None, so a bare `is not None`
    check lets it through - which then crashed reportlab's renderer deep
    inside the bar chart. This must be caught here instead."""
    data = {
        "valuation": {"ev_eur": 200.0, "implied_ev_from_peers": float("nan"), "n_peers_in_sector": 2},
        "dcf": {"enterprise_value": 150.0, "wacc": 0.07, "base_year": 2024},
    }
    reco = onepager.build_reconciliation(data)
    peer_row = [r for r in reco["rows"] if r[0] == "Peer-implied EV"][0]
    assert peer_row[1] is None, "a NaN value must be treated as 'not available', never passed through as real"


def test_reconciliation_handles_missing_dcf_gracefully(onepager):
    data = {"valuation": {"ev_eur": 200.0, "implied_ev_from_peers": None, "n_peers_in_sector": 1}, "dcf": None}
    reco = onepager.build_reconciliation(data)
    dcf_row = [r for r in reco["rows"] if r[0] == "DCF Enterprise Value"][0]
    assert dcf_row[1] is None
    assert "not available" in dcf_row[2]
    assert "Only one valuation method" in reco["narrative"]


def test_reconciliation_handles_no_valuation_and_no_dcf_at_all(onepager):
    """The real Amplifon/Shell case: no gross_profit/cost_of_sales tagged
    at all means no 3-statement model, no DCF - only the actual market EV
    (if valuation itself resolved) should show."""
    data = {"valuation": None, "dcf": None}
    reco = onepager.build_reconciliation(data)
    present = [r for r in reco["rows"] if r[1] is not None]
    assert present == []
    assert "Only one valuation method" in reco["narrative"] or "not available" in reco["narrative"]


def test_bar_chart_omits_bars_for_missing_methods_not_zero_length(onepager):
    """A missing method must be OMITTED from the chart, never drawn as a
    zero-length bar (which would misleadingly suggest 'valued at zero')."""
    rows = [("Actual market EV", 200.0, ""), ("DCF Enterprise Value", None, "n/a"), ("Peer-implied EV", None, "n/a")]
    chart = onepager.build_valuation_bar_chart(rows)
    assert chart is not None  # one real value -> still draws that one bar

    chart_none = onepager.build_valuation_bar_chart([("A", None, ""), ("B", None, "")])
    assert chart_none is None  # zero real values -> no chart at all, not an empty/broken one


def test_fmt_helpers_handle_none_and_nan_without_crashing(onepager):
    for fn in (onepager.fmt_eur, onepager.fmt_pct, onepager.fmt_x, onepager.fmt_days):
        assert fn(None) == "n/a"
        assert fn(float("nan")) == "n/a"


def test_fmt_eur_picks_sensible_units(onepager):
    assert onepager.fmt_eur(2.5e9) == "EUR 2.5bn"
    assert onepager.fmt_eur(500e6) == "EUR 500m"
    assert onepager.fmt_eur(1234) == "EUR 1,234"


# ---------------------------------------------------------------- live smoke test

pytestmark_live = pytest.mark.skipif(
    not DATABASE_URL,
    reason="needs a live DATABASE_URL - see module docstring",
)


@pytest.fixture(scope="module")
def live_engine():
    return create_engine(DATABASE_URL) if DATABASE_URL else None


@pytestmark_live
class TestLiveGeneration:
    def test_every_current_company_generates_a_single_page_pdf(self, onepager, live_engine, tmp_path):
        """The actual verification PLAN.md WP6 asks for: generate for
        all companies and confirm each is readable and correct - here,
        specifically, that it's exactly one page (the whole point of a
        'one-pager') and that the ampersand-escaping fix holds for real
        data, not just the synthetic case above."""
        # Lazy import: pypdf is only needed for this one live-DB-guarded
        # test, which never runs in CI (no DATABASE_URL there) - a
        # module-level import would make it a hard dependency for every
        # CI run even though CI never exercises this path.
        from pypdf import PdfReader

        names = onepager.fetch_all_company_names(live_engine)
        assert len(names) >= 10  # sanity: this is the real universe, not an empty DB

        multi_page = []
        for name in names:
            data = onepager.assemble_onepager_data(live_engine, name)
            assert data is not None, f"{name}: assemble_onepager_data returned None"
            out_path = str(tmp_path / f"{name.replace(chr(39), '')}.pdf")
            onepager.build_onepager_pdf(data, out_path)

            reader = PdfReader(out_path)
            if len(reader.pages) != 1:
                multi_page.append((name, len(reader.pages)))

            text = reader.pages[0].extract_text()
            assert "&amp;" not in text and "&#" not in text, (
                f"{name}: raw XML entity leaked into rendered text - an escaping regression"
            )

        assert not multi_page, f"companies whose one-pager isn't exactly one page: {multi_page}"

    def test_reconciliation_narrative_present_for_every_company(self, onepager, live_engine):
        """Every company must get SOME reconciliation statement - never a
        blank valuation section, even when only one method resolves."""
        names = onepager.fetch_all_company_names(live_engine)
        for name in names:
            data = onepager.assemble_onepager_data(live_engine, name)
            reco = onepager.build_reconciliation(data)
            assert reco["narrative"], f"{name}: empty reconciliation narrative"
