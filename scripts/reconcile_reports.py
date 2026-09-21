"""
scripts/reconcile_reports.py

THE RULE THIS CHECKS
--------------------
Every figure this project shows must equal the figure in the company's own published annual
report. This tool compares the two, line by line, for the primary statements:

  * the company's OWN report (the inline-XBRL page inside its ESEF package) says what was printed;
  * the company's OWN presentation linkbase (same package) says which lines each statement has;
  * the database says what we stored.

For every printed line of the balance sheet / income statement / cash flow statement at the
reporting date it reports one of:
    OK          stored, and equal to the printed value (to the unit)
    DIFFERENT   stored, but not equal to the printed value (scale, sign, wrong period, overwritten)
    MISSING     printed but not stored (an unmapped tag, or a dimensional-only fact)

WHY IT EXISTS
-------------
Found by checking one line of one company against its statements: Recordati prints its borrowings
on two extension lines that were never mapped, so net debt silently came from "total non-current
liabilities". A downstream fallback hid a missing INPUT. Nothing in the pipeline compared the
inputs with the source, so nothing could notice. This does, for every company and year, without a
human reading each report and without an LLM - only the company's own documents.

USAGE
-----
    python scripts/reconcile_reports.py                          # every package under data/raw
    python scripts/reconcile_reports.py --zip data/raw/historical/lvmh_2024-12-31.zip
    python scripts/reconcile_reports.py --kind balance           # balance | income | cashflow | all
    python scripts/reconcile_reports.py --csv reconciliation.csv # every line, for review
Needs DATABASE_URL to compare with what is stored; without it, prints only what the report contains.
Read-only.
"""
import argparse
import csv
import datetime
import glob
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

from lxml import etree

IX = "{http://www.xbrl.org/2013/inlineXBRL}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
PARENT_CHILD = "http://www.xbrl.org/2003/arcrole/parent-child"

# Role names are the FILER's own, in the filer's language (found on the first real runs: French "Etat du
# resultat global", Spanish "Estado de situacion financiera", Swedish "Rapport over finansiell stallning",
# Schneider's "Actifs" / "Capitaux propres et passifs"). Matched on accent-stripped text.
KINDS = {
    "balance": re.compile(r"financial ?position|balance ?sheet|situation ?financi|situacion ?financiera|stato ?patrimoniale|"
                          r"finansiell ?stallning|\bactifs\b|capitaux ?propres ?et ?passifs|\bbilan\b|\bbilanz\b", re.I),
    "income": re.compile(r"profit ?or ?loss|income ?statement|statement ?of ?income|comprehensive ?income|conto ?economico|"
                         r"resultat|resultado|compte ?de ?r", re.I),
    "cashflow": re.compile(r"cash ?flow|flux ?de ?tr|flujos ?de ?efectivo|rendiconto ?finanziario|kassafl", re.I),
}
# a role that only lists notes is not a primary statement
NOT_PRIMARY = re.compile(r"disclosure|notes?[ _-]|note[0-9]|reconciliation|segment|balises|etiquetas|mandatory", re.I)


