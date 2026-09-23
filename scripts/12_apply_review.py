"""
Apply reviewed extension classifications to the mapping file.

After running 12_prep_company.py, extension tags needing human review
are written to data/mappings/REVIEW_extensions.yaml. Fill in the
'statement' field for each entry, then run this script to apply them.

Usage:
    python scripts/12_apply_review.py
    python scripts/12_apply_review.py --review data/mappings/REVIEW_extensions.yaml
"""
import argparse
import importlib.util
from pathlib import Path
import yaml

_ck_spec = importlib.util.spec_from_file_location("concept_keys", Path(__file__).parent / "concept_keys.py")
_ck = importlib.util.module_from_spec(_ck_spec)
_ck_spec.loader.exec_module(_ck)   # one naming rule for new concepts, shared - see concept_keys.py

ap = argparse.ArgumentParser()
ap.add_argument("--review", default="data/mappings/REVIEW_extensions.yaml")
ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
args = ap.parse_args()

if not Path(args.review).exists():
    print(f"Review file not found: {args.review}")
    print("Run 12_prep_company.py first to generate it.")
    raise SystemExit(1)

with open(args.review, encoding="utf-8") as f:
    review = yaml.safe_load(f)

with open(args.mapping, encoding="utf-8") as f:
    existing = yaml.safe_load(f)

entries = review.get("needs_review", [])
valid_stmts = {"income_statement", "balance_sheet", "cash_flow", "other"}

added = skipped_todo = skipped_invalid = 0
for entry in entries:
    stmt = entry.get("statement", "REVIEW")
    if stmt in ("REVIEW", "TODO", "", None):
        skipped_todo += 1
        continue
    if stmt not in valid_stmts:
        print(f"  INVALID statement '{stmt}' for {entry['xbrl_tag']} - skipping")
        skipped_invalid += 1
        continue

    key = _ck.unique_concept_key(existing, entry["suggested_key"], entry["xbrl_tag"])
    existing[stmt][key] = {
        "display_label": entry["display_label"],
        "xbrl_tags": [entry["xbrl_tag"]],
    }
    added += 1

with open(args.mapping, "w", encoding="utf-8") as f:
    yaml.dump(existing, f, allow_unicode=True, sort_keys=False,
              default_flow_style=False)

print(f"Applied {added} reviewed classifications to {args.mapping}")
if skipped_todo:
    print(f"Skipped {skipped_todo} entries still marked REVIEW/TODO - fill these in and rerun")
if skipped_invalid:
    print(f"Skipped {skipped_invalid} entries with invalid statement values")
print(f"\nTotal concepts in mapping: {sum(len(v) for v in existing.values())}")
