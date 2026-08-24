"""Find which facts in a filing conflict with the UNIQUE constraint."""
import ast
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from datetime import timedelta

load_dotenv()
e = create_engine(os.environ["DATABASE_URL"])

# Load mapping
with open("data/mappings/ifrs_concepts_v0.yaml") as f:
    mapping = yaml.safe_load(f)
tag_lookup = {}
for stmt, concepts in mapping.items():
    for name, info in concepts.items():
        for tag in info["xbrl_tags"]:
            tag_lookup[tag] = name

# Load facts CSV
df = pd.read_csv("data/raw/Amplifon_2025_facts.csv")
df = df[df["dimensions"].apply(
    lambda d: len(ast.literal_eval(d)) == 0 if isinstance(d, str) else len(d) == 0
)]

# Simulate what the loader does: build (period_end, period_type, normalized_name)
# and find duplicates
seen = defaultdict(list)
for _, row in df.iterrows():
    tag = row["concept_qname"]
    if tag not in tag_lookup:
        continue
    name = tag_lookup[tag]
    p_start = row.get("period_start")
    p_end = row.get("period_end")
    ptype = "instant" if pd.isna(p_start) else "duration"
    key = (name, ptype, str(p_end))
    seen[key].append(tag)

conflicts = {k: v for k, v in seen.items() if len(v) > 1}
print(f"Conflicting (concept, period) combinations: {len(conflicts)}\n")
for (name, ptype, end), tags in sorted(conflicts.items()):
    print(f"  {name}")
    print(f"    period: {ptype} ending {end}")
    for t in tags:
        print(f"    tag: {t}")
    print()
