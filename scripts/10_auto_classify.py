"""
Auto-classify concepts from the taxonomy itself.

WHY THIS EXISTS
We hand-classified ~300 concepts by reading tag names and guessing which
statement they belong to and what to call them. That was unnecessary and
error-prone: the filing already carries all of it.

Every XBRL concept declares:
  - label()      the official human-readable name, in the filing's language(s)
  - periodType   'instant' (a point in time = balance sheet) or 'duration'
                 (a flow over a period = income statement or cash flow)
  - balance      'debit' or 'credit' - which tells us the natural sign

And every filing declares a PRESENTATION LINKBASE: its own statement
structure, saying which concepts appear on which statement, in what order.
IFRS taxonomy roles are numbered, and those numbers are a reliable signal:

    [2xxxxx]  Statement of financial position   -> balance_sheet
    [3xxxxx]  Statement of profit or loss       -> income_statement
    [4xxxxx]  Statement of comprehensive income -> other (OCI)
    [5xxxxx]  Statement of cash flows           -> cash_flow
    [6xxxxx]  Statement of changes in equity    -> other
    [7xxxxx+] Notes / disclosures               -> other

So classification order of preference:
  1. Presentation linkbase role number  (most reliable - the filer's own view)
  2. periodType                          (instant is definitively balance sheet)
  3. Leave as 'REVIEW' for a human       (rather than guess)

Usage:
    python scripts/10_auto_classify.py data/raw/LVMH_2024.zip
    python scripts/10_auto_classify.py data/raw/LVMH_2024.zip --out data/mappings/auto_LVMH.yaml
    python scripts/10_auto_classify.py data/raw/LVMH_2024.zip --compare data/mappings/ifrs_concepts_v0.yaml
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

import yaml
from arelle import Cntlr, PackageManager, XbrlConst


# ------------------------------------------------------------ role mapping

# IFRS taxonomy presentation roles are numbered; the leading digit is the
# statement family. Matches "[310000]" style prefixes in the role definition.
ROLE_NUMBER_TO_STATEMENT = {
    "2": "balance_sheet",
    "3": "income_statement",
    "4": "other",        # comprehensive income / OCI
    "5": "cash_flow",
    "6": "other",        # changes in equity
    "7": "other",        # notes
    "8": "other",
    "9": "other",
}

# Fallback keyword matching for filers who don't use the numbered IFRS roles
# (extension roles with free-text definitions). Checked in order.
ROLE_KEYWORDS = [
    ("cash_flow", ["cash flow", "cashflow", "flux de tr", "kassaflöde", "flussi finanziari"]),
    ("balance_sheet", ["financial position", "balance sheet", "bilan", "situation financi",
                       "balansräkning", "situazione patrimoniale"]),
    ("other", ["comprehensive income", "changes in equity", "résultat global",
               "variation des capitaux", "totalresultat", "conto economico complessivo"]),
    ("income_statement", ["profit or loss", "income statement", "compte de r",
                          "resultaträkning", "conto economico"]),
]


def statement_from_role(definition: str) -> tuple[str, str]:
    """Return (statement, reason). Empty statement means 'couldn't tell'."""
    if not definition:
        return "", "no role definition"

    m = re.search(r"\[(\d)\d{5}\]", definition)
    if m:
        digit = m.group(1)
        stmt = ROLE_NUMBER_TO_STATEMENT.get(digit)
        if stmt:
            return stmt, f"IFRS role [{digit}xxxxx]"

    low = definition.lower()
    for stmt, keywords in ROLE_KEYWORDS:
        for kw in keywords:
            if kw in low:
                return stmt, f"role keyword '{kw}'"

    return "", "role definition not recognised"


