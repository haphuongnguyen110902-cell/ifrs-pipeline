"""
Load reviewed figures that a company prints in the NOTES of its annual report but does not tag in XBRL.

Why: an ESEF report tags the primary statements in detail, but most notes are only block-tagged, so a figure such as
"depreciation and amortisation" can be printed clearly in a note table (or in the company's own EBITDA reconciliation)
and still be absent from the facts. Found in the D&A audit: LVMH, L'Oreal and Essity (2023-24) had no D&A fact at all, so
their EBITDA was EBIT and the dashboard showed n/a*.

How it stays deterministic and inspectable: data/mappings/reviewed_note_figures.yaml is a REVIEWED specification - which
report, which table row (a regular expression on the row label), how the fiscal years are identified, which concept the
figure is, the perimeter and the evidence. This script reads the figure from the report's own table (nothing is typed by
hand), REFUSES it unless its checks pass (a cross-check against a tagged fact or against the company's own EBITDA bridge,
and a plausibility range against revenue), and stores it in fact_value with raw_xbrl_tag = "note:<id>" and
context_ref = "<report>#row<n>", so a note-sourced fact can always be told from an XBRL fact and traced to its row. It never
overwrites an XBRL fact (INSERT ... ON CONFLICT DO NOTHING). Dry run by default.

Two kinds of figure: a NUMBER read from a table row (the default), and a STATED ZERO (kind: stated_zero) - a balance the
company states to be nil in its report ("ASM was debt-free", "the amount outstanding ... was nil"). A stated zero is stored only
if the report still contains the stating sentence and the balance sheet still has no line of that nature.

Usage:
    python scripts/33_load_note_facts.py                 # extract + check + show what would be stored
    python scripts/33_load_note_facts.py --apply         # store (one transaction)
    python scripts/33_load_note_facts.py --only loreal_2025_da
"""
import argparse
import datetime
import importlib.util
import os
import re
import sys
import zipfile
from pathlib import Path

import yaml
from lxml import etree

ROOT = Path(__file__).parent.parent
SPEC_PATH = ROOT / "data" / "mappings" / "reviewed_note_figures.yaml"
REPORT_DIRS = ("data/raw/historical", "data/raw", "data/raw/gate40")
XHTML = "{http://www.w3.org/1999/xhtml}"
REQUIRED = ("id", "company", "report", "row", "years", "column", "scale", "decimal", "currency", "concept", "statement",
            "label", "perimeter", "checks", "evidence")
REQUIRED_ZERO = ("id", "company", "report", "years", "currency", "concept", "statement", "label", "perimeter", "checks",
                 "evidence")
_RR = None


def _rr():
    """reconcile_reports.py - its number parser and its reader of a report's tagged facts."""
    global _RR
    if _RR is None:
        spec = importlib.util.spec_from_file_location("reconcile_reports_33", Path(__file__).parent / "reconcile_reports.py")
        _RR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_RR)
    return _RR


# ---------------------------------------------------------------- the specification

def load_specs(path=SPEC_PATH) -> list:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs, seen = [], set()
    for f in data.get("figures", []):
        required = REQUIRED_ZERO if f.get("kind") == "stated_zero" else REQUIRED
        missing = [k for k in required if k not in f or f[k] in (None, "", [])]
        if missing:
            raise ValueError(f"figure {f.get('id')} is missing {missing}")
        if f["id"] in seen:
            raise ValueError(f"figure id {f['id']} is listed twice")
        seen.add(f["id"])
        specs.append(f)
    return specs


def locate_report(name: str, root=ROOT):
    for d in REPORT_DIRS:
        p = Path(root) / d / name
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------- reading a report's tables

