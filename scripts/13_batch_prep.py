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

# ---------------------------------------------------------------- materiality
# PLAN.md's WP4b: a tag no ratio in this project reads, and too small to move
# one even if it did, doesn't need classification at all - per rule 7
# ("prefer an explicit 'not available' state"), an honest, deliberately
# UNKNOWN tag costs nothing, so it's not worth a human's or an LLM's time
# either. This is a rough SCREEN, not a restated financial-materiality
# judgement: the denominator is the largest revenue/assets figure found
# anywhere in the filing (across all periods/dimensions), not a specific
# year's consolidated total - good enough to separate "clearly negligible"
# from "worth a human's attention", not precise enough to be trusted as a
# computed ratio itself.
MATERIALITY_THRESHOLD = 0.01  # 1% of the relevant scale figure

# Same fallback pair 11_ratio_engine.py's get_best(wide, "revenue",
# "revenue_from_contracts_with_customers") already trusts (Phase 6's own
# fix - Kering/Pernod Ricard/Amplifon never tag bare Revenue) - reused here
# as the raw XBRL tag names rather than invented fresh.
REVENUE_TAGS = {"ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"}
ASSETS_TAGS = {"ifrs-full:Assets"}


def build_value_map(model):
    """Largest absolute numeric value seen for each concept anywhere in
    this filing (any period, any dimensional breakdown) - deliberately not
    restricted to one context, since this is only a rough size screen, not
    a specific year's reported figure."""
    result = {}
    for fact in model.facts:
        concept = fact.concept
        if concept is None or not concept.isNumeric or fact.value is None:
            continue
        try:
            v = abs(float(fact.value))
        except (TypeError, ValueError):
            continue
        qn = str(fact.qname)
        if qn not in result or v > result[qn]:
            result[qn] = v
    return result


def find_scale(value_map, tags):
    vals = [value_map[t] for t in tags if t in value_map]
    return max(vals) if vals else None


def assess_materiality(concept, tag_value, revenue, assets):
    """Returns (is_immaterial: bool, ratio_or_None, denominator_label).
    Never guesses: with no tag value or no denominator to compare against,
    returns (False, None, None) - i.e. NOT flagged immaterial, since there's
    nothing to honestly base that call on. periodType decides which scale
    figure applies (revenue for a flow, assets for a balance) - the same
    signal build_pres_map's own instant/duration fallback already uses.

    Real bug found running this live, not assumed: a first pass compared
    EVERY numeric fact's raw value against revenue/assets regardless of
    unit - a share-count concept (e.g. "IncreaseDecreaseInNumberOfShares
    OutstandingThroughOtherComprehensiveIncome", unit=shares) was being
    divided by a EUR revenue figure, a meaningless ratio across
    incompatible units that would flag or clear a tag for the wrong
    reason. Fixed by restricting the screen to concept.isMonetary facts
    only - a share count, a per-share ratio, or a pure-number disclosure
    gets no materiality opinion at all (honestly unscreened, stays in
    all_review) rather than a spurious currency comparison."""
    if not concept.isMonetary or tag_value is None:
        return False, None, None
    denom, label = (assets, "assets") if concept.periodType == "instant" else (revenue, "revenue")
    if not denom:
        return False, None, None
    ratio = tag_value / denom
    return ratio < MATERIALITY_THRESHOLD, ratio, label


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


def build_anchor_map(model):
    """ESEF anchoring: the ESMA RTS requires an issuer using an extension
    element in the primary financial statements to anchor it to the
    closest standard IFRS element via the wider-narrower arcrole in the
    definition linkbase (subtotals are exempt; everything else is
    mandatory) - a machine-readable, regulator-mandated pointer, not a
    guess. Confirmed live against real filings before relying on this:
    Arelle exposes it as XbrlConst.widerNarrower, and every filing tried
    (banco_bpm, adyen, renault, mediobanca, kbc_groep) genuinely declares
    these relationships - see PLAN.md's WP4b write-up for the verification
    and the exact resolutions found (e.g. an Italian bank's
    'Acconti_su_dividendi' anchors directly to 'ifrs-full:DividendsPaid').

    Returns {extension_qname_str: standard_concept_object} - the WIDER
    (standard) side of the relationship points TO the NARROWER (extension)
    concept, so this maps the extension tag to its anchor, taking the
    first relationship found if a tag is (unusually) anchored more than
    once - ESEF requires anchoring to the CLOSEST standard element, so
    multiple anchors for one tag are not expected in practice."""
    result = {}
    relset = model.relationshipSet(XbrlConst.widerNarrower)
    if not relset:
        return result
    for rel in relset.modelRelationships:
        wider, narrower = rel.fromModelObject, rel.toModelObject
        if wider is None or narrower is None:
            continue
        if not getattr(narrower, "qname", None) or not getattr(wider, "qname", None):
            continue
        qn = str(narrower.qname)
        if qn not in result:
            result[qn] = wider
    return result