def _plain(text: str) -> str:
    """Lower-cased text without accents: 'résultat' == 'resultat', 'kassaflöden' == 'kassafloden'."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch))


# ---------------------------------------------------------------- numbers as printed

def parse_number(text: str, fmt: str = "", scale: str = "0", sign: str = ""):
    """The value a reader sees in an inline-XBRL cell, as a float: format-aware (1,234.5 vs 1.234,5 vs
    '1 475'), scaled (scale=6 -> millions) and signed (sign='-' -> negative). None when it is not a number."""
    s = (text or "").strip()
    fmt = (fmt or "").lower()
    if re.search(r"zerodash|fixed-zero|fixedzero", fmt) or s in {"-", "—", "–", ""}:
        return 0.0 if re.search(r"zerodash|fixed-zero|fixedzero", fmt) or s in {"-", "—", "–"} else None
    s = re.sub(r"[\s  ']", "", s)               # spaces/NBSP as thousands separators
    if "comma-decimal" in fmt or "numcommadecimal" in fmt:
        s = s.replace(".", "").replace(",", ".")
    else:                                                 # dot-decimal, or unspecified
        s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None
    value *= 10 ** int(scale or 0)
    return -value if sign == "-" else value


# ---------------------------------------------------------------- the report (what was printed)

def read_inline_facts(xhtml: bytes) -> list:
    """Non-dimensional numeric facts as printed: [{tag, instant, start, end, value, text}]."""
    root = etree.fromstring(xhtml, etree.XMLParser(recover=True, huge_tree=True))
    contexts = {}
    for c in root.iter(XBRLI + "context"):
        if any(True for _ in c.iter(XBRLDI + "explicitMember")) or any(True for _ in c.iter(XBRLDI + "typedMember")):
            continue                                       # dimensional: not a headline statement figure
        inst, start, end = c.find(f".//{XBRLI}instant"), c.find(f".//{XBRLI}startDate"), c.find(f".//{XBRLI}endDate")
        contexts[c.get("id")] = (inst.text.strip() if inst is not None else None,
                                 start.text.strip() if start is not None else None,
                                 end.text.strip() if end is not None else None)
    facts = []
    for el in root.iter(IX + "nonFraction"):
        ctx = contexts.get(el.get("contextRef"))
        if ctx is None:
            continue
        text = "".join(el.itertext())
        value = parse_number(text, el.get("format", ""), el.get("scale", "0"), el.get("sign", ""))
        if value is None:
            continue
        facts.append({"tag": el.get("name"), "instant": ctx[0], "start": ctx[1], "end": ctx[2],
                      "value": value, "text": text.strip()})
    return facts


def reporting_date(facts: list):
    """The balance-sheet date: the latest instant that carries the most statement facts."""
    counts = {}
    for f in facts:
        if f["instant"]:
            counts[f["instant"]] = counts.get(f["instant"], 0) + 1
    if not counts:
        return None
    top = max(counts.values())
    return max(d for d, n in counts.items() if n >= 0.5 * top)


# ---------------------------------------------------------------- the company's own statement structure

def qname_from_fragment(fragment: str) -> str:
    """'ifrs-full_Assets' -> 'ifrs-full:Assets'; 'Rec_LoansDueAfterOneYear' -> 'Rec:LoansDueAfterOneYear'."""
    fragment = fragment.split("#")[-1]
    return fragment.replace("_", ":", 1)


def role_definitions(xml_blobs: list) -> dict:
    """{roleURI: definition text} from the extension schema's roleType elements."""
    defs = {}
    for blob in xml_blobs:
        try:
            root = etree.fromstring(blob, etree.XMLParser(recover=True, huge_tree=True))
        except Exception:
            continue
        for rt in root.iter(LINK + "roleType"):
            d = rt.find(LINK + "definition")
            defs[rt.get("roleURI")] = (d.text or "").strip() if d is not None else ""
    return defs


def read_presentation(xml_blobs: list) -> dict:
    """{roleURI: [concept qnames in presentation order]} from the presentation linkbase(s)."""
    trees = {}
    for blob in xml_blobs:
        try:
            root = etree.fromstring(blob, etree.XMLParser(recover=True, huge_tree=True))
        except Exception:
            continue
        for pl in root.iter(LINK + "presentationLink"):
            role = pl.get(XLINK + "role")
            locs = {loc.get(XLINK + "label"): qname_from_fragment(loc.get(XLINK + "href", "")) for loc in pl.iter(LINK + "loc")}
            arcs = [(a.get(XLINK + "from"), a.get(XLINK + "to"), float(a.get("order", "0") or 0))
                    for a in pl.iter(LINK + "presentationArc") if a.get(XLINK + "arcrole") == PARENT_CHILD]
            children, seen = {}, []
            for frm, to, order in arcs:
                children.setdefault(frm, []).append((order, to))
            targets = {to for _, to, _ in arcs}
            roots = [f for f in dict.fromkeys(f for f, _, _ in arcs) if f not in targets]

            def walk(label):
                if label in seen:
                    return
                seen.append(label)
                for _, child in sorted(children.get(label, [])):
                    walk(child)
            for r in roots:
                walk(r)
            ordered = [locs[l] for l in seen if l in locs]
            trees.setdefault(role, [])
            trees[role].extend(q for q in ordered if q not in trees[role])
    return trees


def statement_lines(trees: dict, defs: dict, kind: str) -> list:
    """Concept qnames on the primary statement(s) of `kind`, in printed order. A role is matched on its
    definition text AND its URI (standard IFRS role URIs name the statement)."""
    pattern = KINDS[kind]
    lines = []
    for role, concepts in trees.items():
        text = _plain(f"{defs.get(role, '')} {role}")
        if pattern.search(text) and not NOT_PRIMARY.search(_plain(defs.get(role, ""))):
            lines.extend(q for q in concepts if q not in lines)
    return lines


