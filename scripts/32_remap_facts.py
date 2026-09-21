"""
Re-point stored facts to the concept the mapping file NOW assigns to their XBRL tag.

Why: the mapping file (data/mappings/ifrs_concepts_v0.yaml) is the record of "this tag is that concept", but
fact_value stores the concept a fact had when it was loaded. When a mapping is corrected, already-loaded facts keep
the old concept until they are re-pointed - a full re-parse of the filings would do it, but it needs Arelle and every
raw package, while the answer is already in the database: fact_value.raw_xbrl_tag is the original tag and
`ifrs_concept` / `concept_mapping` say what it was mapped to.

Found in the capex audit: EssilorLuxottica, Kering, L'Oreal and Pernod Ricard print ONE combined "purchase of PP&E and
intangibles" line under extension tags that had been classified into one-off concepts (`..._el_x`, ...) the ratio
engine never reads, so their capex fell back to a flat 3% of revenue (Kering 2023: 587M assumed vs 2,611M printed).

Nothing is guessed or deleted: a fact moves only if its tag is in the mapping file under a DIFFERENT concept; a fact
whose (filing, period) already has a row under the target concept is skipped and reported; old concepts are left in
place. Dry run by default (SELECTs only); --apply is one transaction.

Usage:
    python scripts/32_remap_facts.py                                  # report the drift and what would move
    python scripts/32_remap_facts.py --tags el:Foo,kering:Bar --apply # move only these tags
    python scripts/32_remap_facts.py --company "Kering" --all --apply # everything that drifted, one company
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

MAPPING = Path(__file__).parent.parent / "data" / "mappings" / "ifrs_concepts_v0.yaml"

_CLASH = """AND EXISTS (SELECT 1 FROM fact_value b WHERE b.filing_id = fv.filing_id
                          AND b.period_id = fv.period_id AND b.concept_id = :new)"""


def load_lookup(path=MAPPING) -> dict:
    """{tag: (normalized_name, statement, display_label)} - the loader's own reading of the mapping file."""
    spec = importlib.util.spec_from_file_location("batch_load_09", Path(__file__).parent / "09_batch_load.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.load_mapping(str(path))


def find_drift(conn, lookup: dict, company=None) -> pd.DataFrame:
    """One row per (tag, current concept, target concept, company): how many stored facts carry a concept other than
    the one the mapping file assigns to their tag."""
    sql = """
        SELECT fv.raw_xbrl_tag AS tag, ic.normalized_name AS current_concept, c.name AS company, COUNT(*) AS n_facts
        FROM fact_value fv
        JOIN ifrs_concept ic ON ic.concept_id = fv.concept_id
        JOIN filing f ON f.filing_id = fv.filing_id
        JOIN company c ON c.company_id = f.company_id
        {where}
        GROUP BY fv.raw_xbrl_tag, ic.normalized_name, c.name
    """
    df = pd.read_sql(text(sql.format(where="WHERE c.name = :co" if company else "")), conn,
                     params={"co": company} if company else {})
    df["target_concept"] = df["tag"].map(lambda t: lookup[t][0] if t in lookup else None)
    drift = df[df["target_concept"].notna() & (df["target_concept"] != df["current_concept"])]
    return drift.sort_values(["tag", "company"]).reset_index(drop=True)


def _scope(company):
    return ("AND fv.filing_id IN (SELECT f.filing_id FROM filing f JOIN company c ON c.company_id = f.company_id "
            "WHERE c.name = :co)") if company else ""


def _concept_id(conn, name, statement, label):
    row = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()
    if row:
        return row[0]
    conn.execute(text("INSERT INTO ifrs_concept (normalized_name, statement, display_label) VALUES (:n, :s, :l)"),
                 {"n": name, "s": statement, "l": label})
    return conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()[0]


def preview_drift(conn, lookup: dict, drift: pd.DataFrame, company=None) -> pd.DataFrame:
    """What --apply would do, from SELECTs only: per (tag, current concept), the facts that would move and those that
    would be skipped because the target concept already has a row for the same filing and period."""
    rows = []
    for (tag, current), _ in drift.groupby(["tag", "current_concept"]):
        name = lookup[tag][0]
        old = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": current}).scalar()
        new = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).scalar()
        params = {"old": old, "new": new if new is not None else -1, "tag": tag, **({"co": company} if company else {})}
        base = f"SELECT COUNT(*) FROM fact_value fv WHERE fv.concept_id = :old AND fv.raw_xbrl_tag = :tag {_scope(company)}"
        total = conn.execute(text(base), params).scalar()
        clash = conn.execute(text(f"{base} {_CLASH}"), params).scalar() if new is not None else 0
        rows.append({"tag": tag, "from": current, "to": name, "would_move": total - clash, "would_skip_clash": clash})
    return pd.DataFrame(rows)


