"""
tests/test_webapp_smoke.py

Renders the REAL webapp/app.py once per company, against the live database, with
Streamlit's own AppTest, and fails if any company's page raises an exception or
shows an error banner. Needs a live DATABASE_URL, so it is skipped (not failed)
without one - the same deliberate exception as tests/test_screener.py.

It is read-only apart from what the app itself does on every page load
(CREATE OR REPLACE the screener view).

Why it exists: the dashboard is public, and a company with thin data (the five
loaded from the universe have only two fiscal years and no valuation / DCF /
credit rows) is exactly where a page breaks. Verified by running this against all
16 companies: no exceptions, no error banners. This keeps it true.

AppTest cannot click a dataframe row, so st.dataframe is wrapped to report a chosen
row as selected - the same object shape Streamlit returns for a real click.
"""
import os
import types
from pathlib import Path
from unittest import mock

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                                reason="needs a live DATABASE_URL - see module docstring")

APP = Path(__file__).parent.parent / "webapp" / "app.py"


def render(position: int):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    real = st.dataframe

    def dataframe_with_selection(*a, **k):
        real(*a, **k)
        return types.SimpleNamespace(selection=types.SimpleNamespace(rows=[position]))

    with mock.patch.object(st, "dataframe", dataframe_with_selection):
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
    return at


def company_count(at) -> int:
    return int(at.subheader[0].value.split(" of ")[1].split(" ")[0])


@pytest.fixture(scope="module")
def first_page():
    return render(0)


def test_the_landing_page_loads_and_lists_every_company(first_page):
    assert not first_page.exception
    assert company_count(first_page) >= 11


def test_every_company_page_renders_without_an_exception_or_error_banner(first_page):
    failures = []
    for pos in range(company_count(first_page)):
        at = first_page if pos == 0 else render(pos)
        title = next((m.value for m in at.markdown if str(m.value).startswith("## ")), f"row {pos}")
        if at.exception or at.error:
            failures.append((title, [str(e.value)[:150] for e in list(at.exception) + list(at.error)]))
    assert not failures, failures
