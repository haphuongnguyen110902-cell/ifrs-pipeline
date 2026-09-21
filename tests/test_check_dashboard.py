"""
tests/test_check_dashboard.py

scripts/check_dashboard.sh is what .github/workflows/keep_dashboard_awake.yml
runs. The workflow it replaces ran a plain `curl <url>` and never checked the
result: Streamlit Community Cloud answers a cookie-less request with
`303 -> share.streamlit.io/-/auth/app` (verified live: HTTP 303, and 50 redirect
hops without a cookie jar), so it never reached the app and reported success
every 12 hours while the app was asleep.

These tests run the REAL script against a local HTTP server that behaves like
the pieces of Streamlit Cloud that matter: a cookie handshake, a "gone to sleep"
page, and a server error. Skipped where no working bash/curl exists.
"""
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).parent.parent / "scripts" / "check_dashboard.sh").as_posix()

APP = b"<html><body><noscript>You need to enable JavaScript.</noscript><div id=root></div></body></html>"
SLEEP = b"<html><body><h1>Zzzz</h1><p>This app has gone to sleep due to inactivity.</p><button>Yes, get this app back up!</button></body></html>"


def _bash_works():
    if not shutil.which("bash") or not shutil.which("curl"):
        return False
    try:
        out = subprocess.run(["bash", "-c", "echo ok; command -v curl"], capture_output=True, text=True, timeout=20)
        return out.stdout.startswith("ok") and "curl" in out.stdout
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _bash_works(), reason="needs bash and curl")


def serve(handler_factory):
    server = HTTPServer(("127.0.0.1", 0), handler_factory)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def handler(kind):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if kind == "handshake" and "s=1" not in (self.headers.get("Cookie") or ""):
                # Streamlit Cloud: no cookie -> set one and bounce (loops forever without a cookie jar)
                self.send_response(303)
                self.send_header("Set-Cookie", "s=1; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return
            if kind == "error":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
                return
            body = SLEEP if kind == "asleep" else APP
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
    return H


def run(url):
    return subprocess.run(["bash", SCRIPT, url], capture_output=True, text=True, timeout=120)


@pytest.fixture
def server():
    started = []

    def start(kind):
        s, url = serve(handler(kind))
        started.append(s)
        return url

    yield start
    for s in started:
        s.shutdown()


def test_a_healthy_app_passes(server):
    r = run(server("ok"))
    assert r.returncode == 0 and "dashboard is up" in r.stdout


def test_the_cookie_handshake_is_followed_like_a_browser(server):
    """The old plain curl stopped at the first 303 and never reached the app."""
    r = run(server("handshake"))
    assert r.returncode == 0 and "final HTTP 200" in r.stdout


def test_an_app_that_has_gone_to_sleep_FAILS_the_job(server):
    """The whole point: a sleeping app is a red X, not a silent success."""
    r = run(server("asleep"))
    assert r.returncode == 1 and "ASLEEP" in r.stdout


def test_a_server_error_fails(server):
    r = run(server("error"))
    assert r.returncode == 1 and "HTTP 500" in r.stdout


def test_an_unreachable_host_fails_instead_of_hanging():
    r = run("http://127.0.0.1:9/")            # nothing listens on the discard port
    assert r.returncode == 1 and "not reachable" in r.stdout


def test_a_missing_url_is_a_usage_error():
    r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, timeout=30)
    assert r.returncode != 0


def test_the_workflow_calls_the_script_and_no_longer_uses_a_bare_curl():
    wf = (Path(__file__).parent.parent / ".github" / "workflows" / "keep_dashboard_awake.yml").read_text(encoding="utf-8")
    assert "scripts/check_dashboard.sh" in wf
    assert "curl -sS -o /dev/null" not in wf and "|| echo" not in wf, "the swallow-every-error pattern must not come back"
    assert "actions/checkout" in wf


def test_the_script_keeps_lf_line_endings():
    """CRLF would make bash fail with '\\r: command not found' on any checkout."""
    assert b"\r" not in Path(SCRIPT).read_bytes()
    attrs = (Path(__file__).parent.parent / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attrs
