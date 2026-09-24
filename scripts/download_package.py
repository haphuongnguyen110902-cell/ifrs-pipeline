"""
scripts/download_package.py

One ESEF report package from an official URL that has no API - a company's own investor-relations site (Essity
publishes its ESEF zips on essity.se) or a national storage mechanism - with the same checks as download_bdif.py:
every announced byte arrives and every member passes the zip's CRC check; the report's own XBRL must name the
company's LEI (from download_historical.py's registry); the period is read from the facts, never from the URL; a
period already loaded is not kept. Saved as data/raw/historical/<key>_<period end>.zip for load_historical.py.

    python scripts/download_package.py essity https://assets.www.essity.com/essity/essi-2025-12-31-1-sv.zip
"""
import argparse
import importlib.util
import os
import sys
import zipfile
from pathlib import Path


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    dh = _load("download_historical", "download_historical.py")
    bd = _load("download_bdif", "download_bdif.py")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("key", choices=sorted(dh.COMPANIES))
    ap.add_argument("url")
    ap.add_argument("--out-dir", default="data/raw/historical")
    ap.add_argument("--min-free-mb", type=int, default=300)
    args = ap.parse_args(argv)
    lei, name = dh.COMPANIES[args.key]
    out_dir = Path(args.out_dir)
    tmp = out_dir / f"{args.key}_download.zip"
    sha = bd.download(args.url, tmp, args.min_free_mb, dh.has_room)
    if sha is None:
        return 1
    try:
        period = bd.package_period(tmp, lei)
    except (ValueError, zipfile.BadZipFile) as e:
        tmp.unlink()
        print(f"REJECTED: {e}")
        return 1
    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    load_dotenv()
    if dh.already_loaded(period, dh.loaded_period_ends(create_engine(os.environ["DATABASE_URL"]), name)):
        tmp.unlink()
        print(f"{name}: period {period} is already loaded - file not kept")
        return 0
    final = out_dir / f"{args.key}_{period.isoformat()}.zip"
    if final.exists():
        tmp.unlink()
        print(f"{final.name} already on disk - kept the existing file")
        return 0
    tmp.replace(final)
    print(f"saved {final.name} ({final.stat().st_size // 1024} KB, all members CRC-checked, LEI {lei} and period "
          f"{period} read from the report's facts, sha256 {sha[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
