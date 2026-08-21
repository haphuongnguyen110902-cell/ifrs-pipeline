"""
Week 3 script: build a DRAFT semantic mapping from real data.

Goal: instead of guessing which XBRL concepts matter, take the concepts
that actually appeared exactly 3 times (clean 3-year, no dimensional
breakdown) in your parsed filing, and generate a starting YAML mapping
file. You'll still need to review and label each one by hand - this
script just saves you from typing out 100 raw tag names from scratch,
and makes sure you don't miss any.

Usage:
    python scripts/03_build_mapping_draft.py data/raw/loreal_2025_facts.csv
"""
import sys
from pathlib import Path

import pandas as pd
import yaml

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/03_build_mapping_draft.py <path_to_facts_csv>")
        sys.exit(1)

    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    df = pd.read_csv(path)

    # keep only concepts that appear exactly 3 times: clean 3-year data,
    # no dimensional breakdown to untangle - these are the safe starting set
    counts = df.groupby("concept_qname").size()
    clean_concepts = sorted(counts[counts == 3].index.tolist())

    print(f"Found {len(clean_concepts)} clean concepts (exactly 3 facts each)")

    # build a draft YAML structure - human still fills in display_label
    # and which statement (income_statement/balance_sheet/cash_flow) each
    # belongs to, since that requires judgment the data alone can't give
    draft = {"unmapped_concepts_to_review": []}
    for concept in clean_concepts:
        short_name = concept.split(":")[-1]  # strip the "ifrs-full:" or "loreal:" prefix
        draft["unmapped_concepts_to_review"].append({
            "xbrl_tag": concept,
            "suggested_key": "".join(
                ["_" + c.lower() if c.isupper() else c for c in short_name]
            ).lstrip("_"),
            "statement": "TODO",  # you fill this: income_statement / balance_sheet / cash_flow
            "display_label": "TODO",
        })

    out_path = Path("data/mappings/draft_mapping.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(draft, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"Saved draft mapping to {out_path}")
    print("\nOpen that file and, for each entry, fill in:")
    print("  - statement: income_statement, balance_sheet, or cash_flow")
    print("  - display_label: a human-readable name")
    print("Delete any entries that are notes/disclosures you don't need for the core statements.")
