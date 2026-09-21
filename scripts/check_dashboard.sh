#!/usr/bin/env bash
# Loads the dashboard the way a browser does and FAILS (exit 1) if it is not
# actually up. Used by .github/workflows/keep_dashboard_awake.yml.
#
# Why this exists: the old workflow ran a plain `curl <url>` and only printed
# the status, never checking it. Streamlit Community Cloud answers a cookie-less
# request with `303 -> share.streamlit.io/-/auth/app`, so that curl stopped at the
# first redirect, never reached the app, and "succeeded" every 12 hours while the
# app was asleep ("This app has gone to sleep due to inactivity"). Following the
# redirects needs a cookie jar (without one it loops for 50 hops).
#
# Usage: scripts/check_dashboard.sh <url>
# Exit:  0 up | 1 not up (non-200, asleep, or unreachable)
set -u

URL="${1:?usage: check_dashboard.sh <url>}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

code="$(curl -sSL --max-time 60 -c "$WORK/jar" -b "$WORK/jar" -o "$WORK/body.html" \
        -w '%{http_code}' "$URL" 2>"$WORK/err")" || code="curl-failed"
size="$(wc -c < "$WORK/body.html" 2>/dev/null || echo 0)"
echo "final HTTP ${code}, ${size} bytes"

if [ "$code" != "200" ]; then
  echo "::error::dashboard not reachable: HTTP ${code} $(head -c 200 "$WORK/err")"
  exit 1
fi

if grep -qiE "gone to sleep|get this app back up" "$WORK/body.html"; then
  echo "::error::dashboard is ASLEEP - Streamlit Community Cloud hibernated it. Visit ${URL} and press 'Yes, get this app back up!'"
  exit 1
fi

echo "dashboard is up"
exit 0
