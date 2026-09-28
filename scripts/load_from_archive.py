"""
scripts/load_from_archive.py

A company's annual reports, loaded without keeping the packages. The drive this project lives on is nearly full, and
~35 companies x 5 years of packages would not fit - PLAN.md (WP7) planned "download -> parse -> discard the zip, keep
URL + SHA256" for exactly this.

For each period still missing - from filings.xbrl.org, or one package from an official URL (--url):
  1. the package is streamed into a temporary folder and checked (the archive's sha256 / the zip's own CRC);
  2. its entity and fiscal period are read from its own XBRL contexts (download_bdif.package_period) - never from
     the archive's label: filings.xbrl.org labels Unilever's reports by publication date (2026-02-12 for FY2025),
     Carrefour's and Hermes's FY2025 as 2026-12-31. A package that is not an annual report of this LEI is refused;
     a period already loaded is skipped;
  3. load_historical.py loads it, recording it as data/raw/historical/<key>_<period-end>.zip with its source URL;
  4. reconcile_reports.py compares every printed line of its primary statements with what was stored;
  5. the temporary folder is removed when the package is done - nothing is left on disk.

Companies come from data/companies.yaml (entries with `key` and `lei`, see company_registry.py).

Usage:
    python scripts/load_from_archive.py --only carrefour --dry-run      # the periods it would load
    python scripts/load_from_archive.py --only carrefour thales --min-year 2021
    python scripts/load_from_archive.py --url skf https://.../skf-2025-12-31.zip
"""
import argparse
import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
COUNTRY_PRIORITY = {"FR": 0, "NL": 1, "IT": 2, "SE": 3, "ES": 4, "GB": 5}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dh = _load("download_historical", "download_historical.py")
bdif = _load("download_bdif", "download_bdif.py")


def periods_to_load(filings: list, loaded_ends: list, min_year: int, limit=None) -> list:
    """[(period_end, filing)] newest first: the archive's trusted periods from min_year on whose own filing is not
    loaded yet (download_historical's rules - a comparative year inside a loaded filing does not count as loaded).
    The archive's period is only a label here; the package's own is checked after download."""
    by_period, _ = dh.ff.filings_by_period(filings, country_priority=COUNTRY_PRIORITY)
    eligible = [p for p in by_period if p.year >= min_year]
    return [(p, by_period[p]) for p in dh.periods_still_needed(eligible, loaded_ends, limit)]


def reconcile_summary(output: str) -> list:
    """The per-statement lines of reconcile_reports.py's output."""
    return [line.strip() for line in output.splitlines() if "lines=" in line]


def verified_period(package: Path, lei: str):
    """(period end, '') from the package's own contexts, or (None, why it is refused)."""
    try:
        return bdif.package_period(package, lei), ""
    except Exception as e:                      # not this entity, no annual period, not an ESEF report package
        return None, f"{type(e).__name__}: {e}"


def load_package(key: str, name: str, lei: str, package: Path, url: str, loaded_ends: list) -> str:
    """Verify, load and reconcile one downloaded package; a status line."""
    period, why = verified_period(package, lei)
    if period is None:
        return f"refused - {why}"
    if dh.already_loaded(period, loaded_ends):
        return f"{period} already loaded - skipped"
    canonical = package.with_name(f"{key}_{period.isoformat()}.zip")
    if canonical != package:
        shutil.move(str(package), str(canonical))
    load = subprocess.run([sys.executable, str(HERE / "load_historical.py"), "--raw-dir", str(canonical.parent),
                           "--only", key, "--record-as", "data/raw/historical", "--source-url", url],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    for line in load.stdout.splitlines():
        if "Loaded " in line or "unmapped concepts" in line or "FAILED" in line or "CURRENCY" in line:
            print("  " + line.strip())
    if load.returncode != 0:
        print(load.stdout[-1500:], load.stderr[-1500:])
        return f"{period} load failed ({load.returncode})"
    rec = subprocess.run([sys.executable, str(HERE / "reconcile_reports.py"), "--zip", str(canonical)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    lines = reconcile_summary(rec.stdout)
    print("\n".join("  " + l for l in lines) or "  (reconcile located no primary statement)")
    loaded_ends.append(period)
    ok = lines and all("DIFFERENT= 0" in l for l in lines)
    return f"{period} loaded, reconcile " + ("OK" if ok else "CHECK")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--only", nargs="+", help="registry keys (data/companies.yaml `key`)")
    ap.add_argument("--url", nargs=2, metavar=("KEY", "URL"), help="load one package from an official URL")
    ap.add_argument("--min-year", type=int, default=2021, help="oldest fiscal year end to load")
    ap.add_argument("--limit", type=int, default=None, help="at most this many periods per company")
    ap.add_argument("--min-free-mb", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true", help="list the periods, fetch nothing")
    args = ap.parse_args()
    if not args.only and not args.url:
        ap.error("--only or --url")

    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    load_dotenv(ROOT / ".env")
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (see .env).")
        sys.exit(1)
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

    keys = [args.url[0]] if args.url else args.only
    unknown = [k for k in keys if k not in dh.COMPANIES]
    if unknown:
        print(f"Not in data/companies.yaml (entries with key + lei): {unknown}")
        sys.exit(2)

    results = []
    if args.url:
        key, url = args.url
        lei, name = dh.COMPANIES[key]
        print(f"{name} ({key}) from {url}")
        with tempfile.TemporaryDirectory(prefix="ifrs_pkg_") as tmp:
            out = Path(tmp) / f"{key}_download.zip"
            if bdif.download(url, out, args.min_free_mb, dh.has_room) is None:
                results.append((name, "-", "download refused"))
            else:
                results.append((name, "-", load_package(key, name, lei, out, url, dh.loaded_period_ends(engine, name))))
    for key in ([] if args.url else args.only):
        lei, name = dh.COMPANIES[key]
        print(f"\n{'=' * 70}\n{name} ({key}, {lei})\n{'=' * 70}")
        try:
            filings = dh.get_filings(lei)
        except Exception as e:
            print(f"  archive not reachable: {type(e).__name__}: {e}")
            results.append((name, "-", "archive error"))
            continue
        loaded_ends = dh.loaded_period_ends(engine, name)
        todo = periods_to_load(filings, loaded_ends, args.min_year, args.limit)
        print(f"  {len(filings)} archive filing(s); by the archive's labels, to load: "
              f"{[p.isoformat() for p, _ in todo] or 'nothing'}")
        for label, filing in todo:
            if args.dry_run:
                results.append((name, label.isoformat(), "dry run"))
                continue
            url = "https://filings.xbrl.org" + (filing["attributes"].get("package_url") or "")
            with tempfile.TemporaryDirectory(prefix="ifrs_pkg_") as tmp:
                out = Path(tmp) / f"{key}_{label.isoformat()}.zip"
                if not dh.download_filing(filing, out, args.min_free_mb):
                    results.append((name, label.isoformat(), "download refused"))
                    continue
                results.append((name, label.isoformat(), load_package(key, name, lei, out, url, loaded_ends)))
    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for name, label, status in results:
        print(f"  {name:28s} {label:12s} {status}")


if __name__ == "__main__":
    main()
