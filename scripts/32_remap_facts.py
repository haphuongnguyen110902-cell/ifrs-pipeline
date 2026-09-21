"""
Re-point stored facts to the concept the mapping NOW assigns to their XBRL tag.

Why: the mapping (data/mappings/ifrs_concepts_v0.yaml, plus the reviewed per-company overrides in
data/mappings/company_tag_overrides.yaml) is the record of "this tag is that concept", but fact_value stores the concept a
fact had when it was loaded. When a mapping is corrected, already-loaded facts keep the old concept until they are
re-pointed - a full re-parse of the filings would do it, but it needs Arelle and every raw package, while the answer is
already in the database: fact_value.raw_xbrl_tag is the original tag and `ifrs_concept` / `concept_mapping` say what it
was mapped to.

Found in the capex / D&A / payables audit: EssilorLuxottica, Kering, L'Oreal and Pernod Ricard print ONE combined
"purchase of PP&E and intangibles" line under extension tags that had been classified into one-off concepts the ratio
engine never reads (capex fell back to a flat 3% of revenue: Kering 2023 587M assumed vs 2,611M printed), and Puig
swapped two tags (its trade payables sit under the income-tax-liability tag).

Nothing is guessed or deleted: a fact moves only if its tag is mapped to a DIFFERENT concept (for that company, an
override wins); a fact whose (filing, period) already has a row under the target concept is skipped and reported, unless
the row in the way is itself moving (a swap resolves in a later pass); old concepts are left in place. Dry run by
default (SELECTs only); --apply is one transaction.

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
_LOADER = None


def _loader():
    """09_batch_load.py (its filename cannot be imported normally): the loader's own reading of the mappings."""
    global _LOADER
    if _LOADER is None:
        spec = importlib.util.spec_from_file_location("batch_load_09", Path(__file__).parent / "09_batch_load.py")
        _LOADER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_LOADER)
    return _LOADER


def load_lookup(path=MAPPING) -> dict:
    """{tag: (normalized_name, statement, display_label)}."""
    return _loader().load_mapping(str(path))


def load_overrides(path=None) -> dict:
    """{(company, tag): (normalized_name, statement, display_label)} - reviewed per-company corrections."""
    return _loader().load_overrides(path) if path else _loader().load_overrides()


def target_for(lookup: dict, overrides: dict, company, tag):
    """The concept a company's tag maps to: its reviewed override if it has one, else the global mapping, else None."""
    return (overrides or {}).get((company, tag)) or lookup.get(tag)


def find_drift(conn, lookup: dict, company=None, overrides=None) -> pd.DataFrame:
    """One row per (tag, current concept, company): how many stored facts carry a concept other than the one the
    mapping assigns to that company's tag."""
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
    df["target_concept"] = [(t[0] if (t := target_for(lookup, overrides, co, tag)) else None)
                            for tag, co in zip(df["tag"], df["company"])]
    drift = df[df["target_concept"].notna() & (df["target_concept"] != df["current_concept"])]
    return drift.sort_values(["company", "tag"]).reset_index(drop=True)


_SCOPE = ("AND fv.filing_id IN (SELECT f.filing_id FROM filing f JOIN company c ON c.company_id = f.company_id "
          "WHERE c.name = :co)")


def _concept_id(conn, name, statement=None, label=None, create=False):
    row = conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()
    if row or not create:
        return row[0] if row else None
    conn.execute(text("INSERT INTO ifrs_concept (normalized_name, statement, display_label) VALUES (:n, :s, :l)"),
                 {"n": name, "s": statement, "l": label})
    return conn.execute(text("SELECT concept_id FROM ifrs_concept WHERE normalized_name = :n"), {"n": name}).fetchone()[0]


def _rows(drift: pd.DataFrame, company=None):
    d = drift if company is None else drift[drift["company"] == company]
    return list(d.itertuples(index=False))


def _blockers(conn, old, new, tag, company):
    """(tag, n) of the facts already sitting under the target concept for the same filing+period as the facts to move."""
    rows = conn.execute(text(f"""
        SELECT b.raw_xbrl_tag, COUNT(*) FROM fact_value fv
        JOIN fact_value b ON b.filing_id = fv.filing_id AND b.period_id = fv.period_id AND b.concept_id = :new
        WHERE fv.concept_id = :old AND fv.raw_xbrl_tag = :tag {_SCOPE}
        GROUP BY b.raw_xbrl_tag"""), {"old": old, "new": new, "tag": tag, "co": company}).fetchall()
    return [(r[0], r[1]) for r in rows]