# Third-tier fallback: if a duration concept isn't found in any recognised
# presentation role, guess from its own name/label. Lower confidence than
# the linkbase, so the reason string says so explicitly.
CONCEPT_KEYWORDS = [
    ("cash_flow", ["cashflow", "cashflows", "cashoutflow", "cashinflow",
                   "proceedsfrom", "paymentsfor", "paymentsto", "paymentsof",
                   "purchaseof", "repaymentsof", "dividendspaid", "interestpaid",
                   "interestreceived", "taxespaid", "adjustmentsfor",
                   "increasedecreaseinworkingcapital"]),
    ("other", ["othercomprehensiveincome", "reclassificationadjustments",
               "gainslosseson", "incometaxrelatingto", "increasedecreasethrough"]),
]


def statement_from_concept_name(qname: str, label: str) -> tuple[str, str]:
    """Last-resort guess from the concept's own name and label."""
    haystack = (qname.split(":")[-1] + " " + (label or "")).lower().replace(" ", "")
    for stmt, keywords in CONCEPT_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return stmt, f"concept-name keyword '{kw}' (LOW CONFIDENCE)"
    return "", ""


# ------------------------------------------------------------------ arelle

def load_filing(filepath, package_zip=None):
    controller = Cntlr.Cntlr(logFileName=None)
    if package_zip:
        PackageManager.addPackage(controller, package_zip)
        PackageManager.rebuildRemappings(controller)
    return controller, controller.modelManager.load(filepath)


def open_filing(zip_path: str):
    """Load with the entry-point retry ESEF packages need."""
    controller, model = load_filing(zip_path, package_zip=zip_path)
    if model is None or not model.facts:
        if controller:
            controller.close()
        with zipfile.ZipFile(zip_path) as z:
            cands = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if cands:
            controller, model = load_filing(f"{zip_path}/{cands[0]}", package_zip=zip_path)
    return controller, model


def build_concept_to_statement(model) -> dict:
    """Walk the presentation linkbase: concept qname -> (statement, reason, order)."""
    result = {}
    relset = model.relationshipSet(XbrlConst.parentChild)

    for linkrole in relset.linkRoleUris:
        roleTypes = model.roleTypes.get(linkrole, [])
        definition = roleTypes[0].definition if roleTypes else linkrole
        stmt, reason = statement_from_role(definition)
        if not stmt:
            continue

        # every concept appearing anywhere in this linkrole belongs to it
        role_rels = model.relationshipSet(XbrlConst.parentChild, linkrole)
        seen_order = 0
        for rel in role_rels.modelRelationships:
            for concept in (rel.fromModelObject, rel.toModelObject):
                if concept is None or not getattr(concept, "qname", None):
                    continue
                qn = str(concept.qname)
                # earlier statements win; don't let a note role overwrite
                # a primary-statement assignment
                if qn not in result or result[qn][0] == "other":
                    seen_order += 1
                    result[qn] = (stmt, reason, seen_order)
        # roots too (concepts with no incoming arc)
        for root in role_rels.rootConcepts:
            if root is not None and getattr(root, "qname", None):
                qn = str(root.qname)
                if qn not in result:
                    result[qn] = (stmt, reason, 0)
    return result


def classify(model) -> list:
    """Return one dict per distinct numeric concept in the filing."""
    pres = build_concept_to_statement(model)

    out = {}
    for fact in model.facts:
        concept = fact.concept
        if concept is None or not concept.isNumeric:
            continue
        qn = str(fact.qname)
        if qn in out:
            continue

        stmt, reason, order = pres.get(qn, ("", "", None))

        if not stmt:
            # tier 2: an instant value is a point-in-time balance, which is
            # definitionally a balance sheet item
            if concept.periodType == "instant":
                stmt, reason = "balance_sheet", "periodType=instant"
            else:
                # tier 3: guess from the concept's own name/label
                label_text = concept.label() or ""
                stmt, reason = statement_from_concept_name(qn, label_text)
                if not stmt:
                    stmt, reason = "REVIEW", "duration, not in presentation linkbase, name gives no clue"

        out[qn] = {
            "xbrl_tag": qn,
            "statement": stmt,
            "display_label": concept.label() or qn.split(":")[-1],
            "period_type": concept.periodType,
            "balance": concept.balance or "n/a",
            "is_extension": not qn.startswith("ifrs-full:"),
            "reason": reason,
            "presentation_order": order,
        }
    return sorted(out.values(), key=lambda d: (d["statement"], d["presentation_order"] or 999, d["xbrl_tag"]))


