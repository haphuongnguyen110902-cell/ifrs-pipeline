"""
Download the historical filings of every company in COMPANIES below.
Run to get the multi-year series forecasting needs.

Usage:
    python scripts/download_historical.py
    python scripts/download_historical.py --dry-run
    python scripts/download_historical.py --only heineken --skip-loaded --limit 5

--skip-loaded  leave out periods whose own filing is already in the database
               (needs DATABASE_URL) - re-fetching a 60 MB package for a year that
               is already loaded wastes disk and time
--limit N      at most the N most recent still-needed filings per company
--min-free-mb  refuse a download that would leave less than this much disk free

Every package is streamed to a .part file, checked against the sha256 the archive
publishes for it, and only then renamed - an interrupted or corrupted download can
no longer be left behind as a file that later runs skip as "already exists".
"""
import argparse
import datetime
import hashlib
import importlib.util
import re
import shutil
import sys
import time
from pathlib import Path
import requests

API_BASE = "https://filings.xbrl.org/api"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# which filing / which period is trustworthy lives in one place
ff = _load("find_filing_00", "00_find_filing.py")

# Companies with their entity identifiers (LEIs). Keys are the filename prefix
# load_historical.py reads back (letters only) - keep the two registries in sync;
# tests/test_historical_registry.py fails if they drift apart.
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
    # Loaded from the universe after the first eleven (depth pass). Each LEI was
    # cross-checked: ASM/Adyen/Heineken/Recordati = universe entity id AND the
    # GLEIF/OpenFIGI resolver's answer; Schneider = its own package filename AND the
    # archive's entity record ("SCHNEIDER ELECTRIC SE").
    "heineken":         ("724500K5PTPSST86UQ23",  "Heineken"),
    "schneider":        ("969500A1YF1XUYYXS284",  "Schneider Electric"),
    "adyen":            ("724500973ODKK3IFQ447",  "Adyen"),
    "asm":              ("7245001I22ND6ZFHX623",  "ASM International"),
    "recordati":        ("815600FBF92FD3531704",  "Recordati"),
}

# Minimum period_end to download (don't go further back than this)
MIN_YEAR = "2018-01-01"

DEFAULT_MIN_FREE_MB = 500


# ---------------------------------------------------------------- pure helpers

def has_room(free_bytes: int, needed_bytes: int, reserve_bytes: int) -> bool:
    """True when taking `needed_bytes` still leaves `reserve_bytes` free. The drive
    this project lives on was found 100% full (1.2 GB free) while ~700 MB of packages
    were about to be fetched; a full disk breaks far more than a download."""
    return free_bytes - needed_bytes >= reserve_bytes


def already_loaded(period: datetime.date, loaded_ends, tolerance_days: int = 3) -> bool:
    """Is `period` (a filing's period end) the own period of an already-loaded filing?"""
    return any(abs((period - e).days) <= tolerance_days for e in loaded_ends)


def periods_still_needed(periods, loaded_ends, limit=None) -> list:
    """Newest first, minus periods already loaded, at most `limit` of them."""
    need = [p for p in sorted(periods, reverse=True) if not already_loaded(p, loaded_ends)]
    return need[:limit] if limit else need


def loaded_period_ends(engine, company_name: str) -> list:
    """The reporting period end of each filing already loaded for a company: the latest
    ANNUAL duration end among that filing's own periods, minus the one day Arelle adds
    to every end date. Comparatives inside a filing are deliberately not counted - a
    FY2025 filing carries FY2024 figures, but the FY2024 filing itself is still worth
    having (it holds the year as originally reported)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT MAX(p.end_date) FROM filing f
            JOIN company c ON c.company_id = f.company_id
            JOIN period p ON p.filing_id = f.filing_id
            WHERE c.name = :n AND p.start_date IS NOT NULL
              AND p.end_date - p.start_date BETWEEN 300 AND 400
            GROUP BY f.filing_id"""), {"n": company_name}).fetchall()
    return [r[0] - datetime.timedelta(days=1) for r in rows if r[0] is not None]


