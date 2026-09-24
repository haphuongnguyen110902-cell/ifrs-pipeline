"""
scripts/35_trace_figures.py

EVERY FIGURE THE DASHBOARD SHOWS, TRACED TO THE COMPANY'S OWN REPORT
---------------------------------------------------------------------
reconcile_reports.py checks each report's own year, line by line. The dashboard, however, shows a year through
whichever stored fact the ratio engine picked for it: a later report's comparative column (restated figures), the
year's own report (figures as first reported - AS_REPORTED_COLUMNS), a reviewed note figure. This tool follows
every input the engine actually reads - in both bases, for every company and year, plus the capex and dividend
lines of the 3-statement model - back to the fact that supplied it (filing, raw tag, period) and looks that fact
up in that filing's own package, in the column it was printed in:

    PRINTED     equal to the figure printed on a primary statement (balance sheet, income statement, cash flow,
                changes in equity)
    NOTE        equal to a figure the company tagged in its notes - its own figure, not a primary-statement line
    REVIEWED    a reviewed note figure (33_load_note_facts.py), checked against the report when it was loaded
    DIFFERENT   the package prints another number for this tag and period
    NOT_FOUND   the package has no non-dimensional fact with this tag and period
    NO_PACKAGE  the package is neither on disk nor retrievable from filings.xbrl.org

A package that is no longer on disk is fetched from filings.xbrl.org INTO MEMORY (the same package choice as
download_historical.py, checked against the archive's sha256) and never written to disk. Read-only.

USAGE
-----
    python scripts/35_trace_figures.py                         # every company
    python scripts/35_trace_figures.py --company Essity
    python scripts/35_trace_figures.py --csv trace.csv         # one row per traced input, with the company's label
    python scripts/35_trace_figures.py --no-fetch              # never go online: missing packages are NO_PACKAGE
"""
import argparse
import datetime
import hashlib
import importlib.util
import io
import os
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r11 = _load("ratio_engine_11", "11_ratio_engine.py")
rr = _load("reconcile_reports", "reconcile_reports.py")

STATUSES = ["PRINTED", "NOTE", "REVIEWED", "DIFFERENT", "NOT_FOUND", "NO_PACKAGE"]
# The statement of changes in equity is a primary statement too (IAS 1.10(c)) - dividends distributed to owners are
# printed there. reconcile_reports.py compares only the other three, so the pattern lives here.
EQUITY_STATEMENT = re.compile(r"changes ?in ?equity|variations? ?des ?capitaux ?propres|patrimonio ?netto|"
                              r"forandringar ?i ?eget ?kapital|cambios ?en ?el ?patrimonio|eigen ?vermogen", re.I)
PACKAGE_NAME = re.compile(r"^([a-z_]+)_(\d{4}-\d{2}-\d{2})\.zip$")


# ---------------------------------------------------------------- which stored facts the engine reads

def used_inputs(wide: pd.DataFrame, extra_concepts=()) -> set:
    """{(company_id, year, concept)} for every concept the ratio engine read a value from, row by row.

    The engine reads its inputs through get_col / get_best, except financial debt, which compute_financial_debt
    reads row by row and names in its `basis`. Wrapping the two readers records what each row drew on: get_best
    the first name that has a value in that row (the one that won), get_col every non-empty value it returned;
    the debt lines come from the basis. `extra_concepts` are read the way get_best reads them (the 3-statement
    model's capex and dividend lines). tests/test_trace_figures.py checks, by removing one input at a time, that
    nothing the ratios depend on is left out."""
    seen = set()
    ids, years = wide["company_id"].tolist(), wide["year"].tolist()

    def note(pos, name):
        seen.add((int(ids[pos]), int(years[pos]), name))

    orig_col, orig_best = r11.get_col, r11.get_best

    def get_col(w, name):
        s = orig_col(w, name)
        if w is wide:
            for pos, v in enumerate(s.tolist()):
                if pd.notna(v):
                    note(pos, name)
        return s

    def get_best(w, *names):
        res = orig_best(w, *names)
        if w is wide:
            cols = [(n, w[n].tolist()) for n in names if n in w.columns]
            for pos in range(len(w)):
                for n, vals in cols:
                    if pd.notna(vals[pos]):
                        note(pos, n)
                        break
        return res

    r11.get_col, r11.get_best = get_col, get_best
    try:
        r11._compute_ratios(wide)
        for group in extra_concepts:
            get_best(wide, *group)
    finally:
        r11.get_col, r11.get_best = orig_col, orig_best
    for pos, basis in enumerate(r11.compute_financial_debt(wide)["basis"].tolist()):
        for name in filter(None, (basis or "").split("+")):
            note(pos, name)
    return seen


