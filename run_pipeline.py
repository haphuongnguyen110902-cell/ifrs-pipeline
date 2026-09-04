"""
run_pipeline.py — Master orchestrator for the IFRS/XBRL pipeline.

This is the single entry point for the full pipeline. Every step is
idempotent: running it twice produces the same result.

Designed so that V4 automation (GitHub Actions cron) only needs to call:
    python run_pipeline.py --mode full

Modes:
    full        Run everything end to end
    discover    Scan for new companies in a country, report only
    load        Parse + load all companies in companies.yaml
    validate    Run all validation checks
    ratios      Recompute ratios only (fast, no re-parsing)
    report      Generate statements and Excel outputs only

Usage:
    python run_pipeline.py --mode full
    python run_pipeline.py --mode full --country FR --country IT
    python run_pipeline.py --mode load --only danone.zip essity.zip
    python run_pipeline.py --mode ratios
    python run_pipeline.py --mode validate
    python run_pipeline.py --mode discover --country FR

Exit codes:
    0   Success
    1   Validation failed (check logs)
    2   Unmapped concepts found (human review required)
    3   Environment error (.env missing, DB unreachable)
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------- helpers

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "  ", "OK": "✓ ", "WARN": "⚠ ", "ERROR": "✗ ", "STEP": "\n→ "}
    print(f"[{ts}] {prefix.get(level, '')}{msg}")


def run(cmd: list, description: str, fatal: bool = True) -> int:
    """Run a subprocess command, stream output, return exit code."""
    log(description, "STEP")
    log(" ".join(cmd))
    result = subprocess.run(cmd, cwd=Path(__file__).parent)
    if result.returncode != 0:
        log(f"FAILED (exit {result.returncode}): {description}", "ERROR")
        if fatal:
            sys.exit(result.returncode)
    else:
        log(f"Done: {description}", "OK")
    return result.returncode


def check_env() -> bool:
    """Verify the environment is ready before doing anything."""
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log("DATABASE_URL not found in .env", "ERROR")
        return False
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log("Database connection OK", "OK")
        return True
    except Exception as e:
        log(f"Database unreachable: {e}", "ERROR")
        return False


def get_loaded_companies() -> list:
    """Return list of company names currently in the database."""
    load_dotenv()
    try:
        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM company ORDER BY name")).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------- pipeline steps

def step_discover(countries: list):
    """Scan for new companies not yet in the database."""
    for country in countries:
        run(
            [sys.executable, "scripts/14_scan_universe.py", "--country", country],
            f"Discover new companies in {country}",
            fatal=False,
        )


def step_load(only: list = None, reset: bool = True):
    """Parse and load all companies (or a subset)."""
    cmd = [sys.executable, "scripts/09_batch_load.py"]
    if reset:
        cmd.append("--reset-facts")
    if only:
        for stem in only:
            cmd.extend(["--only", stem])
    run(cmd, "Load companies into database")


def step_validate(fatal: bool = True) -> bool:
    """Run all validation checks. Returns True if all pass."""
    log("Running validation", "STEP")
    result = subprocess.run(
        [sys.executable, "scripts/08_validate.py"],
        cwd=Path(__file__).parent,
        capture_output=False,
    )
    if result.returncode != 0:
        log("Validation FAILED", "ERROR")
        if fatal:
            sys.exit(1)
        return False
    log("All validation checks passed", "OK")
    return True


def step_ratios():
    """Recompute all ratios and update DB + Excel."""
    run(
        [sys.executable, "scripts/11_ratio_engine.py"],
        "Compute ratios → DB + Excel",
    )


def step_statements(companies: list = None):
    """Generate statements for all (or specified) companies."""
    if not companies:
        companies = get_loaded_companies()
    for company in companies:
        run(
            [sys.executable, "scripts/07_generate_statements.py", "--company", company],
            f"Generate statements: {company}",
            fatal=False,  # one company failing shouldn't stop the rest
        )


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="IFRS pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--mode", choices=["full", "discover", "load", "validate", "ratios", "report"],
                    default="full", help="Which part of the pipeline to run")
    ap.add_argument("--country", action="append", default=["FR"],
                    help="Country code(s) for discovery (default: FR)")
    ap.add_argument("--only", nargs="+",
                    help="Only process these zip file stems (for load mode)")
    ap.add_argument("--no-reset", action="store_true",
                    help="Don't clear existing facts before loading (faster but may miss updates)")
    ap.add_argument("--skip-validate", action="store_true",
                    help="Skip validation checks (not recommended)")
    ap.add_argument("--skip-statements", action="store_true",
                    help="Skip statement generation (saves time if only ratios needed)")
    args = ap.parse_args()

    log("=" * 60)
    log(f"IFRS Pipeline — mode={args.mode}")
    log(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    # always check environment first
    if not check_env():
        log("Environment check failed. Fix .env and retry.", "ERROR")
        sys.exit(3)

    # ---- run the requested mode

    if args.mode == "discover":
        step_discover(args.country)

    elif args.mode == "load":
        step_load(only=args.only, reset=not args.no_reset)
        if not args.skip_validate:
            step_validate()

    elif args.mode == "validate":
        ok = step_validate(fatal=False)
        sys.exit(0 if ok else 1)

    elif args.mode == "ratios":
        step_ratios()

    elif args.mode == "report":
        step_ratios()
        if not args.skip_statements:
            step_statements()

    elif args.mode == "full":
        # Full pipeline: discover → load → validate → ratios → statements
        log("Running full pipeline", "STEP")

        # 1. load all companies
        step_load(only=args.only, reset=not args.no_reset)

        # 2. validate - fatal by default
        if not args.skip_validate:
            step_validate(fatal=True)

        # 3. compute ratios
        step_ratios()

        # 4. generate statements (optional skip for speed)
        if not args.skip_statements:
            step_statements()

        log("=" * 60, )
        log("Full pipeline complete", "OK")
        log(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 60)


if __name__ == "__main__":
    main()
