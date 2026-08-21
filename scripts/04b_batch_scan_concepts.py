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
import zipfile
from pathlib import Path

import pandas as pd
import yaml
from arelle import Cntlr, PackageManager


# ---------------------------------------------------------------- parsing

def load_filing(filepath, package_zip=None):
    """Load an ESEF filing with Arelle."""
    controller = Cntlr.Cntlr(logFileName=None)

    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)

    model_xbrl = controller.modelManager.load(filepath)

    return controller, model_xbrl


def dump_facts(model_xbrl):
    """Extract all numeric XBRL facts from the filing."""
    rows = []

    for fact in model_xbrl.facts:
        if fact.concept is None or not fact.concept.isNumeric:
            continue

        context = fact.context

        rows.append({
            "concept_qname": str(fact.qname),
            "value": fact.value,
            "context_id": context.id if context is not None else None,
            "period_start": (
                getattr(context, "startDatetime", None)
                if context is not None
                else None
            ),
            "period_end": (
                getattr(context, "endDatetime", None)
                or getattr(context, "instantDatetime", None)
                if context is not None
                else None
            ),
            "unit": (
                str(fact.unit.value)
                if fact.unit is not None
                else None
            ),
            "decimals": fact.decimals,
            "dimensions": (
                [str(d) for d in context.qnameDims.keys()]
                if context is not None and context.qnameDims
                else []
            ),
        })

    return pd.DataFrame(rows)


def parse_one(zip_path: str) -> pd.DataFrame:
    """
    Parse an ESEF filing using the same logic as 09_batch_load.py.

    First try loading the ZIP directly. If Arelle returns no facts,
    find an XHTML/HTML entry point inside the ZIP and retry.
    """

    controller, model_xbrl = load_filing(
        zip_path,
        package_zip=zip_path
    )

    df = (
        dump_facts(model_xbrl)
        if model_xbrl is not None
        else pd.DataFrame()
    )

    # Retry through XHTML/HTML entry point if direct ZIP load gave
    # no numeric facts.
    if len(df) == 0:
        controller.close()

        with zipfile.ZipFile(zip_path) as z:
            candidates = [
                n
                for n in z.namelist()
                if n.lower().endswith(
                    (".xhtml", ".html", ".htm")
                )
            ]

        if candidates:
            retry_path = f"{zip_path}/{candidates[0]}"

            controller, model_xbrl = load_filing(
                retry_path,
                package_zip=zip_path
            )

            df = (
                dump_facts(model_xbrl)
                if model_xbrl is not None
                else pd.DataFrame()
            )

    controller.close()

    return df


# ---------------------------------------------------------------- main

if __name__ == "__main__":

    raw_dir = Path("data/raw")

    zips = sorted(raw_dir.glob("*.zip"))

    if not zips:
        print("No zip files found in data/raw/")
        sys.exit(1)

    # ------------------------------------------------------------
    # Load existing mapping
    # ------------------------------------------------------------

    with open(
        "data/mappings/ifrs_concepts_v0.yaml",
        encoding="utf-8"
    ) as f:
        existing = yaml.safe_load(f)

    existing_tags = set()

    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])

    print(
        f"Found {len(zips)} zip files. "
        f"Already-mapped tags: {len(existing_tags)}\n"
    )

    # tag -> companies using it
    all_new_concepts = {}

    per_company_summary = []

    # ------------------------------------------------------------
    # Parse every filing
    # ------------------------------------------------------------

    for zip_path in zips:

        company = zip_path.stem

        print(f"Parsing {company}...")

        try:
            df = parse_one(str(zip_path))

        except Exception as e:
            print(f"  FAILED: {e}")

            per_company_summary.append(
                (company, "parse failed", 0, 0)
            )

            continue

        if len(df) == 0:

            print("  0 facts extracted - skipping")

            per_company_summary.append(
                (company, "0 facts", 0, 0)
            )

            continue

        # --------------------------------------------------------
        # Keep clean, non-dimensional concepts.
        #
        # IMPORTANT:
        # We do NOT require exactly 3 occurrences anymore.
        #
        # A legitimate XBRL concept can appear once, twice,
        # three times, or many times depending on the filing.
        # --------------------------------------------------------

        df_clean = df[
            df["dimensions"].apply(
                lambda d: len(d) == 0
            )
        ]

        clean_tags = set(
            df_clean["concept_qname"].unique()
        )

        # Concepts present in this filing but absent from our
        # current IFRS mapping.
        new_for_this_company = (
            clean_tags - existing_tags
        )

        # Pool them across companies.
        for tag in new_for_this_company:

            all_new_concepts.setdefault(
                tag,
                set()
            ).add(company)

        per_company_summary.append(
            (
                company,
                "OK",
                len(df),
                len(new_for_this_company)
            )
        )

        print(
            f"  {len(df)} facts, "
            f"{len(new_for_this_company)} new unmapped concepts"
        )

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    print(
        f"\n{'=' * 70}\n"
        f"SUMMARY\n"
        f"{'=' * 70}"
    )

    for company, status, n_facts, n_new in per_company_summary:

        print(
            f"{company:30s} "
            f"{status:15s} "
            f"facts={n_facts:5d} "
            f"new_concepts={n_new}"
        )

    print(
        f"\nTotal DISTINCT new concepts across all companies: "
        f"{len(all_new_concepts)}"
    )

    # ------------------------------------------------------------
    # Write pooled YAML
    # ------------------------------------------------------------

    draft = {
        "unmapped_concepts_to_review": []
    }

    for tag in sorted(all_new_concepts):

        short = tag.split(":")[-1]

        key = "".join(
            [
                "_" + c.lower()
                if c.isupper()
                else c
                for c in short
            ]
        ).lstrip("_")

        companies = sorted(
            all_new_concepts[tag]
        )

        draft["unmapped_concepts_to_review"].append(
            {
                "xbrl_tag": tag,
                "suggested_key": key,
                "used_by": companies,
            }
        )

    out_path = Path(
        "data/mappings/pooled_new_concepts.yaml"
    )

    with open(
        out_path,
        "w",
        encoding="utf-8"
    ) as f:

        yaml.dump(
            draft,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False
        )

    print(
        f"\nSaved pooled draft to {out_path}"
    )

    print(
        "Open that file, copy its contents, "
        "and send it back for classification -"
    )

    print(
        "this covers every remaining company in ONE batch "
        "instead of one at a time."
    )