def read_rows(zip_path) -> list:
    """Every table row of the annual report in document order: [(index, [non-empty cell texts])]."""
    z = zipfile.ZipFile(zip_path)
    name = next((n for n in z.namelist() if n.lower().endswith((".xhtml", ".html")) and "/reports/" in n.lower()), None) \
        or next(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html")))
    root = etree.fromstring(z.read(name), etree.XMLParser(recover=True, huge_tree=True))
    rows = []
    for i, tr in enumerate(root.iter(XHTML + "tr")):
        cells = [" ".join("".join(td.itertext()).split()) for td in tr if td.tag.split("}")[-1] in ("td", "th")]
        cells = [c for c in cells if c]
        if cells:
            rows.append((i, cells))
    return rows


def read_text(zip_path) -> list:
    """The report's running text as lines (own text and tails of every block element), for the checks that look for a
    sentence rather than a number."""
    z = zipfile.ZipFile(zip_path)
    name = next((n for n in z.namelist() if n.lower().endswith((".xhtml", ".html")) and "/reports/" in n.lower()), None)         or next(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html")))
    root = etree.fromstring(z.read(name), etree.XMLParser(recover=True, huge_tree=True))
    lines = []
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.split("}")[-1] in ("p", "div", "li", "span", "td", "th"):
            own = " ".join((el.text or "").split())
            if own:
                lines.append(own)
            for ch in el:
                if ch.tail and ch.tail.strip():
                    lines.append(" ".join(ch.tail.split()))
    return lines


def parse_cell(text: str, decimal: str = ","):
    """A printed table cell as a float, or None if it is not a number: French/Swedish '1 652,4', English '1,652.4',
    negatives as '(837)', '-1 099' or '–1 099'. A bare dash is not a number here."""
    s = (text or "").strip().replace("−", "-")
    if not s or s in {"-", "–", "—"}:
        return None
    neg = s.startswith("(") and s.endswith(")") or s[0] in "-–—"
    s = s.strip("()").lstrip("-–—").strip()
    if not re.fullmatch(r"[\d\s  .,]+", s):
        return None
    value = _rr().parse_number(s, "numcommadecimal" if decimal == "," else "", "0", "")
    return None if value is None else (-value if neg else value)


def numeric_values(cells: list, decimal: str) -> list:
    return [v for v in (parse_cell(c, decimal) for c in cells[1:]) if v is not None]


def column_values(cells: list, decimal: str) -> list:
    """Like numeric_values, but a bare dash counts as 0 so every number keeps its column. Used where columns of
    DIFFERENT rows are added up (the EBITDA bridge): Essity's FY2025 reconciliation prints "–" for 2025 on its IAC
    row and 70 for 2024 - dropping the dash shifted 70 into the 2025 column and refused a bridge that adds up."""
    out = []
    for c in cells[1:]:
        if c.strip() in {"-", "–", "—"}:
            out.append(0.0)
        else:
            v = parse_cell(c, decimal)
            if v is not None:
                out.append(v)
    return out


def header_years(rows: list, pos: int, n: int):
    """The fiscal years of the table row at position `pos`: the nearest row above it (within 12 rows) whose numeric cells are
    all years. Returns the last `n` of them, or None."""
    for k in range(pos - 1, max(pos - 13, -1), -1):
        vals = numeric_values(rows[k][1], ",")
        if len(vals) >= n >= 1 and all(1990 <= v <= 2100 and v == int(v) for v in vals):
            return [int(v) for v in vals[-n:]]
    return None


def report_facts(zip_path) -> list:
    return _rr().read_package(str(zip_path))["facts"]


def fact_by_year(facts: list, tags, span=(300, 400), instant=False) -> dict:
    """{year: value} of the first tag in `tags` that has an annual duration ending that year (year of the printed end date),
    or - with instant=True - a balance at that year end."""
    out = {}
    for tag in ([tags] if isinstance(tags, str) else tags):
        for f in facts:
            if instant:
                if f["tag"] == tag and f["instant"]:
                    out.setdefault(datetime.date.fromisoformat(f["instant"]).year, f["value"])
                continue
            if f["tag"] != tag or not f["start"] or not f["end"]:
                continue
            d0, d1 = datetime.date.fromisoformat(f["start"]), datetime.date.fromisoformat(f["end"])
            if span[0] <= (d1 - d0).days + 1 <= span[1]:
                out.setdefault(d1.year, f["value"])
    return out


# ---------------------------------------------------------------- extracting one reviewed figure

class FigureError(Exception):
    """The report does not contain what the reviewed specification says it does - nothing is stored."""


def _stated_zero(spec: dict, rows: list, facts: list, text_lines) -> list:
    """A balance the company states to be nil: one entry per listed year, valid only if the report still says so."""
    out = [{"year": int(y), "value": 0.0, "row_index": None, "row_text": "stated nil (see checks)", "pos": None, "checks": []}
           for y in spec["years"]]
    joined = " ".join(text_lines or [])
    for chk in spec["checks"]:
        (kind, arg), = chk.items()
        if kind == "report_states":
            if not re.search(arg, joined, re.I):
                raise FigureError(f"{spec['id']}: the report no longer contains the stating sentence {arg!r}")
            for r in out:
                r["checks"].append(f"the report states {arg!r}")
        elif kind == "no_row_matching":
            start, end, pat = re.compile(arg["from"], re.I), re.compile(arg["to"], re.I), re.compile(arg["pattern"], re.I)
            starts = [p for p, (_, c) in enumerate(rows) if start.search(c[0])]
            i = starts[int(arg.get("occurrence", 1)) - 1] if len(starts) >= int(arg.get("occurrence", 1)) else None
            j = next((p for p in range(i + 1, len(rows)) if end.search(rows[p][1][0])), None) if i is not None else None
            if i is None or j is None:
                raise FigureError(f"{spec['id']}: cannot locate the section {arg['from']!r} .. {arg['to']!r}")
            hit = [rows[p][1][0] for p in range(i, j + 1) if pat.search(rows[p][1][0]) and numeric_values(rows[p][1], ",")]
            if hit:
                raise FigureError(f"{spec['id']}: the section has a line of that nature after all: {hit}")
            for r in out:
                r["checks"].append(f"no line matching {arg['pattern']!r} between {rows[i][1][0]!r} and {rows[j][1][0]!r}")
        else:
            raise FigureError(f"{spec['id']}: unknown check {kind!r} for a stated zero")
    return out


def extract(spec: dict, rows: list, facts: list, text_lines=None) -> list:
    """[{year, value (in currency units), row_index, row_text, checks: [...]}] for one reviewed figure. Raises
    FigureError when the row cannot be found unambiguously or any check fails."""
    if spec.get("kind") == "stated_zero":
        return _stated_zero(spec, rows, facts, text_lines)
    pat = re.compile(spec["row"], re.I)
    min_values = int(spec.get("min_values", 1))
    hits = [pos for pos, (_, cells) in enumerate(rows)
            if pat.search(cells[0]) and len(numeric_values(cells, spec["decimal"])) >= min_values]
    years_spec, scale, decimal = spec["years"], float(spec["scale"]), spec["decimal"]
    instant = spec.get("period") == "instant"
    picked = []                                     # (pos, year, printed value)
    if years_spec == "header":
        if len(hits) != 1:
            raise FigureError(f"{spec['id']}: expected exactly one row matching {spec['row']!r}, found {len(hits)}")
        pos = hits[0]
        vals = numeric_values(rows[pos][1], decimal)
        yrs = header_years(rows, pos, len(vals)) or header_years(rows, pos, min(len(vals), 3))
        if not yrs:
            raise FigureError(f"{spec['id']}: no header row with years above {rows[pos][1][0]!r}")
        vals = vals[-len(yrs):]
        picked = [(pos, y, v) for y, v in zip(yrs, vals)]
    else:
        if len(hits) != len(years_spec):
            raise FigureError(f"{spec['id']}: expected {len(years_spec)} rows matching {spec['row']!r} (one per year, "
                              f"listed newest first), found {len(hits)}")
        for pos, y in zip(hits, years_spec):
            vals = numeric_values(rows[pos][1], decimal)
            if not vals:
                raise FigureError(f"{spec['id']}: no number in row {rows[pos][1]}")
            picked.append((pos, int(y), vals[-1] if spec["column"] == "last" else vals[0]))
    out = [{"year": y, "value": abs(v) * scale, "row_index": rows[pos][0], "row_text": " | ".join(rows[pos][1])[:200],
            "pos": pos, "checks": []} for pos, y, v in picked]
    by_year = {r["year"]: r for r in out}

    for chk in spec["checks"]:
        (kind, arg), = chk.items()
        if kind == "sane_pct_of_revenue":
            rev = fact_by_year(facts, ["ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"])
            for y, r in by_year.items():
                if y not in rev:
                    r["checks"].append("sane_pct_of_revenue: skipped (no revenue fact for the year)")
                    continue
                pct = 100 * r["value"] / rev[y]
                if not arg[0] <= pct <= arg[1]:
                    raise FigureError(f"{spec['id']} {y}: {pct:.1f}% of revenue is outside the reviewed range {arg}")
                r["checks"].append(f"sane_pct_of_revenue: {pct:.1f}% of revenue (range {arg})")
        elif kind == "sane_pct_of_tagged_fact":
            base = fact_by_year(facts, arg["tag"], instant=instant)
            for y, r in by_year.items():
                if y not in base:
                    r["checks"].append(f"sane_pct_of_tagged_fact: skipped (no {arg['tag']} for {y})")
                    continue
                pct = 100 * r["value"] / base[y]
                if not arg["range"][0] <= pct <= arg["range"][1]:
                    raise FigureError(f"{spec['id']} {y}: {pct:.2f}% of {arg['tag']} is outside the reviewed range {arg['range']}")
                r["checks"].append(f"{pct:.2f}% of {arg['tag'].split(':')[-1]} (range {arg['range']})")
        elif kind == "cross_row_equals_tagged_fact":
            tagged = fact_by_year(facts, arg["tag"], instant=instant)
            cross = re.compile(arg["row"], re.I)
            verified = 0
            for k, (pos, y, _) in enumerate(picked):
                nxt = rows[pos + arg.get("row_offset", 1)][1]
                if not cross.search(nxt[0]):
                    raise FigureError(f"{spec['id']} {y}: row {arg.get('row_offset', 1):+d} from {rows[pos][1][0]!r} is "
                                      f"{nxt[0]!r}, not {arg['row']!r}")
                cross_vals = numeric_values(nxt, decimal)
                if years_spec == "header":                 # same column layout as the figure's row: year k <-> value k
                    cross_vals = cross_vals[-len(picked):]
                    printed = abs(cross_vals[k]) * scale
                else:
                    printed = abs(cross_vals[0 if arg.get("column") == "first" else -1]) * scale
                if y not in tagged:
                    if arg.get("min_verified"):        # a year the report itself does not tag: judged by its position
                        by_year[y]["checks"].append(f"{nxt[0]} {printed:,.0f}: no tagged fact for {y} to compare "
                                                    f"(year taken from the table order)")
                        continue
                if y not in tagged or abs(printed - abs(tagged[y])) > max(0.5 * scale / 1000, float(arg.get("tolerance", 0)) * scale):
                    raise FigureError(f"{spec['id']} {y}: {nxt[0]!r} prints {printed:,.0f} but {arg['tag']} is "
                                      f"{tagged.get(y)!r} - the row/year mapping cannot be trusted")
                verified += 1
                by_year[y]["checks"].append(f"{nxt[0]} {printed:,.0f} = tagged {arg['tag'].split(':')[-1]}")
            if verified < int(arg.get("min_verified", len(picked))):
                raise FigureError(f"{spec['id']}: only {verified} year(s) could be verified against {arg['tag']}, "
                                  f"{arg.get('min_verified', len(picked))} required")
        elif kind == "ebitda_bridge":
            end_pat, start_pat, tol = re.compile(arg["end"], re.I), re.compile(arg["start"], re.I), float(arg.get("tolerance", 1))
            ends = [pos for pos, (_, c) in enumerate(rows) if end_pat.search(c[0]) and len(numeric_values(c, decimal)) >= len(picked)]
            end_pos = next((p for p in ends if header_years(rows, p, len(picked)) == [y for _, y, _ in picked]), None)
            if end_pos is None:
                raise FigureError(f"{spec['id']}: no {arg['end']!r} row under the same years")
            start_pos = next((p for p in range(end_pos - 1, max(end_pos - 12, -1), -1)
                              if start_pat.search(rows[p][1][0]) and len(numeric_values(rows[p][1], decimal)) >= len(picked)), None)
            if start_pos is None:
                raise FigureError(f"{spec['id']}: no {arg['start']!r} row within 12 rows above the {arg['end']!r} row")
            n = len(picked)
            for k, (pos, y, v) in enumerate(picked):
                col = lambda p: column_values(rows[p][1], decimal)[-n:][k]
                between = sum(col(p) for p in range(start_pos + 1, end_pos))
                if abs(col(start_pos) + between - col(end_pos)) > tol:
                    raise FigureError(f"{spec['id']} {y}: bridge {col(start_pos)} + {between} != {col(end_pos)}")
                if abs((col(end_pos) - col(start_pos)) - abs(v)) > tol:
                    raise FigureError(f"{spec['id']} {y}: {rows[end_pos][1][0]} - {rows[start_pos][1][0]} = "
                                      f"{col(end_pos) - col(start_pos):,.1f} but the row prints {abs(v):,.1f}")
                by_year[y]["checks"].append(f"{rows[end_pos][1][0]} {col(end_pos):,.0f} - {rows[start_pos][1][0]} "
                                            f"{col(start_pos):,.0f} = {abs(v):,.0f}")
        else:
            raise FigureError(f"{spec['id']}: unknown check {kind!r}")
    return out


# ---------------------------------------------------------------- the database

def _filing_id(conn, company, report):
    from sqlalchemy import text
    rows = conn.execute(text("""SELECT f.filing_id FROM filing f JOIN company c ON c.company_id = f.company_id
                                WHERE c.name = :co AND replace(f.source_file, '\\', '/') LIKE :pat"""),
                        {"co": company, "pat": f"%/{report}"}).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _period_id(conn, filing_id, year, instant=False):
    """The fiscal year's period, stored the way the loader stores it (Arelle's end date is one day late): an annual duration,
    or - for a balance - an instant at the year end (no start date)."""
    from sqlalchemy import text
    start, end = datetime.date(year, 1, 1).isoformat(), datetime.date(year + 1, 1, 1).isoformat()
    where = ("start_date IS NULL AND period_type = 'instant'" if instant else "start_date = :s AND period_type = 'duration'")
    params = {"f": filing_id, "e": end, **({} if instant else {"s": start})}
    row = conn.execute(text(f"SELECT period_id FROM period WHERE filing_id = :f AND end_date = :e AND {where}"), params).fetchone()
    if row:
        return row[0]
    conn.execute(text("INSERT INTO period (filing_id, start_date, end_date, period_type) VALUES (:f, :s, :e, :t)"),
                 {"f": filing_id, "s": None if instant else start, "e": end, "t": "instant" if instant else "duration"})
    return conn.execute(text(f"SELECT period_id FROM period WHERE filing_id = :f AND end_date = :e AND {where}"), params).fetchone()[0]


def _concept_id(conn, name, statement, label):
    from sqlalchemy import text
    row = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()
    if row:
        return row[0]
    conn.execute(text("INSERT INTO ifrs_concept (normalized_name, statement, display_label) VALUES (:n, :s, :l)"),
                 {"n": name, "s": statement, "l": label})
    return conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()[0]


def store(conn, spec: dict, extracted: list) -> list:
    """Insert the extracted figures as facts; returns [(year, 'stored' | 'exists' | 'no filing')]. Never overwrites."""
    from sqlalchemy import text
    filing = _filing_id(conn, spec["company"], spec["report"])
    if filing is None:
        return [(r["year"], f"no single filing of {spec['company']} loaded from {spec['report']}") for r in extracted]
    concept = _concept_id(conn, spec["concept"], spec["statement"], spec["label"])
    out = []
    for r in extracted:
        period = _period_id(conn, filing, r["year"], instant=spec.get("period") == "instant")
        n = conn.execute(text("""INSERT INTO fact_value (filing_id, period_id, concept_id, raw_xbrl_tag, value, currency, decimals,
                                                         context_ref, dimensions)
                                 VALUES (:f, :p, :c, :tag, :v, :cur, NULL, :ctx, NULL)
                                 ON CONFLICT (filing_id, period_id, concept_id) DO NOTHING"""),
                         {"f": filing, "p": period, "c": concept, "tag": f"note:{spec['id']}", "v": r["value"],
                          "cur": spec["currency"],
                          "ctx": f"{spec['report']}#" + ("stated" if r["row_index"] is None else f"row{r['row_index']}")}).rowcount
        out.append((r["year"], "stored" if n else "exists"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to the database (default: dry run)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="only these figure ids")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    specs = [s for s in load_specs() if not args.only or s["id"] in args.only]
    plan, failed = [], 0
    for spec in specs:
        path = locate_report(spec["report"])
        if path is None:
            print(f"[{spec['id']}] report {spec['report']} not on this machine - skipped")
            failed += 1
            continue
        try:
            got = extract(spec, read_rows(path), report_facts(path),
                          read_text(path) if spec.get("kind") == "stated_zero" else None)
        except FigureError as e:
            print(f"[{spec['id']}] REFUSED: {e}")
            failed += 1
            continue
        plan.append((spec, got))
        print(f"[{spec['id']}] {spec['company']} - {spec['report']}: {spec['perimeter']}")
        for r in got:
            print(f"    {r['year']}: {r['value']:>18,.0f}  <- " + (f"row {r['row_index']}: {r['row_text'][:90]}"
                                                                     if r["row_index"] is not None else r["row_text"]))
            for c in r["checks"]:
                print(f"          check ok: {c}")
    if not args.apply:
        print(f"\nDry run: {sum(len(g) for _, g in plan)} figure(s) would be offered for storage; {failed} refused/skipped. "
              f"Pass --apply to store.")
        return 1 if failed else 0
    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set (see .env)")
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        for spec, got in plan:
            for year, status in store(conn, spec, got):
                print(f"  {spec['id']} {year}: {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
