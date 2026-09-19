"""
Automated filing finder + downloader for filings.xbrl.org.

Goal: replace the manual "search website, click, download, rename" loop
with one command. Uses the site's public JSON:API.

Usage:
    # Search for a company by name (fuzzy match), see what's available:
    python scripts/00_find_filing.py --search "LVMH"

    # Once you know the entity's identifier (shown in search results),
    # download its most recent filing directly:
    python scripts/00_find_filing.py --entity <IDENTIFIER> --download --out data/raw/lvmh.zip

    # Batch mode: search AND download for several companies in one go.
    # Auto-picks the FIRST search match for each name - review the printed
    # summary afterward in case any name matched the wrong company.
    python scripts/00_find_filing.py --batch "Danone,Pernod Ricard,Essity,Inditex"
"""
import argparse
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

API_BASE = "https://filings.xbrl.org/api"


# ---------------------------------------------------------------- which filing is "the latest"?
#
# The archive's own `period_end` is NOT safe to sort by. Found live: it lists
# a Recordati FY2022 package (its filename says 2022-12-31) as period_end
# 2032-12-31, so "newest first by period_end" ranked that old file FIRST and
# we downloaded FY2022 while the real FY2025 report (added 2026-04-07) sat
# right below it; Carrefour's and Hermes's FY2025 reports are labelled
# 2026-12-31. Every script that picked a filing by period_end inherited this
# (this one, 14_scan_universe.py, download_historical.py - whose filenames
# load_historical.py then reads back as the fiscal year).
#
# The rules below use only signals the archive itself provides and never
# invent a date: a declared period that cannot be true (in the future, or
# after the report was published) is not trusted; it is replaced ONLY by a
# date the package's own filename states, if that one is possible; otherwise
# the period is reported as unknown. Same-period duplicates (language /
# version variants) are ranked by the archive's own validation counts.

_FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_BIG = 10 ** 9


def _to_datetime(value):
    """'2026-04-07 13:18:37.597068' / '2025-12-31' / None -> datetime, or None
    if it is missing or unparseable (never raises, never guesses)."""
    if not value:
        return None
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def package_filename_date(package_url):
    """The date embedded in the package's own filename, e.g.
    '.../815600FBF92FD3531704-2022-12-31-it.zip' -> 2022-12-31."""
    if not package_url:
        return None
    m = _FILENAME_DATE_RE.search(str(package_url).rstrip("/").split("/")[-1])
    d = _to_datetime(m.group(1)) if m else None
    return d.date() if d else None


def assess_period(attrs: dict, today: date = None) -> dict:
    """Can this filing's declared period_end be trusted?

    A period is possible only if it has already ended (<= today) and the
    report was not published before it ended (<= date_added). Returns
    {period, declared, plausible, note}: `period` is the declared date if
    possible; else the filename's date if THAT is possible; else None."""
    today = today or date.today()
    declared_dt = _to_datetime(attrs.get("period_end"))
    declared = declared_dt.date() if declared_dt else None
    added_dt = _to_datetime(attrs.get("date_added"))
    added = added_dt.date() if added_dt else None

    def possible(d):
        return d is not None and d <= today and (added is None or d <= added)

    if possible(declared):
        return {"period": declared, "declared": declared, "plausible": True, "note": ""}
    from_name = package_filename_date(attrs.get("package_url"))
    if possible(from_name):
        return {"period": from_name, "declared": declared, "plausible": True,
                "note": f"declared period_end {declared} is impossible (in the future or after "
                        f"the report was published); using the package filename's {from_name}"}
    return {"period": None, "declared": declared, "plausible": False,
            "note": f"declared period_end {declared} is impossible and the package filename "
                    f"gives no usable date - real period unknown"}


def _quality_key(filing: dict):
    """Lower is better: fewest validation errors, then inconsistencies, then
    warnings; then the earliest-added, then fxo_id, purely so the pick is
    reproducible."""
    a = filing.get("attributes", {})
    def n(key):
        return a[key] if a.get(key) is not None else _BIG
    return (n("error_count"), n("inconsistency_count"), n("warning_count"),
            str(a.get("date_added") or ""), str(a.get("fxo_id") or a.get("package_url") or ""))


def pick_best_package(group: list) -> dict:
    """Among packages for the SAME period (e.g. a /0/ and a /1/ variant),
    the one with the best archive validation record."""
    return min(group, key=_quality_key)