def fetch_traceable_facts(engine, company=None) -> pd.DataFrame:
    """fetch_facts' rows plus what a trace needs: value_id, the raw tag and the package the fact came from."""
    from sqlalchemy import text
    where = "WHERE c.name = :company" if company else ""
    df = pd.read_sql(text(f"""
        SELECT c.name AS company, c.company_id, fv.filing_id, fv.value_id, fv.raw_xbrl_tag, fi.source_file,
               ic.normalized_name, p.period_type, p.start_date, p.end_date, fv.value, fv.currency
        FROM fact_value fv JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p ON fv.period_id = p.period_id JOIN filing fi ON fv.filing_id = fi.filing_id
        JOIN company c ON fi.company_id = c.company_id {where}"""), engine,
        params={"company": company} if company else {})
    if df.empty:
        return df
    df["year"] = [r11.fiscal_year_label(pt, s, e) for pt, s, e in zip(df["period_type"], df["start_date"], df["end_date"])]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["year"]).astype({"year": int})


def chosen_facts(facts: pd.DataFrame, used: set, prefer_own_filing: bool) -> pd.DataFrame:
    """The stored fact behind each used (company_id, year, concept) under one basis."""
    resolved, _ = r11.resolve_fact_conflicts(facts, prefer_own_filing)
    keys = pd.DataFrame(sorted(used), columns=["company_id", "year", "normalized_name"])
    return resolved.merge(keys, on=["company_id", "year", "normalized_name"])


# ---------------------------------------------------------------- the report the fact came from

def printed_key(tag: str, period_type: str, start, end):
    """The (tag, period) a stored fact has in its report. Arelle stores every end date one day late; a start date
    is stored as printed."""
    day = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    if period_type == "instant":
        return tag, None, day
    return tag, pd.Timestamp(start).date().isoformat(), day


def index_package(pkg: dict) -> tuple:
    """({(tag, start, end): {values printed}}, {tags on a primary statement}) for one read package."""
    printed = {}
    for f in pkg["facts"]:
        key = (f["tag"], f["start"], f["instant"] or f["end"])
        printed.setdefault(key, set()).add(f["value"])
    defs, trees = rr.role_definitions(pkg["xsd"] + pkg["pre"]), rr.read_presentation(pkg["pre"])
    primary = set()
    for kind in rr.KINDS:
        primary.update(rr.statement_lines(trees, defs, kind))
    for role, concepts in trees.items():
        if EQUITY_STATEMENT.search(rr._plain(f"{defs.get(role, '')} {role}")) and                 not rr.NOT_PRIMARY.search(rr._plain(defs.get(role, ""))):
            primary.update(concepts)
    return printed, primary


def trace_status(fact: dict, printed: dict, primary: set, tolerance: float = 1.0) -> tuple:
    """(status, the value(s) printed for this tag and period)."""
    tag = fact["raw_xbrl_tag"] or ""
    if tag.startswith("note:"):
        return "REVIEWED", None
    key = printed_key(tag, fact["period_type"], fact["start_date"], fact["end_date"])
    values = printed.get(key)
    if not values:
        return "NOT_FOUND", None
    value = fact["value"]
    if any(abs(v - value) <= max(tolerance, 1e-9 * abs(value)) for v in values):
        return ("PRINTED" if tag in primary else "NOTE"), value
    return "DIFFERENT", sorted(values)


def archive_package(source_file: str, fetch_filings):
    """(bytes of the package a historical source file was loaded from, sha256-checked) from filings.xbrl.org, or
    None. `fetch_filings(slug, period_end)` returns (package_url, sha256) or None - injectable for tests."""
    m = PACKAGE_NAME.match(os.path.basename(source_file.replace("\\", "/")))
    if not m:
        return None
    found = fetch_filings(m.group(1), datetime.date.fromisoformat(m.group(2)))
    if not found:
        return None
    url, expected = found
    import requests
    resp = requests.get(f"https://filings.xbrl.org{url}", timeout=120)
    resp.raise_for_status()
    data = resp.content
    if expected and hashlib.sha256(data).hexdigest() != expected.lower():
        return None
    return data


