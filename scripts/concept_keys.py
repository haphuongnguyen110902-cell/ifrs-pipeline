"""
scripts/concept_keys.py

One rule for naming a new concept in data/mappings/ifrs_concepts_v0.yaml, shared by every script that adds one
(12_prep_company.py, 12_apply_review.py, 13_batch_prep.py - loaded with importlib like the other scripts).

The old rule appended "_x" while a name was taken WITHIN THE SAME STATEMENT. It produced the 69 "_x" / "_x_x"
near-duplicates removed on 2026-09-22, and it could give a new tag a name another statement already used: concept names
are unique across the whole ifrs_concept table, so the loader would silently file the new tag under that other concept.
"""
import re


def taken_keys(existing: dict) -> set:
    """Every concept name already used, in any statement."""
    return {name for concepts in existing.values() if isinstance(concepts, dict) for name in concepts}


def clean_key(key: str) -> str:
    """Only a-z, 0-9 and single underscores: EssilorLuxottica's tag "el:AutresReserves." once became the concept name
    "autres_reserves." and "el:ChangesInOtherNon-FinancialAssets" became "changes_in_other_non-_financial_assets"."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", key.lower())).strip("_")


def unique_concept_key(existing: dict, key: str, tag: str) -> str:
    """`key` (cleaned) if no statement uses it yet; otherwise key + the tag's namespace prefix (el, essi, ifrs, ...),
    then a number - a name that says whose line it is instead of a stack of "_x"."""
    key = clean_key(key)
    used = taken_keys(existing)
    if key not in used:
        return key
    prefix = re.sub(r"[^a-z0-9]", "", tag.split(":")[0].lower()) or "ext"
    candidate, n = f"{key}_{prefix}", 2
    while candidate in used:
        candidate, n = f"{key}_{prefix}_{n}", n + 1
    return candidate