def apply_drift(conn, lookup: dict, drift: pd.DataFrame, company=None) -> pd.DataFrame:
    """Move the drifted facts. Returns one row per (tag, current concept) with moved / skipped counts."""
    results = []
    scope = _scope(company)
    for (tag, current), _ in drift.groupby(["tag", "current_concept"]):
        name, statement, label = lookup[tag]
        old = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"),
                           {"n": current}).fetchone()[0]
        new = _concept_id(conn, name, statement, label)
        params = {"old": old, "new": new, "tag": tag, **({"co": company} if company else {})}
        clash = conn.execute(text(f"""
            SELECT COUNT(*) FROM fact_value fv
            WHERE fv.concept_id = :old AND fv.raw_xbrl_tag = :tag {scope} {_CLASH}"""), params).scalar()
        moved = conn.execute(text(f"""
            UPDATE fact_value SET concept_id = :new
            WHERE value_id IN (
                SELECT fv.value_id FROM fact_value fv
                WHERE fv.concept_id = :old AND fv.raw_xbrl_tag = :tag {scope}
                  AND NOT EXISTS (SELECT 1 FROM fact_value b WHERE b.filing_id = fv.filing_id
                                  AND b.period_id = fv.period_id AND b.concept_id = :new))"""), params).rowcount
        if not company:      # the tag's mapping row follows only when the whole tag moved
            left = conn.execute(text("SELECT COUNT(*) FROM fact_value WHERE concept_id = :old AND raw_xbrl_tag = :tag"),
                                params).scalar()
            if left == 0:
                conn.execute(text("UPDATE concept_mapping SET concept_id = :new WHERE xbrl_tag = :tag AND concept_id = :old"),
                             params)
        results.append({"tag": tag, "from": current, "to": name, "moved": moved, "skipped_clash": clash})
    return pd.DataFrame(results)


def main():
    ap = argparse.ArgumentParser(description="Re-point stored facts to the concept the mapping file assigns to their tag")
    ap.add_argument("--company", default=None, help="only this company's facts")
    ap.add_argument("--tags", default=None, help="comma-separated tags to move")
    ap.add_argument("--all", action="store_true", help="move every drifted tag (still only with --apply)")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing changes")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (see .env)")
    engine = create_engine(url)
    lookup = load_lookup()
    with engine.connect() as conn:
        drift = find_drift(conn, lookup, args.company)
    if drift.empty:
        print("No drift: every stored fact already has the concept the mapping file gives its tag.")
        return
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(f"{len(drift)} (tag, concept, company) group(s) hold a concept the mapping file no longer assigns:")
    print(drift.to_string(index=False))
    chosen = drift
    if args.tags:
        wanted = {t.strip() for t in args.tags.split(",")}
        chosen = drift[drift["tag"].isin(wanted)]
    if not args.apply:
        with engine.connect() as conn:
            prev = preview_drift(conn, lookup, chosen, args.company)
        print("\nDry run (SELECTs only):")
        print(prev.to_string(index=False))
        print(f"would move {int(prev['would_move'].sum())} fact(s), skip {int(prev['would_skip_clash'].sum())} "
              f"clashing fact(s). Pass --tags a,b (or --all) with --apply to write.")
        return
    if not (args.tags or args.all):
        sys.exit("--apply needs --tags a,b or --all")
    with engine.begin() as conn:
        out = apply_drift(conn, lookup, chosen, args.company)
    print("\nApplied:")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