def xbrl_org_lookup():
    """fetch_filings for archive_package: the same package choice as download_historical.py."""
    dh = _load("download_historical", "download_historical.py")
    cache = {}

    def fetch(slug, period_end):
        if slug not in dh.COMPANIES:
            return None
        if slug not in cache:
            by_period, _ = dh.ff.filings_by_period(
                dh.get_filings(dh.COMPANIES[slug][0]),
                country_priority={"FR": 0, "NL": 1, "IT": 2, "SE": 3, "ES": 4, "GB": 5})
            cache[slug] = by_period
        filing = cache[slug].get(period_end)
        if filing is None:
            return None
        attrs = filing["attributes"]
        return attrs.get("package_url"), attrs.get("sha256")
    return fetch


# ---------------------------------------------------------------- the run

def trace(engine, company=None, fetch=True, extra_concepts=()) -> pd.DataFrame:
    facts = fetch_traceable_facts(engine, company)
    if facts.empty:
        return pd.DataFrame()
    rows = []
    for basis, own in (("restated", False), ("as reported", True)):
        wide = r11.pivot_to_wide(facts.drop(columns=["value_id", "raw_xbrl_tag", "source_file"]), prefer_own_filing=own)
        chosen = chosen_facts(facts, used_inputs(wide, extra_concepts), own)
        rows.append(chosen.assign(basis=basis))
    chosen = pd.concat(rows, ignore_index=True)
    # one fact can feed both bases: trace it once, remember both
    bases = chosen.groupby("value_id")["basis"].agg(lambda b: " + ".join(sorted(set(b))))
    chosen = chosen.drop_duplicates("value_id").drop(columns="basis").merge(bases.rename("basis"), on="value_id")

    lookup = xbrl_org_lookup() if fetch else None
    out = []
    for source, group in chosen.groupby("source_file"):
        path = ROOT / str(source).replace("\\", "/")
        pkg, where = None, ""
        if path.exists():
            pkg, where = rr.read_package(str(path)), "disk"
        elif lookup is not None:
            try:
                data = archive_package(str(source), lookup)
            except Exception as e:                       # a network failure is NO_PACKAGE, never a crash
                print(f"  could not fetch {os.path.basename(str(source))}: {type(e).__name__}: {e}")
                data = None
            if data is not None:
                pkg, where = rr.read_package(io.BytesIO(data)), "filings.xbrl.org"
        printed, primary = index_package(pkg) if pkg else ({}, set())
        labels = rr.read_labels(pkg["lab"]) if pkg else {}
        for fact in group.to_dict("records"):
            status, printed_value = trace_status(fact, printed, primary) if pkg else ("NO_PACKAGE", None)
            where_ = "reviewed note spec" if status == "REVIEWED" else where
            out.append({"company": fact["company"], "year": fact["year"], "concept": fact["normalized_name"],
                        "basis": fact["basis"], "status": status, "value": fact["value"], "printed": printed_value,
                        "tag": fact["raw_xbrl_tag"], "label": labels.get(fact["raw_xbrl_tag"], ""),
                        "package": os.path.basename(str(source).replace("\\", "/")), "checked_in": where_,
                        "value_id": fact["value_id"]})
    return pd.DataFrame(out).sort_values(["company", "year", "concept"]).reset_index(drop=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--company")
    ap.add_argument("--csv", help="write every traced input to this CSV")
    ap.add_argument("--no-fetch", action="store_true", help="never fetch a missing package from filings.xbrl.org")
    args = ap.parse_args()

    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    load_dotenv(ROOT / ".env")
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (see .env) - nothing to trace.")
        sys.exit(1)
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    tsm = _load("three_statement_21", "21_three_statement_model.py")
    # the 3-statement model's own inputs (a capex override always names one of these concepts)
    extra = [tsm.CAPEX_COMBINED, *[(c,) for c in tsm.CAPEX_COMPONENTS], tsm.DIVIDEND_CONCEPTS]

    df = trace(engine, args.company, fetch=not args.no_fetch, extra_concepts=extra)
    if df.empty:
        print("No facts to trace.")
        return
    counts = df.pivot_table(index="company", columns="status", values="value_id", aggfunc="count", fill_value=0)
    counts = counts.reindex(columns=[s for s in STATUSES if s in counts.columns])
    counts["inputs"] = counts.sum(axis=1)
    print("Inputs the dashboard's figures are computed from, by company and status:\n")
    print(counts.to_string())
    bad = df[df["status"].isin(["DIFFERENT", "NOT_FOUND", "NO_PACKAGE"])]
    if not bad.empty:
        print(f"\n*** {len(bad)} input(s) not matched to a printed figure:")
        print(bad[["company", "year", "concept", "basis", "status", "value", "printed", "tag", "package"]]
              .to_string(index=False, max_colwidth=70))
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nEvery traced input written to {args.csv}")


if __name__ == "__main__":
    main()