def resolve_via_anchor(anchor_concept, pres_map, tag_to_statement):
    """Given the standard concept an extension tag anchors to, resolve
    ITS statement - reusing the exact same trust order the rest of this
    file already uses for standard tags: (1) it's already in the trusted
    mapping (fastest, most direct - a real concept this project already
    classified), (2) its own presentation-linkbase role, (3) its own
    periodType. Returns (statement, reason) - statement is '' if none of
    these resolve it (rare for a standard concept)."""
    anchor_qn = str(anchor_concept.qname)
    if anchor_qn in tag_to_statement:
        return tag_to_statement[anchor_qn], f"ESEF anchor -> {anchor_qn} (already in mapping)"

    stmt, _ = pres_map.get(anchor_qn, ("", ""))
    if stmt:
        return stmt, f"ESEF anchor -> {anchor_qn} (anchor's own presentation role)"

    if anchor_concept.periodType == "instant":
        return "balance_sheet", f"ESEF anchor -> {anchor_qn} (anchor's periodType=instant)"

    stmt, _ = statement_from_name(anchor_qn, anchor_concept.label() or anchor_qn.split(":")[-1])
    if stmt:
        return stmt, f"ESEF anchor -> {anchor_qn} (anchor's own name keyword)"

    return "", f"ESEF anchor -> {anchor_qn} (anchor itself unclassifiable - rare)"


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


