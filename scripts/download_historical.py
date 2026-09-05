"""
Download all historical filings for all 11 companies.
Run once to get full time series data for forecasting.

Usage:
    python scripts/download_historical.py
    python scripts/download_historical.py --dry-run
"""
import argparse
import re
import time
from pathlib import Path
import requests

API_BASE = "https://filings.xbrl.org/api"

# All 11 companies with their entity identifiers
COMPANIES = {
    "loreal":           ("529900JI1GG6F7RKVI53",  "L'Oreal"),
    "lvmh":             ("IOG4E947OATN0KJYSD45",  "LVMH"),
    "kering":           ("549300VGEJKB7SVUZR78",  "Kering"),
    "essilorluxottica": ("549300M3VH1A3ER1TB49",  "EssilorLuxottica"),
    "danone":           ("969500KMUQ2B6CBAF162",  "Danone"),
    "essity":           ("549300G8E6YUVJ1DA153",  "Essity"),
    "moncler":          ("815600EBD7FB00525B20",  "Moncler"),
    "shell":            ("21380068P1DRHMJ8KU70",  "Shell"),
    "amplifon":         ("ZYXJDNVM2JI3VBM8G556",  "Amplifon"),
    "pernod_ricard":    ("52990097YFPX9J0H5D87",  "Pernod Ricard"),
    "puig":             ("549300OVHNSX30L1AQ94",  "Puig Brands"),
}

# Minimum period_end to download (don't go further back than this)
MIN_YEAR = "2018-01-01"


def get_filings(identifier: str) -> list:
    resp = requests.get(
        f"{API_BASE}/entities/{identifier}/filings",
        params={"sort": "-period_end"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def download_filing(filing: dict, out_path: Path) -> bool:
    if out_path.exists():
        print(f"    Already exists: {out_path.name} — skipping")
        return True
    package_url = filing["attributes"].get("package_url")
    if not package_url:
        print(f"    No package URL — skipping")
        return False
    full_url = f"https://filings.xbrl.org{package_url}"
    print(f"    Downloading {full_url}")
    resp = requests.get(full_url, timeout=60)
    resp.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    print(f"    Saved {out_path.name} ({len(resp.content)//1024} KB)")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be downloaded without downloading")
    ap.add_argument("--out-dir", default="data/raw/historical",
                    help="Output directory (default: data/raw/historical)")
    ap.add_argument("--only", nargs="+",
                    help="Only download for these company slugs (e.g. loreal lvmh)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    companies = {k: v for k, v in COMPANIES.items()
                 if not args.only or k in args.only}

    total_downloaded = 0
    total_skipped = 0
    summary = []

    for slug, (identifier, name) in companies.items():
        print(f"\n{'='*60}")
        print(f"{name} ({identifier})")
        print(f"{'='*60}")

        try:
            filings = get_filings(identifier)
        except Exception as e:
            print(f"  Failed to fetch filings: {e}")
            summary.append((name, "fetch failed", 0))
            continue

        # filter to filings with packages and not too old
        # deduplicate by period_end - keep one per period
        # priority: FR > NL > IT > SE > ES > GB (primary listing preference)
        COUNTRY_PRIORITY = {"FR": 0, "NL": 1, "IT": 2, "SE": 3, "ES": 4, "GB": 5}
        seen_periods = {}
        for f in filings:
            attrs = f["attributes"]
            if not attrs.get("package_url"):
                continue
            period = attrs.get("period_end", "")
            if period < MIN_YEAR:
                continue
            country = attrs.get("country", "ZZ")
            priority = COUNTRY_PRIORITY.get(country, 99)
            if period not in seen_periods or priority < seen_periods[period][1]:
                seen_periods[period] = (f, priority)

        eligible = [v[0] for v in sorted(seen_periods.values(), key=lambda x: x[0]["attributes"]["period_end"], reverse=True)]

        print(f"  Found {len(eligible)} eligible filings (from {MIN_YEAR})")

        downloaded = 0
        for filing in eligible:
            period = filing["attributes"]["period_end"]
            country = filing["attributes"].get("country", "XX")
            filename = f"{slug}_{period}.zip"
            out_path = out_dir / filename

            print(f"  {period} [{country}]")

            if args.dry_run:
                if out_path.exists():
                    print(f"    Would skip (exists): {filename}")
                else:
                    print(f"    Would download: {filename}")
                continue

            if download_filing(filing, out_path):
                downloaded += 1
            time.sleep(0.5)

        summary.append((name, "OK", downloaded))
        total_downloaded += downloaded
        time.sleep(1)

    print(f"\n\n{'='*60}")
    print(f"SUMMARY {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'='*60}")
    for name, status, n in summary:
        print(f"  {name:25s} {status:12s} {n} files")
    if not args.dry_run:
        print(f"\n  Total downloaded: {total_downloaded}")
        print(f"\nNext step:")
        print(f"  python scripts/13_batch_prep.py --raw-dir {out_dir}")
        print(f"  python scripts/12_apply_review.py")
        print(f"  python scripts/09_batch_load.py --raw-dir {out_dir} --reset-facts")
