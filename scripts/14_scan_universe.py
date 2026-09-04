"""
Company universe scanner.

Scans filings.xbrl.org for ALL ESEF filers in a given country and
compares against what's already in your database. Shows you what's
new - companies you haven't loaded yet, or companies with newer
filings than what you have.

Usage:
    python scripts/13_scan_universe.py --country FR
    python scripts/13_scan_universe.py --country FR --country IT
    python scripts/13_scan_universe.py --country FR --show-all
    python scripts/13_scan_universe.py --country FR --download --out-dir data/raw
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

API_BASE = "https://filings.xbrl.org/api"


def true_identifier(entity: dict) -> str:
    """Extract the real identifier from the entity's relationship link."""
    related = entity.get("relationships", {}).get("filings", {}).get("links", {}).get("related", "")
    m = re.match(r"/api/entities/([^/]+)/filings", related)
    if m:
        return m.group(1)
    return entity.get("identifier", entity["id"])


def get_entity_name(identifier: str, cache: dict) -> str:
    """Fetch the real company name for an entity identifier."""
    if identifier in cache:
        return cache[identifier]
    try:
        resp = requests.get(
            f"{API_BASE}/entities/{identifier}",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("data", {}).get("attributes", {}).get("name", identifier)
            cache[identifier] = name
            return name
    except Exception:
        pass
    cache[identifier] = identifier
    return identifier


def get_all_filings_for_country(country: str, max_pages: int = 50) -> list:
    """Page through all filings for a country, most recent first."""
    print(f"Scanning filings.xbrl.org for country={country}...")
    all_filings = []
    page = 1
    while page <= max_pages:
        try:
            resp = requests.get(
                f"{API_BASE}/filings",
                params={
                    "filter[country]": country,
                    "sort": "-period_end",
                    "page[size]": 100,
                    "page[number]": page,
                    "include": "entity",
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  API error on page {page}: {e}")
            break

        body = resp.json()
        filings = body.get("data", [])
        if not filings:
            break

        all_filings.extend(filings)
        print(f"  Page {page}: {len(filings)} filings (total so far: {len(all_filings)})")

        if not body.get("links", {}).get("next"):
            break
        page += 1
        time.sleep(0.5)  # polite delay

    return all_filings


def get_companies_in_db(engine) -> set:
    """Return set of filing source files already in the database.
    We match on the filing identifier (LEI) stored in source_file,
    not on the company name which can differ between API and DB."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT fi.source_file
            FROM filing fi
        """)).fetchall()
    # extract identifiers from source_file paths like data/raw/IOG4E947...zip
    identifiers = set()
    for (source,) in rows:
        if source:
            # the identifier is often embedded in the path or filename
            identifiers.add(str(source))
    return identifiers


def download_filing(filing: dict, out_dir: str) -> bool:
    package_url = filing["attributes"].get("package_url")
    if not package_url:
        return False
    full_url = f"https://filings.xbrl.org{package_url}"
    # build a clean filename from the URL
    slug = package_url.rstrip("/").split("/")[-1]
    out_path = Path(out_dir) / slug
    if out_path.exists():
        print(f"    Already exists: {out_path.name}")
        return True
    print(f"    Downloading {full_url} ...")
    resp = requests.get(full_url, timeout=60)
    resp.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"    Saved {out_path.name} ({len(resp.content)//1024} KB)")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", action="append", required=True,
                    help="Country code to scan (e.g. FR). Repeat for multiple.")
    ap.add_argument("--show-all", action="store_true",
                    help="Show all filers, not just ones missing from the DB")
    ap.add_argument("--download", action="store_true",
                    help="Download the most recent filing for each new company")
    ap.add_argument("--out-dir", default="data/raw",
                    help="Where to save downloaded filings (default: data/raw)")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    engine = create_engine(db_url) if db_url else None

    db_files = get_companies_in_db(engine) if engine else set()
    db_names = set()
    if engine:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM company")).fetchall()
            db_names = {r[0].lower() for r in rows}
    print(f"Companies already in database: {len(db_names)}\n")

    # group filings by entity so we show one row per company
    seen_entities = {}  # identifier -> {name, latest_filing, has_package}

    entity_name_cache = {}

    for country in args.country:
        filings = get_all_filings_for_country(country)
        print(f"\nTotal filings found for {country}: {len(filings)}")
        print(f"Fetching entity names (this may take a moment)...")

        for filing in filings:
            attrs = filing.get("attributes", {})

            # extract the real entity identifier (LEI) from the relationship link
            # e.g. "/api/entities/IOG4E947OATN0KJYSD45" -> "IOG4E947OATN0KJYSD45"
            entity_link = filing.get("relationships", {}).get("entity", {}).get("links", {}).get("related", "")
            m = re.match(r"/api/entities/([^/]+)$", entity_link)
            entity_id = m.group(1) if m else filing.get("id", "unknown")

            # get the real name from the entity endpoint
            name = get_entity_name(entity_id, entity_name_cache) if entity_id else "Unknown"

            period_end = attrs.get("period_end", "")
            has_pkg = bool(attrs.get("package_url"))

            if entity_id not in seen_entities:
                seen_entities[entity_id] = {
                    "name": name,
                    "identifier": entity_id,
                    "latest_period": period_end,
                    "has_package": has_pkg,
                    "filing": filing,
                    "country": country,
                }
            else:
                if period_end > seen_entities[entity_id]["latest_period"]:
                    seen_entities[entity_id].update({
                        "latest_period": period_end,
                        "has_package": has_pkg,
                        "filing": filing,
                    })

    # split into already-in-DB vs new
    # match on lowercased name - API names like "LVMH MOET HENNESSY LOUIS VUITTON"
    # vs DB name "LVMH" won't match, so we do a substring check
    in_db = []
    new_companies = []
    no_package = []

    for ident, info in seen_entities.items():
        if not info["has_package"]:
            no_package.append(info)
        else:
            api_name_lower = info["name"].lower()
            # check if any DB company name is a substring of the API name or vice versa
            matched = any(
                db_name in api_name_lower or api_name_lower in db_name
                for db_name in db_names
            )
            if matched:
                in_db.append(info)
            else:
                new_companies.append(info)

    # sort by period_end descending (most recent filer first)
    new_companies.sort(key=lambda x: x["latest_period"], reverse=True)

    print(f"\n{'='*70}")
    print(f"UNIVERSE SCAN RESULTS")
    print(f"{'='*70}")
    print(f"Already in your database:          {len(in_db)}")
    print(f"New - not yet in database:         {len(new_companies)}")
    print(f"No downloadable package (skipped): {len(no_package)}")

    if new_companies:
        print(f"\n--- New companies ({len(new_companies)}) ---")
        print(f"{'Name':40s} {'Latest Filing':15s} {'ID'}")
        print("-" * 80)
        for info in new_companies[:50]:
            print(f"{info['name'][:40]:40s} {info['latest_period']:15s} {info['identifier']}")
        if len(new_companies) > 50:
            print(f"... and {len(new_companies) - 50} more")

    if args.show_all and in_db:
        print(f"\n--- Already in database ({len(in_db)}) ---")
        for info in in_db:
            print(f"  {info['name']} ({info['latest_period']})")

    if args.download and new_companies:
        print(f"\nDownloading filings for {len(new_companies)} new companies...")
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for info in new_companies:
            print(f"\n  {info['name']} ({info['latest_period']})")
            if download_filing(info["filing"], args.out_dir):
                downloaded += 1
            time.sleep(1)
        print(f"\nDownloaded {downloaded}/{len(new_companies)} filings to {args.out_dir}/")

    if new_companies:
        print(f"\nTo add a specific company:")
        print(f"  python scripts/00_find_filing.py --entity <ID> --download --out data/raw/name.zip")
        print(f"  python scripts/12_prep_company.py --zip data/raw/name.zip")
        print(f"\nTo download ALL new companies at once:")
        print(f"  python scripts/14_scan_universe.py --country {' --country '.join(args.country)} --download")
