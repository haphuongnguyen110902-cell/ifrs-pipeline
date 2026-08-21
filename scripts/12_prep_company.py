"""
Pre-load auto-classifier for new companies.

This is the correct workflow for adding a new company:

    python scripts/12_prep_company.py --zip data/raw/newco.zip

It will:
  1. Parse the filing with Arelle
  2. Auto-classify every concept using the taxonomy (presentation linkbase,
     periodType, balance attribute, official labels)
  3. For standard ifrs-full: tags -> add to mapping automatically, no review
  4. For company extension tags -> write to a REVIEW file for human decision
  5. Show a clear summary of what was done and what needs your attention

After running this, you can load immediately:
    python scripts/09_batch_load.py --only <stem>

CLASSIFICATION RULES
--------------------
Standard ifrs-full: tags: classified automatically from the taxonomy.
The taxonomy is authoritative for these - no human judgment needed.

Company extension tags (loreal:, LVM:, essi:, etc.):
  - If the presentation linkbase places them clearly on one statement -> auto
  - If the label and name strongly suggest a statement -> auto with LOW_CONF flag
  - Otherwise -> written to REVIEW file, NOT loaded until human classifies

This means a new company with only standard tags needs zero manual work.
A company with many extensions (like L'Oreal or LVMH) needs ~15 minutes
of review for just the extension tags, not the full 200+ concepts.
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

import yaml
from arelle import Cntlr, PackageManager, XbrlConst


# ---------------------------------------------------------------- taxonomy classification
# (reused from 10_auto_classify.py)

ROLE_NUMBER_TO_STATEMENT = {
    "2": "balance_sheet",
    "3": "income_statement",
    "4": "other",
    "5": "cash_flow",
    "6": "other",
    "7": "other",
    "8": "other",
    "9": "other",
}

ROLE_KEYWORDS = [
    ("cash_flow", ["cash flow", "cashflow", "flux de tr", "kassaflöde", "flussi finanziari"]),
    ("balance_sheet", ["financial position", "balance sheet", "bilan", "situation financi",
                       "balansräkning", "situazione patrimoniale"]),
    ("other", ["comprehensive income", "changes in equity", "résultat global",
               "variation des capitaux", "totalresultat", "conto economico complessivo"]),
    ("income_statement", ["profit or loss", "income statement", "compte de r",
                          "resultaträkning", "conto economico"]),
]

CONCEPT_KEYWORDS = [
    ("cash_flow", ["cashflow", "cashflows", "cashoutflow", "cashinflow",
                   "proceedsfrom", "paymentsfor", "paymentsto", "paymentsof",
                   "purchaseof", "repaymentsof", "dividendspaid", "interestpaid",
                   "interestreceived", "taxespaid", "adjustmentsfor"]),
    ("other", ["othercomprehensiveincome", "reclassificationadjustments",
               "incometaxrelatingto", "increasedecreasethrough"]),
]


def statement_from_role(definition: str):
    if not definition:
        return "", "no role definition"
    m = re.search(r"\[(\d)\d{5}\]", definition)
    if m:
        digit = m.group(1)
        stmt = ROLE_NUMBER_TO_STATEMENT.get(digit)
        if stmt:
            return stmt, f"IFRS role [{digit}xxxxx] - AUTHORITATIVE"
    low = definition.lower()
    for stmt, keywords in ROLE_KEYWORDS:
        for kw in keywords:
            if kw in low:
                return stmt, f"role keyword '{kw}'"
    return "", "role definition not recognised"


def statement_from_concept_name(qname: str, label: str):
    haystack = (qname.split(":")[-1] + " " + (label or "")).lower().replace(" ", "")
    for stmt, keywords in CONCEPT_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return stmt, f"name keyword '{kw}' (LOW_CONF)"
    return "", ""


def load_filing(filepath, package_zip=None):
    controller = Cntlr.Cntlr(logFileName=None)
    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)
    return controller, controller.modelManager.load(filepath)


def open_filing(zip_path: str):
    controller, model = load_filing(zip_path, package_zip=zip_path)
    if model is None or not model.facts:
        if controller:
            controller.close()
        with zipfile.ZipFile(zip_path) as z:
            cands = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if cands:
            controller, model = load_filing(f"{zip_path}/{cands[0]}", package_zip=zip_path)
    return controller, model


def build_pres_map(model) -> dict:
    result = {}
    relset = model.relationshipSet(XbrlConst.parentChild)
    for linkrole in relset.linkRoleUris:
        roleTypes = model.roleTypes.get(linkrole, [])
        definition = roleTypes[0].definition if roleTypes else linkrole
        stmt, reason = statement_from_role(definition)
        if not stmt:
            continue
        role_rels = model.relationshipSet(XbrlConst.parentChild, linkrole)
        for rel in role_rels.modelRelationships:
            for concept in (rel.fromModelObject, rel.toModelObject):
                if concept is None or not getattr(concept, "qname", None):
                    continue
                qn = str(concept.qname)
                if qn not in result or result[qn][0] == "other":
                    result[qn] = (stmt, reason)
        for root in role_rels.rootConcepts:
            if root is not None and getattr(root, "qname", None):
                qn = str(root.qname)
                if qn not in result:
                    result[qn] = (stmt, "presentation linkbase root")
    return result


def slugify(name: str) -> str:
    key = "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")
    return key[:60] + "_etc" if len(key) > 60 else key


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="Path to the filing zip")
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--review-out", default="data/mappings/REVIEW_extensions.yaml",
                    help="Where to write extension tags needing human review")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be added without writing anything")
    args = ap.parse_args()

    if not Path(args.zip).exists():
        print(f"File not found: {args.zip}")
        sys.exit(1)

    # load existing mapping
    with open(args.mapping, encoding="utf-8") as f:
        existing = yaml.safe_load(f)
    existing_tags = set()
    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])

    print(f"Existing mapping: {len(existing_tags)} tags")
    print(f"Parsing {args.zip} ...")

    controller, model = open_filing(args.zip)
    if model is None:
        print("Failed to parse filing.")
        sys.exit(1)

    pres_map = build_pres_map(model)

    auto_standard = {}    # ifrs-full: tags, auto-classified
    auto_extension = {}   # extension tags, clearly classifiable
    review_needed = {}    # extension tags needing human review
    already_covered = 0

    for fact in model.facts:
        concept = fact.concept
        if concept is None or not concept.isNumeric:
            continue
        qn = str(fact.qname)
        if qn in existing_tags:
            already_covered += 1
            continue
        if qn in auto_standard or qn in auto_extension or qn in review_needed:
            continue

        is_standard = qn.startswith("ifrs-full:")
        label = concept.label() or qn.split(":")[-1]
        key = slugify(qn.split(":")[-1])

        # classify
        stmt, reason = pres_map.get(qn, ("", ""))
        if not stmt:
            if concept.periodType == "instant":
                stmt, reason = "balance_sheet", "periodType=instant - AUTHORITATIVE"
            else:
                stmt, reason = statement_from_concept_name(qn, label)

        entry = {
            "xbrl_tag": qn,
            "suggested_key": key,
            "display_label": label,
            "statement": stmt or "REVIEW",
            "balance": concept.balance or "n/a",
            "reason": reason,
        }

        if is_standard and stmt and stmt != "REVIEW":
            auto_standard[qn] = entry
        elif not is_standard and stmt and "AUTHORITATIVE" in reason:
            auto_extension[qn] = entry
        elif not is_standard:
            entry["statement"] = stmt or "REVIEW"
            review_needed[qn] = entry
        else:
            # standard tag but couldn't classify - rare, treat as review
            review_needed[qn] = entry

    controller.close()

    print(f"\nResults:")
    print(f"  Already covered by mapping:     {already_covered}")
    print(f"  Standard tags - auto-classify:  {len(auto_standard)}")
    print(f"  Extension tags - auto-classify: {len(auto_extension)}")
    print(f"  Extension tags - NEED REVIEW:   {len(review_needed)}")

    if not args.dry_run:
        # add auto-classified tags to mapping
        added = 0
        for qn, entry in {**auto_standard, **auto_extension}.items():
            stmt = entry["statement"]
            key = entry["suggested_key"]
            while key in existing[stmt]:
                key += "_x"
            existing[stmt][key] = {
                "display_label": entry["display_label"],
                "xbrl_tags": [qn],
            }
            added += 1

        with open(args.mapping, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False)
        print(f"\nAdded {added} concepts to {args.mapping}")

        # write review file for extensions needing human decision
        if review_needed:
            review = {
                "_instructions": (
                    "Fill in 'statement' for each entry: "
                    "income_statement / balance_sheet / cash_flow / other. "
                    "Then run: python scripts/12_apply_review.py"
                ),
                "needs_review": list(review_needed.values())
            }
            with open(args.review_out, "w", encoding="utf-8") as f:
                yaml.dump(review, f, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
            print(f"Wrote {len(review_needed)} extension tags to {args.review_out}")
            print(f"\n*** ACTION REQUIRED: open {args.review_out}")
            print("*** Fill in 'statement' for each entry, then run:")
            print("***   python scripts/12_apply_review.py")
            print("*** Then load normally:")
            print(f"***   python scripts/09_batch_load.py --only {Path(args.zip).stem}")
        else:
            print("\nNo extensions need review - ready to load immediately:")
            print(f"  python scripts/09_batch_load.py --only {Path(args.zip).stem}")
    else:
        print("\nDRY RUN - no files written.")
        if review_needed:
            print(f"\nExtension tags needing review ({len(review_needed)}):")
            for qn, entry in list(review_needed.items())[:10]:
                print(f"  {qn}")
                print(f"    label: {entry['display_label']}")
                print(f"    reason: {entry['reason'] or 'no classification found'}")
            if len(review_needed) > 10:
                print(f"  ... and {len(review_needed) - 10} more")
