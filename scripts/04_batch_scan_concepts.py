"""
Batch concept scanner.

Goal: parse EVERY zip in data/raw/ (skipping any already-parsed CSVs),
pool all their concepts together, and report the UNION of concepts not
yet covered by ifrs_concepts_v0.yaml - in one pass, instead of reviewing
one company's new concepts at a time.

Usage:
    python scripts/04_batch_scan_concepts.py
"""
import sys
from pathlib import Path

import pandas as pd
import yaml
from arelle import Cntlr, PackageManager


def load_filing(filepath, package_zip=None):
    controller = Cntlr.Cntlr(logFileName="logToPrint")
    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)
    model_xbrl = controller.modelManager.load(filepath)
    return controller, model_xbrl


def dump_facts(model_xbrl):
    rows = []
    for fact in model_xbrl.facts:
        if fact.concept is None or not fact.concept.isNumeric:
            continue
        context = fact.context
        rows.append({
            "concept_qname": str(fact.qname),
            "value": fact.value,
            "context_id": context.id if context is not None else None,
            "period_start": getattr(context, "startDatetime", None),
            "period_end": getattr(context, "endDatetime", None) or getattr(context, "instantDatetime", None),
            "unit": str(fact.unit.value) if fact.unit is not None else None,
            "decimals": fact.decimals,
            "dimensions": [str(d) for d in context.qnameDims.keys()] if context is not None and context.qnameDims else [],
        })
    return pd.DataFrame(rows)


def parse_one(zip_path: str) -> pd.DataFrame:
    """Parse a filing, quietly, with the same 0-facts retry logic as
    script 01 (registers the taxonomy package, retries against the
    entry point xhtml if the direct zip load yields nothing)."""
    import zipfile
    controller, model_xbrl = load_filing(zip_path, package_zip=zip_path)
    df = dump_facts(model_xbrl)
    if len(df) == 0:
        controller.close()
        with zipfile.ZipFile(zip_path) as z:
            candidates = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if candidates:
            retry_path = f"{zip_path}/{candidates[0]}"
            controller, model_xbrl = load_filing(retry_path, package_zip=zip_path)
            df = dump_facts(model_xbrl)
    controller.close()
    return df


if __name__ == "__main__":
    raw_dir = Path("data/raw")
    zips = sorted(raw_dir.glob("*.zip"))
    if not zips:
        print("No zip files found in data/raw/")
        sys.exit(1)

    with open("data/mappings/ifrs_concepts_v0.yaml") as f:
        existing = yaml.safe_load(f)
    existing_tags = set()
    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])

    print(f"Found {len(zips)} zip files. Already-mapped tags: {len(existing_tags)}\n")

    all_new_concepts = {}  # tag -> set of companies using it
    per_company_summary = []

    for zip_path in zips:
        company = zip_path.stem
        print(f"Parsing {company}...")
        try:
            df = parse_one(str(zip_path))
        except Exception as e:
            print(f"  FAILED: {e}")
            per_company_summary.append((company, "parse failed", 0, 0))
            continue

        if len(df) == 0:
            print(f"  0 facts extracted - skipping")
            per_company_summary.append((company, "0 facts", 0, 0))
            continue

        # keep only clean, non-dimensional, 3x-appearing concepts (same
        # filter used throughout this project for the primary statements)
        df_clean = df[df["dimensions"].apply(lambda d: len(d) == 0)]
        counts = df_clean.groupby("concept_qname").size()
        clean_tags = set(counts[counts == 3].index)

        new_for_this_company = clean_tags - existing_tags
        for tag in new_for_this_company:
            all_new_concepts.setdefault(tag, set()).add(company)

        per_company_summary.append((company, "OK", len(df), len(new_for_this_company)))
        print(f"  {len(df)} facts, {len(new_for_this_company)} new unmapped concepts")

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for company, status, n_facts, n_new in per_company_summary:
        print(f"{company:30s} {status:15s} facts={n_facts:5d} new_concepts={n_new}")

    print(f"\nTotal DISTINCT new concepts across all companies: {len(all_new_concepts)}")

    # write out a pooled draft, one entry per distinct new tag, noting
    # which companies use it (useful context when classifying)
    draft = {"unmapped_concepts_to_review": []}
    for tag in sorted(all_new_concepts):
        short = tag.split(":")[-1]
        key = "".join(["_" + c.lower() if c.isupper() else c for c in short]).lstrip("_")
        companies = sorted(all_new_concepts[tag])
        draft["unmapped_concepts_to_review"].append({
            "xbrl_tag": tag,
            "suggested_key": key,
            "used_by": companies,
        })

    out_path = Path("data/mappings/pooled_new_concepts.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(draft, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"\nSaved pooled draft to {out_path}")
    print("Open that file, copy its contents, and send it back for classification -")
    print("this covers every remaining company in ONE batch instead of one at a time.")