def choose_latest_filing(filings: list, today: date = None) -> dict:
    """The most recent filing that has a downloadable package.

    Returns {filing, period, label_unreliable, notes, alternatives}.
    Newest is decided by the TRUSTED period (see assess_period). One case
    overrides it: a filing whose label is impossible AND that was published
    after the newest filing with a possible label - that is the newest
    report with a typo'd label (Carrefour), not an old one (Recordati's typo
    was added years earlier, so it never qualifies). It is chosen but flagged
    label_unreliable with period=None: the real year must come from the
    filing's own facts, not from this label."""
    today = today or date.today()
    out = {"filing": None, "period": None, "label_unreliable": False, "notes": [], "alternatives": []}
    cands = [f for f in filings if f.get("attributes", {}).get("package_url")]
    if not cands:
        return out
    assessed = [(f, assess_period(f["attributes"], today)) for f in cands]
    plausible = [(f, a) for f, a in assessed if a["plausible"]]
    implausible = [(f, a) for f, a in assessed if not a["plausible"]]
    out["notes"] = [a["note"] for _, a in assessed if a["note"]]

    def added(f):
        return _to_datetime(f["attributes"].get("date_added")) or datetime.min

    best_period = None
    best = None
    if plausible:
        best_period = max(a["period"] for _, a in plausible)
        group = [f for f, a in plausible if a["period"] == best_period]
        best = pick_best_package(group)
        out["alternatives"] = [f for f in group if f is not best]

    newer = [f for f, _ in implausible if best is None or added(f) > added(best)]
    if newer:
        # latest-added wins; among equals the best validation record (max()
        # returns the first maximum, and the list is pre-sorted by quality)
        chosen = max(sorted(newer, key=_quality_key), key=added)
        out.update(filing=chosen, period=None, label_unreliable=True)
        if best is None:
            out["notes"].append("no filing has a possible period label - chose the most recently published")
        else:
            out["notes"].append(
                f"chose the filing with the impossible label because it was published "
                f"({chosen['attributes'].get('date_added')}) after the newest one with a possible label")
        return out

    out.update(filing=best, period=best_period)
    return out


def filings_by_period(filings: list, today: date = None, country_priority: dict = None):
    """One filing per TRUSTED period, for building a time series.
    Returns ({period: filing}, skipped) where skipped lists
    (filing, note) for filings whose period could not be established - never
    downloaded under a label that isn't true. Where several packages share a
    period, the preferred country wins (if given), then the best validation
    record."""
    today = today or date.today()
    groups, skipped = {}, []
    for f in filings:
        attrs = f.get("attributes", {})
        if not attrs.get("package_url"):
            continue
        a = assess_period(attrs, today)
        if not a["plausible"]:
            skipped.append((f, a["note"]))
            continue
        groups.setdefault(a["period"], []).append(f)
    prio = country_priority or {}
    chosen = {p: min(g, key=lambda f: (prio.get(f["attributes"].get("country", "ZZ"), 99), _quality_key(f)))
              for p, g in groups.items()}
    return chosen, skipped


def true_identifier(entity: dict) -> str:
    """The entity's own relationships.filings link always encodes the
    correct identifier for building further requests - more reliable
    than the top-level 'identifier' attribute, which some records omit
    (falling back to the internal numeric 'id' instead would silently
    point at the wrong entity)."""
    related = entity.get("relationships", {}).get("filings", {}).get("links", {}).get("related", "")
    m = re.match(r"/api/entities/([^/]+)/filings", related)
    if m:
        return m.group(1)
    return entity.get("identifier", entity["id"])  # last-resort fallback


