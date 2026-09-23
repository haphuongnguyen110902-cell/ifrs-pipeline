"""
scripts/34_link_renamed_tags.py

Companies rename their own extension elements between years. EssilorLuxottica's FY2025 report switched its French names
to English (el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles -> el:PurchaseOfPropertyPlantAndEquipmentAnd
IntangibleAssets). Classified as new, a renamed line becomes a new concept: the series breaks, and a line the engine
reads (here capex) silently disappears for the new year.

The evidence is in the numbers: every report restates last year as its comparative. When an unmapped extension tag's
value for a period equals - exactly and uniquely - the value one already-mapped concept of the same company holds for
the same period (loaded from that year's own report), they are the same line, and the new tag is added to that concept.
Rules, all deterministic: non-dimensional facts only; the match must be unique among the company's facts for that
period; every overlapping period must agree on the same concept; the value must be at least 0.1% of the filing's
largest revenue/assets figure (so 0 or a rounding-size number never links anything). Standard ifrs-full tags are never
linked - they keep their global IFRS meaning. Anything not linked goes to the normal classifier (13_batch_prep.py).

    python scripts/34_link_renamed_tags.py data/raw/historical/essilorluxottica_2025-12-31.zip       # dry run
    python scripts/34_link_renamed_tags.py data/raw/historical/*.zip --apply
"""
import argparse
import datetime
import importlib.util
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
MAPPING = ROOT / "data" / "mappings" / "ifrs_concepts_v0.yaml"
EVIDENCE = ROOT / "data" / "mappings" / "LINKED_renames.yaml"
SCALE_TAGS = ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers", "ifrs-full:Assets")
MIN_SHARE = 0.001


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _day(d):
    return d.date() if isinstance(d, datetime.datetime) else d


def propose_links(new_facts, lookup, stored):
    """new_facts: [(tag, start, end, value)] of ONE filing, non-dimensional. stored: [(tag, concept, start, end, value)]
    already loaded for the same company. Returns ({tag: (concept, [(start, end, value, predecessor_tag)])}, {tag: why})."""
    scale = max((abs(v) for t, s, e, v in new_facts if t in SCALE_TAGS and v is not None), default=0.0)
    floor = MIN_SHARE * scale
    by_period = {}
    for tag, concept, s, e, v in stored:
        by_period.setdefault((s, e), []).append((tag, concept, v))
    links, skipped = {}, {}
    candidates = sorted({t for t, *_ in new_facts if t not in lookup and not t.startswith("ifrs-full:")})
    for tag in candidates:
        agreed, evidence, why = None, [], None
        for t, s, e, v in new_facts:
            if t != tag or v is None or (s, e) not in by_period:
                continue
            if abs(v) < floor or v == 0:
                continue
            hits = {c for pt, c, pv in by_period[(s, e)] if pv is not None and abs(pv - v) <= 0.5 and pt != tag}
            if len(hits) > 1:
                why = f"value {v:,.0f} matches {len(hits)} concepts for {e} - ambiguous"
                break
            if not hits:
                continue
            (c,) = hits
            if agreed not in (None, c):
                why = f"periods disagree ({agreed} vs {c})"
                break
            agreed = c
            pred = sorted(pt for pt, pc, pv in by_period[(s, e)] if pc == c and pv is not None and abs(pv - v) <= 0.5)
            evidence.append((s, e, v, pred[0]))
        if why:
            skipped[tag] = why
        elif agreed is None:
            skipped[tag] = "no overlapping period with a matching, material value"
        else:
            links[tag] = (agreed, evidence)
    return links, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zips", nargs="+")
    ap.add_argument("--apply", action="store_true", help="edit the mapping and record the evidence (default: dry run)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    b09 = _load("batch_load_09", "09_batch_load.py")
    lh = _load("load_historical", "load_historical.py")
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    load_dotenv()
    engine = create_engine(os.environ["DATABASE_URL"])
    lookup = b09.load_mapping(str(MAPPING))
    all_links = []
    for z in args.zips:
        p = Path(z)
        matched, _ = lh.discover_files(p.parent)
        item = next((m for m in matched if m["path"].name == p.name), None)
        if item is None:
            print(f"{p.name}: not a known <company>_<date>.zip - skipped")
            continue
        company = item["company"]
        df = b09.parse_one(str(p))
        df = df[[not d for d in df["dimensions"]]]
        new_facts = [(q, _day(s), _day(e), _num(v)) for q, s, e, v in
                     zip(df["concept_qname"], df["period_start"], df["period_end"], df["value"])]
        with engine.connect() as conn:
            stored = [(r[0], r[1], r[2], r[3], float(r[4])) for r in conn.execute(text("""
                SELECT fv.raw_xbrl_tag, ic.normalized_name, p.start_date, p.end_date, fv.value FROM fact_value fv
                JOIN ifrs_concept ic ON ic.concept_id = fv.concept_id JOIN period p ON p.period_id = fv.period_id
                JOIN filing f ON f.filing_id = fv.filing_id JOIN company c ON c.company_id = f.company_id
                WHERE c.name = :n AND fv.raw_xbrl_tag NOT LIKE 'note:%' AND fv.value IS NOT NULL"""),
                {"n": company}).fetchall()]
        links, skipped = propose_links(new_facts, lookup, stored)
        print(f"\n{company} - {p.name}: {len(links)} renamed tag(s) linked, {len(skipped)} left to the classifier")
        for tag, (concept, ev) in links.items():
            s, e, v, pred = ev[0]
            print(f"  LINK {tag}\n       -> {concept}   ({len(ev)} period(s); e.g. {v:,.0f} for {s}..{e} = {pred})")
            all_links.append({"company": company, "report": p.name, "tag": tag, "concept": concept,
                              "matched": [{"start": str(s), "end": str(e), "value": v, "stored_under": pt}
                                          for s, e, v, pt in ev]})
        for tag, why in skipped.items():
            print(f"  skip {tag}: {why}")
        for tag, (concept, _) in links.items():         # later zips see this zip's links too
            lookup[tag] = (concept, None, None)
    if not args.apply:
        print(f"\nDRY RUN: {len(all_links)} link(s) proposed. Pass --apply to write them.")
        return 0
    data = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    where = {name: st for st, cs in data.items() for name in cs}
    for link in all_links:
        tags = data[where[link["concept"]]][link["concept"]]["xbrl_tags"]
        if link["tag"] not in tags:
            tags.append(link["tag"])
    MAPPING.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding="utf-8")
    old = (yaml.safe_load(EVIDENCE.read_text(encoding="utf-8")) or {}).get("links", []) if EVIDENCE.exists() else []
    EVIDENCE.write_text(yaml.dump({"_about": "Renamed extension tags linked to their predecessor's concept by "
                                             "value evidence (scripts/34_link_renamed_tags.py).",
                                   "links": old + all_links}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\nApplied {len(all_links)} link(s) to {MAPPING.name}; evidence in {EVIDENCE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
