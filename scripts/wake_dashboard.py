"""
Open the public dashboard in a real (headless) browser, wake it if Streamlit Community Cloud has put it to sleep, and
exit 1 - so GitHub emails the owner - if it does not come up with real content.

Why a browser: the "gone to sleep" page and the app itself are both drawn by JavaScript. The app sits in an iframe
("streamlitApp") of a page whose own HTML is nearly empty (the top-level body text is empty even when the app is up), so
the earlier HTTP-only check answered "up" (HTTP 200) whether the app was awake or asleep - the job was green at 03:31 UTC
on 2026-09-21 and the app was found asleep later that day. Only a browser session sees the sleep page, can press
"Yes, get this app back up!", and counts as the traffic that keeps the app from hibernating again.

Outcomes: already-up | woken (exit 0); timeout (exit 1).
Usage:  python scripts/wake_dashboard.py <url> [--timeout 300] [--settle 20]      (needs `pip install playwright`
        and `python -m playwright install chromium`)
"""
import argparse
import re
import sys
import time

APP_TITLE = "IFRS Pipeline"                                   # the page title once the app has loaded
SLEEP_TEXT = re.compile(r"get this app back up|gone to sleep", re.I)
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
APP_CONTENT = re.compile(r"\b\d+ of \d+ companies\b")         # the landing table's header, drawn inside the iframe


def classify(title: str, top_text: str) -> str:
    """'awake' (app title present) | 'asleep' (hibernation page) | 'unknown' (still loading or an error page)."""
    if APP_TITLE in (title or ""):
        return "awake"
    if SLEEP_TEXT.search(top_text or ""):
        return "asleep"
    return "unknown"


def wake(driver, timeout_s=300, poll_s=5, now=time.monotonic, sleep=time.sleep) -> str:
    """Drive the page until the app shows real content. `driver` needs title(), top_text(), click_wake() -> bool,
    app_text(). Returns 'already-up', 'woken' or 'timeout' (with the last state in `wake.last_state`)."""
    start, woke = now(), False
    while now() - start < timeout_s:
        state = classify(driver.title(), driver.top_text())
        wake.last_state = state
        if state == "asleep":
            woke = driver.click_wake() or woke
        elif state == "awake" and APP_CONTENT.search(driver.app_text() or ""):
            return "woken" if woke else "already-up"
        sleep(poll_s)
    return "timeout"


wake.last_state = "unknown"


class PlaywrightDriver:
    def __init__(self, page):
        self.page = page

    def title(self) -> str:
        return self.page.title()

    def top_text(self) -> str:
        try:
            return self.page.inner_text("body", timeout=3000)
        except Exception:
            return ""

    def click_wake(self) -> bool:
        button = self.page.get_by_role("button", name=WAKE_BUTTON)
        if button.count() and button.first.is_visible():
            button.first.click()
            return True
        return False

    def app_text(self) -> str:
        try:
            return self.page.frame_locator("iframe[title='streamlitApp']").locator("body").inner_text(timeout=3000)
        except Exception:
            return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for the app to show content")
    ap.add_argument("--settle", type=int, default=20, help="seconds to stay on the page once it is up")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(args.url, wait_until="domcontentloaded", timeout=90_000)
        outcome = wake(PlaywrightDriver(page), timeout_s=args.timeout)
        if outcome != "timeout":
            print(f"dashboard is up ({outcome}); staying {args.settle}s so the session counts as traffic")
            time.sleep(args.settle)
        browser.close()
    if outcome == "timeout":
        print(f"::error::dashboard did not come up within {args.timeout}s (last state: {wake.last_state}). "
              f"Open {args.url} and press 'Yes, get this app back up!' if it is asleep.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
