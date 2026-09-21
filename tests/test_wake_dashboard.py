"""
tests/test_wake_dashboard.py

scripts/wake_dashboard.py is what .github/workflows/keep_dashboard_awake.yml runs. The HTTP-only checks it replaces
answered "up" (HTTP 200) for an app that was asleep: the app and its "gone to sleep" page are drawn by JavaScript, and the
app sits in an iframe of a page whose own body text is empty. The state machine is tested here against a fake page with a
fake clock (the real browser part needs Playwright and a sleeping app); the page structure it relies on was read from the
live app on 2026-09-21: title "IFRS Pipeline · Streamlit", iframe title "streamlitApp", landing text "16 of 16 companies",
sleep page "This app has gone to sleep due to inactivity ... Yes, get this app back up!". No network.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def wd(load_script):
    return load_script("wake_dashboard.py")


class FakePage:
    """A page that goes through the given states, one per poll; pressing the wake button moves it to `after_click`."""

    def __init__(self, script, after_click=None):
        self.script, self.after_click, self.i, self.clicks = list(script), after_click, 0, 0

    def _state(self):
        return self.script[min(self.i, len(self.script) - 1)]

    def title(self):
        return {"asleep": "Streamlit", "loading": "Streamlit", "awake": "IFRS Pipeline · Streamlit",
                "awake-error": "IFRS Pipeline · Streamlit"}[self._state()]

    def top_text(self):
        return "Zzzz\nThis app has gone to sleep due to inactivity.\nYes, get this app back up!" \
            if self._state() == "asleep" else ""

    def click_wake(self):
        if self._state() != "asleep":
            return False
        self.clicks += 1
        if self.after_click:
            self.script = list(self.after_click)
            self.i = -1
        return True

    def app_text(self):
        return "\U0001F4CA IFRS Pipeline\n16 of 16 companies" if self._state() == "awake" else ""


def run(wd, page, timeout=300):
    t = {"now": 0.0}

    def now():
        return t["now"]

    def sleep(s):
        t["now"] += s
        page.i += 1

    return wd.wake(page, timeout_s=timeout, poll_s=5, now=now, sleep=sleep)


class TestClassify:
    def test_the_loaded_app(self, wd):
        assert wd.classify("IFRS Pipeline · Streamlit", "") == "awake"

    def test_the_sleep_page(self, wd):
        assert wd.classify("Streamlit", "This app has gone to sleep due to inactivity.\nYes, get this app back up!") == "asleep"

    def test_a_blank_or_error_page_is_unknown_not_awake(self, wd):
        assert wd.classify("Streamlit", "") == "unknown" and wd.classify("", "Oh no. Error running app.") == "unknown"
        assert wd.classify(None, None) == "unknown"


class TestWake:
    def test_an_awake_app_is_reported_as_already_up(self, wd):
        page = FakePage(["awake"])
        assert run(wd, page) == "already-up" and page.clicks == 0

    def test_a_sleeping_app_is_woken_by_pressing_the_button_once(self, wd):
        page = FakePage(["asleep"], after_click=["loading", "loading", "awake"])
        assert run(wd, page) == "woken" and page.clicks == 1

    def test_a_loading_page_is_waited_for_not_clicked(self, wd):
        page = FakePage(["loading", "loading", "awake"])
        assert run(wd, page) == "already-up" and page.clicks == 0

    def test_a_title_without_the_landing_content_is_not_up(self, wd):
        """An error page that still carries the app title must not pass as a healthy dashboard."""
        assert run(wd, FakePage(["awake-error"]), timeout=30) == "timeout"

    def test_an_app_that_never_wakes_times_out_and_says_where_it_stuck(self, wd):
        page = FakePage(["asleep"])            # the button does nothing
        assert run(wd, page, timeout=30) == "timeout" and wd.wake.last_state == "asleep"


class TestWorkflow:
    wf = (ROOT / ".github" / "workflows" / "keep_dashboard_awake.yml").read_text(encoding="utf-8")

    def test_it_runs_the_browser_script_against_the_public_url(self):
        assert "scripts/wake_dashboard.py" in self.wf and "streamlit.app" in self.wf

    def test_it_installs_a_browser_and_has_a_timeout_and_a_schedule(self):
        assert "playwright install" in self.wf and "timeout-minutes" in self.wf and "cron:" in self.wf

    def test_the_http_only_check_that_could_not_see_the_sleep_page_is_gone(self):
        assert "check_dashboard.sh" not in self.wf
        assert not (ROOT / "scripts" / "check_dashboard.sh").exists()