def read_labels(xml_blobs: list) -> dict:
    """{concept qname: english label} for the extension concepts in the package (standard IFRS labels are external)."""
    labels = {}
    for blob in xml_blobs:
        try:
            root = etree.fromstring(blob, etree.XMLParser(recover=True, huge_tree=True))
        except Exception:
            continue
        for ll in root.iter(LINK + "labelLink"):
            locs = {loc.get(XLINK + "label"): qname_from_fragment(loc.get(XLINK + "href", "")) for loc in ll.iter(LINK + "loc")}
            texts = {l.get(XLINK + "label"): "".join(l.itertext()).strip() for l in ll.iter(LINK + "label")
                     if (l.get(XLINK + "role") or "").endswith("/label")}
            for arc in ll.iter(LINK + "labelArc"):
                q, t = locs.get(arc.get(XLINK + "from")), texts.get(arc.get(XLINK + "to"))
                if q and t:
                    labels.setdefault(q, t)
    return labels


# ---------------------------------------------------------------- the comparison

def reconcile(lines: list, facts: list, date: str, stored: dict, tolerance: float = 1.0) -> list:
    """One row per printed statement line at `date`:
    {tag, printed, stored, status}. `stored` = {(tag, date_or_(start,end)): value}. For flow statements pass
    the duration end as `date` and the facts are matched on their (non-null) end."""
    printed, wrong_period = {}, {}
    for f in facts:
        key_date = f["instant"] or f["end"]
        if key_date != date:
            continue
        # a flow fact must span about a year; an instant has no start
        if f["start"]:
            d0 = datetime.date.fromisoformat(f["start"]); d1 = datetime.date.fromisoformat(f["end"])
            if not 300 <= (d1 - d0).days <= 400:
                wrong_period.setdefault(f["tag"], f["value"])   # e.g. Recordati FY2022: start == end
                continue
        printed.setdefault(f["tag"], f["value"])
    rows = []
    for tag in lines:
        if tag not in printed and tag in wrong_period:
            # printed, but the FILER's own period is not a year (Recordati tags its P&L and cash flow this way,
            # every year). Never silently drop it - and say whether we still hold the printed figure:
            # OK_BAD_PERIOD = stored and equal (the pipeline coped), BAD_PERIOD = not stored / different.
            sv = stored.get((tag, date))
            ok = sv is not None and abs(sv - wrong_period[tag]) <= tolerance
            rows.append({"tag": tag, "printed": wrong_period[tag], "stored": sv,
                         "status": "OK_BAD_PERIOD" if ok else "BAD_PERIOD"})
            continue
        if tag not in printed:
            continue                                        # an abstract/header node or a line with no figure
        sv = stored.get((tag, date))
        if sv is None:
            status = "MISSING"
        elif abs(sv - printed[tag]) <= tolerance:
            status = "OK"
        else:
            status = "DIFFERENT"
        rows.append({"tag": tag, "printed": printed[tag], "stored": sv, "status": status})
    return rows


# ---------------------------------------------------------------- package + database plumbing

def read_package(zip_path: str) -> dict:
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    report = next((n for n in names if n.lower().endswith((".xhtml", ".html")) and "/reports/" in n.lower()), None) \
        or next((n for n in names if n.lower().endswith((".xhtml", ".html"))), None)
    xml = [n for n in names if n.lower().endswith((".xml", ".xsd")) and "/reports/" not in n.lower()]
    blobs = lambda pred: [z.read(n) for n in xml if pred(n.lower())]
    return {"facts": read_inline_facts(z.read(report)) if report else [],
            "pre": blobs(lambda n: n.endswith("pre.xml")),
            "xsd": blobs(lambda n: n.endswith(".xsd")),
            "lab": blobs(lambda n: "lab" in n and n.endswith(".xml") and ("-en" in n or "_en" in n or "lab.xml" in n))}