# -------------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("filing", help="Path to the filing zip")
    ap.add_argument("--out", help="Where to write the auto-classified YAML")
    ap.add_argument("--compare", help="Existing mapping YAML to diff against")
    args = ap.parse_args()

    if not Path(args.filing).exists():
        print(f"File not found: {args.filing}")
        sys.exit(1)

    print(f"Loading {args.filing} ...")
    controller, model = open_filing(args.filing)
    if model is None:
        print("Failed to load the filing.")
        sys.exit(1)

    rows = classify(model)
    controller.close()

    print(f"\nClassified {len(rows)} distinct numeric concepts\n")

    by_stmt = {}
    for r in rows:
        by_stmt.setdefault(r["statement"], []).append(r)
    for stmt in sorted(by_stmt):
        n_ext = sum(1 for r in by_stmt[stmt] if r["is_extension"])
        print(f"  {stmt:18s} {len(by_stmt[stmt]):4d}  ({n_ext} company extensions)")

    needs_review = by_stmt.get("REVIEW", [])
    if needs_review:
        print(f"\n{len(needs_review)} concepts need human review "
              f"(duration facts not found in any recognised presentation role):")
        for r in needs_review[:15]:
            print(f"    {r['xbrl_tag']}\n        label: {r['display_label']}")
        if len(needs_review) > 15:
            print(f"    ... and {len(needs_review) - 15} more")

    # ---- compare against the hand-built mapping, if asked
    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
        hand = {}
        for stmt, concepts in existing.items():
            for name, info in concepts.items():
                for tag in info["xbrl_tags"]:
                    hand[tag] = (stmt, info["display_label"])

        agree = disagree = only_auto = 0
        disagreements = []
        for r in rows:
            tag = r["xbrl_tag"]
            if tag not in hand:
                only_auto += 1
                continue
            hand_stmt, hand_label = hand[tag]
            if r["statement"] == "REVIEW":
                continue
            if hand_stmt == r["statement"]:
                agree += 1
            else:
                disagree += 1
                disagreements.append((tag, hand_stmt, r["statement"], r["reason"], r["display_label"]))

        print(f"\n{'=' * 70}\nCOMPARISON vs {args.compare}\n{'=' * 70}")
        print(f"  Agree:              {agree}")
        print(f"  DISAGREE:           {disagree}")
        print(f"  Only in auto:       {only_auto}")
        if disagreements:
            print(f"\n  Disagreements (hand-classified -> taxonomy says):")
            for tag, h, a, reason, label in disagreements[:40]:
                print(f"    {tag}")
                print(f"        hand={h}  taxonomy={a}  ({reason})")
                print(f"        official label: {label}")
            if len(disagreements) > 40:
                print(f"    ... and {len(disagreements) - 40} more")
            print("\n  The taxonomy is usually right - it's the filer's own declaration.")

    # ---- write out
    if args.out:
        grouped = {"income_statement": {}, "balance_sheet": {}, "cash_flow": {},
                   "other": {}, "REVIEW": {}}
        for r in rows:
            short = r["xbrl_tag"].split(":")[-1]
            key = "".join(["_" + c.lower() if c.isupper() else c for c in short]).lstrip("_")
            if len(key) > 60:
                key = key[:57] + "_etc"
            stmt = r["statement"]
            while key in grouped[stmt]:
                key += "_x"
            grouped[stmt][key] = {
                "display_label": r["display_label"],
                "xbrl_tags": [r["xbrl_tag"]],
                "balance": r["balance"],
                "period_type": r["period_type"],
                "source": r["reason"],
            }
        grouped = {k: v for k, v in grouped.items() if v}
        with open(args.out, "w", encoding="utf-8") as f:
            yaml.dump(grouped, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        print(f"\nWrote {args.out}")