def search_entities(name: str, max_pages: int = 15):
    """Search entities by name. Tries the server-side filter first (fast);
    if that returns nothing (filter syntax can vary by server version),
    falls back to paging through entities and matching client-side."""
    filt = f'[{{"name":"name","op":"ilike","val":"%{name}%"}}]'
    try:
        resp = requests.get(f"{API_BASE}/entities", params={"filter": filt, "page[size]": 20}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        if data:
            return data
    except requests.RequestException:
        pass

    # fallback: page through entities client-side, case-insensitive match
    print("(server-side filter returned nothing - falling back to a manual search, this takes a bit longer)")
    name_lower = name.lower()
    matches = []
    page = 1
    while page <= max_pages:
        resp = requests.get(f"{API_BASE}/entities", params={"page[size]": 200, "page[number]": page}, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        for e in body["data"]:
            if name_lower in e["attributes"].get("name", "").lower():
                matches.append(e)
        if not body["links"].get("next"):
            break
        page += 1
    return matches


def get_filings_for_entity(identifier: str, country: str = None):
    """Fetch filings for an entity via its direct relationship link -
    more reliable than constructing a filter, since this URL pattern is
    confirmed to exist on every entity object."""
    resp = requests.get(f"{API_BASE}/entities/{identifier}/filings", params={"sort": "-period_end"}, timeout=15)
    resp.raise_for_status()
    filings = resp.json()["data"]
    if country:
        filings = [f for f in filings if f["attributes"]["country"] == country]
    return filings


def report_choice(choice: dict):
    """Say out loud which filing was picked and why anything looked odd -
    a silently wrong 'latest' is how a stale FY2022 filing got loaded."""
    a = choice["filing"]["attributes"]
    period = choice["period"] or "unknown"
    print(f"Chosen filing: period={period}  added={str(a.get('date_added', ''))[:10]}  "
          f"errors={a.get('error_count')} warnings={a.get('warning_count')}")
    for note in choice["notes"]:
        print(f"  *** {note}")
    if choice["label_unreliable"]:
        print("  *** the period label is unreliable - the real fiscal year comes from the "
              "filing's own facts once loaded, not from this label")
    if choice["alternatives"]:
        print(f"  (note: {len(choice['alternatives'])} other package(s) exist for this same period; "
              f"took the one with the best validation record)")


def download_package(filing: dict, out_path: str):
    package_url = filing["attributes"].get("package_url")
    if not package_url:
        print("This filing has no downloadable package (package_url is empty).")
        print("This can happen for older or non-ESEF filings. Try a different one.")
        return False
    full_url = f"https://filings.xbrl.org{package_url}"
    print(f"Downloading {full_url} ...")
    resp = requests.get(full_url, timeout=60)
    resp.raise_for_status()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"Saved to {out_path} ({len(resp.content) / 1024:.0f} KB)")
    return True


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def batch_download(names: list, out_dir: str = "data/raw"):
    results = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
        try:
            entities = search_entities(name)
        except requests.RequestException as e:
            print(f"Search failed: {e}")
            results.append((name, "search failed", None))
            continue

        if not entities:
            print("No match found - skipping.")
            results.append((name, "no match", None))
            continue

        chosen = entities[0]
        identifier = true_identifier(chosen)
        matched_name = chosen["attributes"].get("name", "(no name)")
        print(f"Matched: {matched_name}  (identifier: {identifier})")
        if len(entities) > 1:
            print(f"  (note: {len(entities)} entities matched '{name}' - took the first one, double-check this is right)")

        try:
            filings = get_filings_for_entity(identifier)
        except requests.RequestException as e:
            print(f"Could not fetch filings: {e}")
            results.append((name, "filings fetch failed", identifier))
            continue

        choice = choose_latest_filing(filings)
        if choice["filing"] is None:
            print("No downloadable package found for this entity.")
            results.append((name, "no package", identifier))
            continue
        report_choice(choice)

        best = choice["filing"]
        out_path = f"{out_dir}/{slugify(name)}.zip"
        try:
            download_package(best, out_path)
            results.append((name, f"OK -> {out_path}", identifier))
        except requests.RequestException as e:
            print(f"Download failed: {e}")
            results.append((name, "download failed", identifier))

        time.sleep(1)  # light courtesy delay between requests

    print(f"\n\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, status, identifier in results:
        print(f"{name:30s} {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", help="Company name to search for")
    parser.add_argument("--entity", help="Entity identifier (shown in search results) to fetch filings for")
    parser.add_argument("--country", help="Filter filings to a specific country code, e.g. FR")
    parser.add_argument("--download", action="store_true", help="Download the most recent matching filing")
    parser.add_argument("--out", help="Output path for the downloaded zip")
    parser.add_argument("--batch", help="Comma-separated list of company names to search+download in one go")
    args = parser.parse_args()

    if args.batch:
        names = args.batch.split(",")
        batch_download(names)

    elif args.search:
        print(f"Searching for entities matching '{args.search}'...\n")
        entities = search_entities(args.search)
        if not entities:
            print("No matches found. Try a shorter or different search term.")
            sys.exit(0)
        for e in entities:
            identifier = true_identifier(e)
            name = e["attributes"].get("name", "(no name)")
            print(f"identifier: {identifier}   name: {name}")
        print(f"\nFound {len(entities)} entities. Copy an identifier above and rerun with --entity <identifier> to see its filings.")

    elif args.entity:
        print(f"Fetching filings for entity {args.entity}...\n")
        filings = get_filings_for_entity(args.entity, country=args.country)
        if not filings:
            print("No filings found for this entity" + (f" in country {args.country}" if args.country else "") + ".")
            sys.exit(0)

        for f in filings:
            a = f["attributes"]
            has_pkg = "yes" if a.get("package_url") else "no"
            assessment = assess_period(a)
            flag = "" if assessment["plausible"] and not assessment["note"] else "  [SUSPECT: " + assessment["note"] + "]"
            print(f"period_end={a['period_end']}  country={a['country']}  has_package={has_pkg}  "
                  f"added={str(a.get('date_added', ''))[:10]}  id={f['id']}{flag}")

        if args.download:
            choice = choose_latest_filing(filings)
            if choice["filing"] is None:
                print("\nNone of these filings have a downloadable package.")
                sys.exit(1)
            report_choice(choice)
            best = choice["filing"]
            out_path = args.out or f"data/raw/{args.entity}.zip"
            download_package(best, out_path)
        else:
            print("\nAdd --download --out <path> to download the most recent one automatically.")

    else:
        print("Provide either --search <name> or --entity <identifier>. See the script docstring for examples.")