def fetch_stored(engine, source_basename: str) -> dict:
    """{(raw tag, ISO date): value} of the non-dimensional facts loaded from one package. Arelle stores every end
    date one day late, so it is shifted back to the date printed in the report."""
    from sqlalchemy import text
    q = text("""SELECT fv.raw_xbrl_tag, p.start_date, p.end_date, fv.value
                FROM fact_value fv JOIN filing f ON f.filing_id = fv.filing_id JOIN period p ON p.period_id = fv.period_id
                WHERE f.source_file LIKE :pat""")
    out = {}
    with engine.connect() as c:
        for tag, start, end, value in c.execute(q, {"pat": f"%{source_basename}"}):
            if value is None:
                continue
            day = (end - datetime.timedelta(days=1)).isoformat()
            out[(tag, day)] = float(value)
    return out


def summarize(rows: list) -> dict:
    n = {"OK": 0, "DIFFERENT": 0, "MISSING": 0, "BAD_PERIOD": 0, "OK_BAD_PERIOD": 0}
    for r in rows:
        n[r["status"]] += 1
    n["lines"] = len(rows)
    # a figure we hold and that equals the printed one is a match, even when the filer's own period is malformed
    n["share_ok"] = round(100 * (n["OK"] + n["OK_BAD_PERIOD"]) / n["lines"], 1) if n["lines"] else None
    return n


def run_package(zip_path: str, kinds: list, engine=None) -> list:
    pkg = read_package(zip_path)
    if not pkg["facts"]:
        return []
    defs, trees, labels = role_definitions(pkg["xsd"] + pkg["pre"]), read_presentation(pkg["pre"]), read_labels(pkg["lab"])
    date = reporting_date(pkg["facts"])
    stored = fetch_stored(engine, os.path.basename(zip_path)) if engine is not None else {}
    if engine is not None and not stored:
        # nothing of this package is in the database: that is "not loaded", not "every line missing"
        return [{"package": os.path.basename(zip_path), "date": date, "statement": "all", "label": "(package not loaded)",
                 "tag": "", "printed": None, "stored": None, "status": "NOT_LOADED"}]
    out = []
    for kind in kinds:
        lines = statement_lines(trees, defs, kind)
        if not lines:
            # never a silent zero: "no lines" must be distinguishable from "everything reconciled"
            out.append({"package": os.path.basename(zip_path), "date": date, "statement": kind, "label": "(statement not located)",
                        "tag": "", "printed": None, "stored": None, "status": "NO_STATEMENT"})
            continue
        for r in reconcile(lines, pkg["facts"], date, stored):
            out.append({"package": os.path.basename(zip_path), "date": date, "statement": kind,
                        "label": labels.get(r["tag"], r["tag"].split(":")[-1]), **r})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", help="one package (default: every package under data/raw)")
    ap.add_argument("--kind", choices=["balance", "income", "cashflow", "all"], default="all")
    ap.add_argument("--csv", help="write every line to this CSV")
    ap.add_argument("--no-db", action="store_true", help="do not compare with the database")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    engine = None
    if not args.no_db:
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        load_dotenv()
        if os.environ.get("DATABASE_URL"):
            engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    root = Path(__file__).parent.parent / "data" / "raw"
    zips = [args.zip] if args.zip else sorted(glob.glob(str(root / "*.zip")) + glob.glob(str(root / "*" / "*.zip")))
    kinds = ["balance", "income", "cashflow"] if args.kind == "all" else [args.kind]

    all_rows = []
    for z in zips:
        rows = run_package(z, kinds, engine)
        all_rows.extend(rows)
        if rows and rows[0]["status"] == "NOT_LOADED":
            print(f"{os.path.basename(z):46s} not loaded in the database - not checked", flush=True)
            continue
        for kind in kinds:
            krows = [r for r in rows if r["statement"] == kind]
            if any(r["status"] == "NO_STATEMENT" for r in krows):
                print(f"{os.path.basename(z):46s} {kind:9s} STATEMENT NOT LOCATED (role names not recognised)", flush=True)
                continue
            s = summarize(krows)
            print(f"{os.path.basename(z):46s} {kind:9s} lines={s['lines']:3d} OK={s['OK']:3d} OK(filer period malformed)={s['OK_BAD_PERIOD']:2d} "
                  f"DIFFERENT={s['DIFFERENT']:2d} MISSING={s['MISSING']:3d} BAD_PERIOD={s['BAD_PERIOD']:2d}  share matching={s['share_ok']}%", flush=True)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["package", "date", "statement", "tag", "label", "printed", "stored", "status"])
            w.writeheader()
            w.writerows({k: r[k] for k in w.fieldnames} for r in all_rows)
        print("wrote", args.csv)