def get_filings(identifier: str) -> list:
    resp = requests.get(
        f"{API_BASE}/entities/{identifier}/filings",
        params={"sort": "-period_end"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def download_filing(filing: dict, out_path: Path, min_free_mb: int = DEFAULT_MIN_FREE_MB) -> bool:
    """Stream one package to `<name>.part`, verify it against the archive's sha256, then
    rename. Returns False (and leaves nothing behind) if there is not enough disk, the
    download fails, or the checksum does not match."""
    if out_path.exists():
        print(f"    Already exists: {out_path.name} — skipping")
        return True
    attrs = filing["attributes"]
    package_url = attrs.get("package_url")
    if not package_url:
        print(f"    No package URL — skipping")
        return False
    full_url = f"https://filings.xbrl.org{package_url}"
    expected = (attrs.get("sha256") or "").lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    print(f"    Downloading {full_url}")
    digest = hashlib.sha256()
    try:
        with requests.get(full_url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            size = int(resp.headers.get("Content-Length") or 0)
            free = shutil.disk_usage(out_path.parent).free
            if not has_room(free, size, min_free_mb * 1024 * 1024):
                print(f"    NOT downloaded: {size // 2**20} MB would leave {(free - size) // 2**20} MB free "
                      f"(reserve {min_free_mb} MB) - free some disk space and re-run")
                return False
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
    except (requests.RequestException, OSError) as e:
        part.unlink(missing_ok=True)
        print(f"    FAILED, nothing kept: {type(e).__name__}: {e}")
        return False
    if expected and digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        print(f"    REJECTED: sha256 {digest.hexdigest()[:12]}... does not match the archive's {expected[:12]}...")
        return False
    part.replace(out_path)
    print(f"    Saved {out_path.name} ({out_path.stat().st_size // 1024} KB"
          f"{', sha256 verified' if expected else ', archive gave no sha256 to verify'})")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be downloaded without downloading")
    ap.add_argument("--out-dir", default="data/raw/historical",
                    help="Output directory (default: data/raw/historical)")
    ap.add_argument("--only", nargs="+",
                    help="Only download for these company slugs (e.g. loreal lvmh)")
    ap.add_argument("--skip-loaded", action="store_true",
                    help="Skip periods whose own filing is already in the database (needs DATABASE_URL)")
    ap.add_argument("--limit", type=int, default=None,
                    help="At most this many (most recent still-needed) filings per company")
    ap.add_argument("--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB,
                    help=f"Refuse a download that would leave less free disk than this (default {DEFAULT_MIN_FREE_MB})")
    args = ap.parse_args()

    unknown = [s for s in (args.only or []) if s not in COMPANIES]
    if unknown:
        print(f"Unknown company slug(s): {unknown}. Known: {', '.join(sorted(COMPANIES))}")
        sys.exit(2)

    engine = None
    if args.skip_loaded:
        import os
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        load_dotenv()
        if not os.environ.get("DATABASE_URL"):
            print("--skip-loaded needs DATABASE_URL (see .env)")
            sys.exit(1)
        engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

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

        # ONE filing per TRUSTED period (the archive's own period_end can be
        # wrong - e.g. Recordati FY2022 listed as 2032-12-31 - and this
        # period ends up in the filename load_historical.py reads back as the
        # fiscal year). Where several packages share a period: the primary-
        # listing country first (FR > NL > IT > SE > ES > GB), then the best
        # archive validation record. A filing whose real period can't be
        # established is skipped and reported, never saved under a false label.
        COUNTRY_PRIORITY = {"FR": 0, "NL": 1, "IT": 2, "SE": 3, "ES": 4, "GB": 5}
        by_period, skipped = ff.filings_by_period(filings, country_priority=COUNTRY_PRIORITY)
        for _, note in skipped:
            print(f"  *** skipped a filing: {note}")
        loaded_ends = loaded_period_ends(engine, name) if engine is not None else []
        if loaded_ends:
            print(f"  already loaded (each filing's own period end): {sorted(d.isoformat() for d in loaded_ends)}")
        eligible_periods = periods_still_needed(
            [p for p in by_period if p.isoformat() >= MIN_YEAR], loaded_ends, args.limit)
        eligible = [(p.isoformat(), by_period[p]) for p in eligible_periods]

        print(f"  Found {len(eligible)} eligible filings (from {MIN_YEAR})")

        downloaded = 0
        for period, filing in eligible:
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

            if download_filing(filing, out_path, args.min_free_mb):
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