def scan_zips(zips, existing_tags, tag_to_statement=None):
    """Scan every filing in `zips`, classifying every numeric fact not
    already in `existing_tags`. Returns (all_auto, all_review, per_company) -
    pooled across all companies, same shape __main__ below always used
    inline. Factored out so other scripts can reuse the exact same
    classification pass via importlib (this project's established
    cross-script reuse pattern - see CLAUDE.md) instead of duplicating this
    loop.

    `tag_to_statement` (optional, {xbrl_tag: statement}) lets ESEF anchoring
    (see build_anchor_map/resolve_via_anchor above) resolve an extension tag
    straight to a statement it ALREADY knows, when the tag it anchors to is
    already in the trusted mapping - the strongest tier of the whole
    classification ladder, since it's the filer's own regulator-mandated
    declaration, not a guess (PLAN.md's WP4b).

    Returns (all_auto, all_review, all_immaterial, per_company) - a tag that
    doesn't auto-classify is still checked against the materiality screen
    (see assess_materiality above) before landing in all_review: something
    too small to move any ratio this project computes goes to
    all_immaterial instead, an honest deliberate-UNKNOWN, not a review
    burden. per_company tuples are now (company, auto_n, review_n,
    immaterial_n)."""
    tag_to_statement = tag_to_statement or {}
    all_auto = {}        # tag -> entry (standard or clearly classifiable extension)
    all_review = {}      # tag -> entry, material enough to need a human
    all_immaterial = {}  # tag -> entry, found and sized, safely left UNKNOWN
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
        anchor_map = build_anchor_map(model)
        value_map = build_value_map(model)
        revenue = find_scale(value_map, REVENUE_TAGS)
        assets = find_scale(value_map, ASSETS_TAGS)
        auto_n = review_n = skip_n = anchor_n = immaterial_n = 0

        for fact in model.facts:
            concept = fact.concept
            if concept is None or not concept.isNumeric:
                continue
            qn = str(fact.qname)
            if qn in existing_tags or qn in all_auto or qn in all_review or qn in all_immaterial:
                skip_n += 1
                continue

            is_std = qn.startswith("ifrs-full:")
            label = concept.label() or qn.split(":")[-1]
            key = slugify(qn.split(":")[-1])

            # Tier 1 (highest authority): ESEF-mandated anchoring to a
            # standard concept, tried before the presentation-linkbase
            # tiers below - a filer's own declared anchor beats a role
            # keyword match even for extension tags.
            if not is_std and qn in anchor_map:
                a_stmt, a_reason = resolve_via_anchor(anchor_map[qn], pres_map, tag_to_statement)
                if a_stmt:
                    entry = {
                        "xbrl_tag": qn, "suggested_key": key, "display_label": label,
                        "statement": a_stmt, "balance": concept.balance or "n/a",
                        "reason": a_reason, "used_by": [company],
                    }
                    all_auto[qn] = entry
                    auto_n += 1
                    anchor_n += 1
                    continue

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
                is_immaterial, ratio, denom_label = assess_materiality(
                    concept, value_map.get(qn), revenue, assets)
                if is_immaterial:
                    entry["materiality_ratio"] = round(ratio, 5)
                    entry["materiality_denominator"] = denom_label
                    entry["materiality_note"] = (
                        f"{ratio*100:.2f}% of this filing's largest {denom_label} figure - "
                        f"below the {MATERIALITY_THRESHOLD*100:.0f}% screen, left UNKNOWN"
                    )
                    all_immaterial[qn] = entry
                    immaterial_n += 1
                else:
                    all_review[qn] = entry
                    review_n += 1

        c.close()
        per_company.append((company, auto_n, review_n, immaterial_n))
        print(f"  auto={auto_n} (of which anchor-resolved={anchor_n})  review={review_n}  "
              f"immaterial={immaterial_n}  already_known={skip_n}")

    return all_auto, all_review, all_immaterial, per_company


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default="data/mappings/ifrs_concepts_v0.yaml")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--review-out", default="data/mappings/REVIEW_extensions.yaml")
    ap.add_argument("--immaterial-out", default="data/mappings/IMMATERIAL_extensions.yaml",
                     help="Audit trail for tags found but judged too small to matter "
                          "(PLAN.md's WP4b materiality screen) - never applied to the "
                          "mapping, kept only so the exclusion is inspectable, not silent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="+", help="Only process these zip filenames")
    args = ap.parse_args()

    with open(args.mapping, encoding="utf-8") as f:
        existing = yaml.safe_load(f)
    existing_tags = set()
    tag_to_statement = {}
    for stmt, concepts in existing.items():
        for name, info in concepts.items():
            existing_tags.update(info["xbrl_tags"])
            for tag in info["xbrl_tags"]:
                tag_to_statement[tag] = stmt
    print(f"Existing mapping: {len(existing_tags)} tags\n")

    raw_dir = Path(args.raw_dir)
    if args.only:
        zips = [raw_dir / z for z in args.only if (raw_dir / z).exists()]
    else:
        zips = sorted(raw_dir.glob("*.zip"))

    all_auto, all_review, all_immaterial, per_company = scan_zips(zips, existing_tags, tag_to_statement)
    total_found = len(all_auto) + len(all_review) + len(all_immaterial)
    handled = len(all_auto) + len(all_immaterial)

    print(f"\n{'='*60}")
    print(f"TOTAL across all companies:")
    print(f"  Auto-classify (no human needed):     {len(all_auto)}")
    print(f"  Immaterial (safe to leave UNKNOWN):  {len(all_immaterial)}")
    print(f"  Need review (genuinely need a human): {len(all_review)}")
    if total_found:
        print(f"  Handled without a human: {handled}/{total_found} ({100*handled/total_found:.1f}%)")

    if args.dry_run:
        print("\nDRY RUN - nothing written.")
        print("\nExtension tags needing review:")
        for qn, entry in list(all_review.items())[:10]:
            print(f"  {qn}: {entry['display_label']}")
        if len(all_review) > 10:
            print(f"  ... and {len(all_review) - 10} more")
        if all_immaterial:
            print("\nSample of tags screened immaterial (left UNKNOWN, not applied):")
            for qn, entry in list(all_immaterial.items())[:5]:
                print(f"  {qn}: {entry['materiality_note']}")
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

        # audit trail for the materiality-screened tags - NEVER applied to
        # the mapping (they were deliberately not classified at all), kept
        # only so a human can spot-check the screen itself, per rule 7's
        # "prefer an explicit not available state" - a silent exclusion
        # would be worse than a wrong classification.
        if all_immaterial:
            immaterial_data = {
                "_instructions": (
                    f"Tags found but judged too small (< {MATERIALITY_THRESHOLD*100:.0f}% of this "
                    "filing's own revenue/assets scale) to be worth classifying - deliberately left "
                    "UNKNOWN, per this project's own rule for an explicit 'not available' state. "
                    "NOT applied anywhere. Spot-check a few if you want to sanity-check the screen "
                    "itself; nothing here needs action."
                ),
                "left_unknown": list(all_immaterial.values()),
            }
            with open(args.immaterial_out, "w", encoding="utf-8") as f:
                yaml.dump(immaterial_data, f, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
            print(f"Wrote {len(all_immaterial)} materiality-screened tags to {args.immaterial_out} (audit trail only)")

        print(f"\n*** Open {args.review_out}, fill in statements, then run:")
        print("*** python scripts/12_apply_review.py")
        print("*** Then reload ALL companies:")
        print("*** python scripts/09_batch_load.py --reset-facts")