def preview_drift(conn, lookup: dict, drift: pd.DataFrame, company=None, overrides=None) -> pd.DataFrame:
    """What --apply would do, from SELECTs only, per (tag, current concept, company): facts that would move; facts that
    wait for another fact that is itself moving (a swap: resolved in a later pass); facts that stay because a fact
    that is NOT moving is in the way."""
    scheduled = {(r.tag, r.current_concept, r.company) for r in _rows(drift, company)}
    out = []
    for r in _rows(drift, company):
        name = target_for(lookup, overrides, r.company, r.tag)[0]
        old, new = _concept_id(conn, r.current_concept), _concept_id(conn, name)
        p = {"old": old, "new": new if new is not None else -1, "tag": r.tag, "co": r.company}
        total = conn.execute(text(f"SELECT COUNT(*) FROM fact_value fv WHERE fv.concept_id = :old "
                                  f"AND fv.raw_xbrl_tag = :tag {_SCOPE}"), p).scalar()
        chain = stay = 0
        if new is not None:
            for btag, n in _blockers(conn, old, new, r.tag, r.company):
                if (btag, name, r.company) in scheduled:
                    chain += n
                else:
                    stay += n
        out.append({"company": r.company, "tag": r.tag, "from": r.current_concept, "to": name,
                    "would_move": total - stay, "of_which_after_a_swap_partner": chain, "would_stay_clash": stay})
    return pd.DataFrame(out)


def apply_drift(conn, lookup: dict, drift: pd.DataFrame, company=None, overrides=None, max_passes=3) -> pd.DataFrame:
    """Move the drifted facts (up to `max_passes` passes, so a swap of two concepts resolves). Returns one row per
    (company, tag, current concept) with moved / still-blocked counts."""
    pending = _rows(drift, company)
    results = {}
    for _ in range(max_passes):
        retry, progressed = [], False
        for r in pending:
            name, statement, label = target_for(lookup, overrides, r.company, r.tag)
            old = _concept_id(conn, r.current_concept)
            new = _concept_id(conn, name, statement, label, create=True)
            p = {"old": old, "new": new, "tag": r.tag, "co": r.company}
            blocked = conn.execute(text(f"SELECT COUNT(*) FROM fact_value fv WHERE fv.concept_id = :old "
                                        f"AND fv.raw_xbrl_tag = :tag {_SCOPE} {_CLASH}"), p).scalar()
            moved = conn.execute(text(f"""
                UPDATE fact_value SET concept_id = :new
                WHERE value_id IN (
                    SELECT fv.value_id FROM fact_value fv
                    WHERE fv.concept_id = :old AND fv.raw_xbrl_tag = :tag {_SCOPE}
                      AND NOT EXISTS (SELECT 1 FROM fact_value b WHERE b.filing_id = fv.filing_id
                                      AND b.period_id = fv.period_id AND b.concept_id = :new))"""), p).rowcount
            rec = results.setdefault((r.company, r.tag, r.current_concept),
                                     {"company": r.company, "tag": r.tag, "from": r.current_concept, "to": name,
                                      "moved": 0, "still_blocked": 0})
            rec["moved"] += moved
            rec["still_blocked"] = blocked
            progressed = progressed or moved > 0
            if blocked:
                retry.append(r)
        pending = retry
        if not pending or not progressed:
            break
    # the GLOBAL tag -> concept row follows only when the tag moved by the global mapping and no fact is left behind
    for r in _rows(drift, company):
        if (overrides or {}).get((r.company, r.tag)) is not None:
            continue
        name = lookup[r.tag][0]
        old, new = _concept_id(conn, r.current_concept), _concept_id(conn, name)
        if old is None or new is None:
            continue
        left = conn.execute(text("SELECT COUNT(*) FROM fact_value WHERE concept_id = :old AND raw_xbrl_tag = :tag"),
                            {"old": old, "tag": r.tag}).scalar()
        if left == 0:
            conn.execute(text("UPDATE concept_mapping SET concept_id = :new WHERE xbrl_tag = :tag AND concept_id = :old"),
                         {"old": old, "new": new, "tag": r.tag})
    return pd.DataFrame(list(results.values()))


def main():
    ap = argparse.ArgumentParser(description="Re-point stored facts to the concept the mapping assigns to their tag")
    ap.add_argument("--company", default=None, help="only this company's facts")
    ap.add_argument("--tags", default=None, help="comma-separated tags to move")
    ap.add_argument("--all", action="store_true", help="move every drifted tag (still only with --apply)")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing changes")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")     # accented tags (el:...Dépréciations...) on a cp1252 console

    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (see .env)")
    engine = create_engine(url)
    lookup, overrides = load_lookup(), load_overrides()
    with engine.connect() as conn:
        drift = find_drift(conn, lookup, args.company, overrides)
    if drift.empty:
        print("No drift: every stored fact already has the concept the mapping gives its tag.")
        return
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(f"{len(drift)} (company, tag, concept) group(s) hold a concept the mapping no longer assigns "
          f"({len(overrides)} reviewed override(s) in force):")
    print(drift.to_string(index=False))
    chosen = drift
    if args.tags:
        wanted = {t.strip() for t in args.tags.split(",")}
        chosen = drift[drift["tag"].isin(wanted)]
    if not args.apply:
        with engine.connect() as conn:
            prev = preview_drift(conn, lookup, chosen, args.company, overrides)
        print("\nDry run (SELECTs only):")
        print(prev.to_string(index=False))
        print(f"would move {int(prev['would_move'].sum())} fact(s); {int(prev['would_stay_clash'].sum())} would stay "
              f"because a fact that is not moving is in the way. Pass --tags a,b (or --all) with --apply to write.")
        return
    if not (args.tags or args.all):
        sys.exit("--apply needs --tags a,b or --all")
    with engine.begin() as conn:
        out = apply_drift(conn, lookup, chosen, args.company, overrides)
    print("\nApplied:")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
