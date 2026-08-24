"""
Batch prep: auto-classify ALL companies in one pass.

Instead of running 12_prep_company.py once per company and reviewing
each REVIEW file separately, this script:
  1. Scans every zip in data/raw/
  2. Auto-classifies all standard ifrs-full: tags across all companies
  3. Pools ALL extension tags needing review into ONE combined file
  4. You review once, apply once, then load everything

Usage:
    python scripts/13_batch_prep.py --dry-run   # see what would happen
    python scripts/13_batch_prep.py             # run for real
    python scripts/13_batch_prep.py --only danone.zip essity.zip  # specific zips
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

import yaml
from arelle import Cntlr, PackageManager, XbrlConst

# reuse classification logic from 12_prep_company.py
ROLE_NUMBER_TO_STATEMENT = {
    "2": "balance_sheet", "3": "income_statement", "4": "other",
    "5": "cash_flow", "6": "other", "7": "other", "8": "other", "9": "other",
}
ROLE_KEYWORDS = [
    ("cash_flow", ["cash flow", "cashflow", "flux de tr", "kassaflöde"]),
    ("balance_sheet", ["financial position", "balance sheet", "bilan",
                       "balansräkning", "situazione patrimoniale"]),
    ("other", ["comprehensive income", "changes in equity", "résultat global",
               "variation des capitaux", "totalresultat"]),
    ("income_statement", ["profit or loss", "income statement", "compte de r",
                          "resultaträkning", "conto economico"]),
]
CONCEPT_KEYWORDS = [
    ("cash_flow", ["cashflow", "cashflows", "proceedsfrom", "paymentsfor",
                   "paymentsto", "paymentsof", "purchaseof", "repaymentsof",
                   "dividendspaid", "interestpaid", "taxespaid", "adjustmentsfor"]),
    ("other", ["othercomprehensiveincome", "reclassificationadjustments",
               "incometaxrelatingto", "increasedecreasethrough"]),
]


def statement_from_role(definition):
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
    return "", ""


def statement_from_name(qname, label):
    h = (qname.split(":")[-1] + " " + (label or "")).lower().replace(" ", "")
    for stmt, keywords in CONCEPT_KEYWORDS:
        for kw in keywords:
            if kw in h:
                return stmt, f"name keyword '{kw}' (LOW_CONF)"
    return "", ""


def load_filing(filepath, package_zip=None):
    c = Cntlr.Cntlr(logFileName=None)
    if package_zip:
        PackageManager.addPackage(c, package_zip)
        PackageManager.rebuildRemappings(c)
    return c, c.modelManager.load(filepath)


def open_zip(zip_path):
    c, m = load_filing(zip_path, package_zip=zip_path)
    if m is None or not m.facts:
        if c:
            c.close()
        with zipfile.ZipFile(zip_path) as z:
            cands = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if cands:
            c, m = load_filing(f"{zip_path}/{cands[0]}", package_zip=zip_path)
    return c, m


def build_pres_map(model):
    result = {}
    relset = model.relationshipSet(XbrlConst.parentChild)
    for linkrole in relset.linkRoleUris:
        rts = model.roleTypes.get(linkrole, [])
        defn = rts[0].definition if rts else linkrole
        stmt, reason = statement_from_role(defn)
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
            if root and getattr(root, "qname", None):
                qn = str(root.qname)
                if qn not in result:
                    result[qn] = (stmt, "presentation linkbase root")
    return result


def slugify(name):
    key = "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")
    return key[:60] + "_etc" if len(key) > 60 else key


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--review-out", default="data/mappings/REVIEW_extensions.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="+", help="Only process these zip filenames")
    args = ap.parse_args()

    with open(args.mapping, encoding="utf-8") as f:
        existing = yaml.safe_load(f)
    existing_tags = set()
    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])
    print(f"Existing mapping: {len(existing_tags)} tags\n")

    raw_dir = Path(args.raw_dir)
    if args.only:
        zips = [raw_dir / z for z in args.only if (raw_dir / z).exists()]
    else:
        zips = sorted(raw_dir.glob("*.zip"))

    # pool results across all companies
    all_auto = {}       # tag -> entry (standard or clearly classifiable extension)
    all_review = {}     # tag -> entry + which companies use it
    per_company = []

    for zip_path in zips:
        company = zip_path.stem
        print(f"Scanning {company}...")
        try:
            c, model = open_zip(str(zip_path))
        except Exception as ex:
            print(f"  FAILED: {ex}")
            continue
        if model is None:
            print(f"  Could not parse")
            c.close() if c else None
            continue

        pres_map = build_pres_map(model)
        auto_n = review_n = skip_n = 0

        for fact in model.facts:
            concept = fact.concept
            if concept is None or not concept.isNumeric:
                continue
            qn = str(fact.qname)
            if qn in existing_tags or qn in all_auto or qn in all_review:
                skip_n += 1
                continue

            is_std = qn.startswith("ifrs-full:")
            label = concept.label() or qn.split(":")[-1]
            key = slugify(qn.split(":")[-1])

            stmt, reason = pres_map.get(qn, ("", ""))
            if not stmt:
                if concept.periodType == "instant":
                    stmt, reason = "balance_sheet", "periodType=instant"
                else:
                    stmt, reason = statement_from_name(qn, label)

            entry = {
                "xbrl_tag": qn,
                "suggested_key": key,
                "display_label": label,
                "statement": stmt or "REVIEW",
                "balance": concept.balance or "n/a",
                "reason": reason,
                "used_by": [company],
            }

            if is_std and stmt and stmt != "REVIEW":
                all_auto[qn] = entry
                auto_n += 1
            elif not is_std and stmt and "AUTHORITATIVE" in reason:
                all_auto[qn] = entry
                auto_n += 1
            else:
                entry["statement"] = "REVIEW"
                all_review[qn] = entry
                review_n += 1

        c.close()
        per_company.append((company, auto_n, review_n))
        print(f"  auto={auto_n}  review={review_n}  already_known={skip_n}")

    print(f"\n{'='*60}")
    print(f"TOTAL across all companies:")
    print(f"  Auto-classify (no human needed): {len(all_auto)}")
    print(f"  Need review (extension tags):    {len(all_review)}")

    if args.dry_run:
        print("\nDRY RUN - nothing written.")
        print("\nExtension tags needing review:")
        for qn, entry in list(all_review.items())[:10]:
            print(f"  {qn}: {entry['display_label']}")
        if len(all_review) > 10:
            print(f"  ... and {len(all_review) - 10} more")
    else:
        # add auto-classified to mapping
        added = 0
        for qn, entry in all_auto.items():
            stmt = entry["statement"]
            key = entry["suggested_key"]
            while key in existing.get(stmt, {}):
                key += "_x"
            existing.setdefault(stmt, {})[key] = {
                "display_label": entry["display_label"],
                "xbrl_tags": [qn],
            }
            added += 1

        with open(args.mapping, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False)
        print(f"\nAdded {added} concepts to mapping automatically")

        # write combined review file
        review_data = {
            "_instructions": (
                "Fill in 'statement' for each entry: "
                "income_statement / balance_sheet / cash_flow / other. "
                "When done: python scripts/12_apply_review.py"
            ),
            "needs_review": list(all_review.values()),
        }
        with open(args.review_out, "w", encoding="utf-8") as f:
            yaml.dump(review_data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False)
        print(f"Wrote {len(all_review)} extension tags to {args.review_out}")
        print(f"\n*** Open {args.review_out}, fill in statements, then run:")
        print("*** python scripts/12_apply_review.py")
        print("*** Then reload ALL companies:")
        print("*** python scripts/09_batch_load.py --reset-facts")
