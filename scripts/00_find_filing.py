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
from pathlib import Path

import requests

API_BASE = "https://filings.xbrl.org/api"


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

        candidates = [f for f in filings if f["attributes"].get("package_url")]
        if not candidates:
            print("No downloadable package found for this entity.")
            results.append((name, "no package", identifier))
            continue

        best = candidates[0]  # most recent, already sorted
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
            print(f"period_end={a['period_end']}  country={a['country']}  has_package={has_pkg}  id={f['id']}")

        if args.download:
            # take the most recent filing that actually has a package
            candidates = [f for f in filings if f["attributes"].get("package_url")]
            if not candidates:
                print("\nNone of these filings have a downloadable package.")
                sys.exit(1)
            best = candidates[0]  # already sorted most-recent-first
            out_path = args.out or f"data/raw/{args.entity}.zip"
            download_package(best, out_path)
        else:
            print("\nAdd --download --out <path> to download the most recent one automatically.")

    else:
        print("Provide either --search <name> or --entity <identifier>. See the script docstring for examples.")
