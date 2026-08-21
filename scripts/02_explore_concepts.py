"""
Week 1-2 exploration script.

Goal: look at the FULL set of facts extracted (not just the first 20),
answer three questions:
  1. How many DISTINCT concepts (line items) are there?
  2. Which concepts show up for MULTIPLE periods (years)?
  3. Which concepts are company-specific extensions (loreal:...) vs
     standard IFRS taxonomy (ifrs-full:...)?

Usage:
    python scripts/02_explore_concepts.py data/raw/loreal_2025_facts.csv
"""
import sys
from pathlib import Path

import pandas as pd

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/02_explore_concepts.py <path_to_facts_csv>")
        sys.exit(1)

    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    df = pd.read_csv(path)
    print(f"Total facts: {len(df)}")
    print(f"Distinct concepts: {df['concept_qname'].nunique()}")
    print()

    # split standard IFRS tags from company-specific extension tags
    df["is_extension"] = ~df["concept_qname"].str.startswith("ifrs-full:")
    n_ext = df["is_extension"].sum()
    print(f"Standard ifrs-full: concepts: {len(df) - n_ext} facts")
    print(f"Company-specific extension concepts: {n_ext} facts")
    print()

    # how many periods does each concept appear in - concepts appearing
    # 3x are likely a clean 3-year comparative (this year, last year, year before)
    counts = df.groupby("concept_qname").size().sort_values(ascending=False)
    print("Concepts by how many periods/rows they appear in:")
    print(counts.value_counts().sort_index(ascending=False))
    print()

    print("Top 30 most-repeated concepts (likely your core statement line items):")
    print(counts.head(30))

    out_path = Path(path).parent / (Path(path).stem + "_concept_summary.csv")
    counts.to_frame("num_facts").to_csv(out_path)
    print(f"\nSaved concept summary to {out_path}")
