"""
Ratio engine - V1.

Computes financial ratios across all loaded companies and years,
writes them to the ratio table in the database, and exports a
clean comps table to Excel.

RATIOS COMPUTED
---------------
Profitability (currency-neutral - comparable across EUR/SEK/USD):
  gross_margin          Gross Profit / Revenue
  operating_margin      Operating Profit / Revenue
  net_margin            Net Profit Attributable to Owners / Revenue

Efficiency (currency-neutral):
  cash_conversion       Cash Flow from Operations / Operating Profit
                        > 1.0 means profit turns into MORE cash than booked
                        < 1.0 means some profit is not yet collected/paid
  dso                   Days Sales Outstanding = Trade Receivables / Revenue * 365
                        How many days of sales are sitting uncollected.
  dio                   Days Inventory Outstanding = Inventories / COGS * 365
                        How many days of stock sit in inventory before selling.
  dpo                   Days Payables Outstanding = Trade Payables / COGS * 365
                        How many days the company takes to pay its suppliers.
  ccc                   Cash Conversion Cycle = DSO + DIO - DPO
                        Days between paying cash out (for inventory) and
                        collecting cash in (from customers). Lower is better -
                        it means less cash is tied up in working capital.
                        NOTE: uses ENDING balance sheet values, not the
                        average of opening+closing (the textbook-correct
                        version) - simpler, consistent with how net_debt/
                        equity are already computed here, but means DSO/DIO/
                        DPO will look slightly off for a year with a big
                        swing in receivables/inventory/payables right at
                        year-end. Fine for trend-watching, worth flagging if
                        used for a precise working-capital target.

Returns (currency-neutral):
  roic                  Operating Profit * (1 - tax_rate) / Invested Capital
                        where Invested Capital = Equity + Net Debt
  roe                   Net Profit / Equity Attributable to Owners

Leverage (NOT currency-neutral - only meaningful within one currency):
  net_debt_ebitda       Net Debt / Operating Profit
                        (proxy for EBITDA since we don't always have it)
  interest_cover        Operating Profit / Finance Costs (where available)

DESIGN NOTES
------------
- All ratios are computed from normalized_name concepts in the database,
  not from raw XBRL tags. This means a mapping fix automatically improves
  all downstream ratios on the next run.
- NULL inputs produce NULL outputs. We never fill gaps with zeros - a
  missing gross profit is different from a zero gross profit.
- Signs: XBRL filers are inconsistent about whether expenses are positive
  or negative. We take absolute values where needed and note this.
- One value per (company, year, concept), chosen DETERMINISTICALLY - see
  resolve_fact_conflicts(). The same fact routinely appears in several
  filings (each annual report repeats last year's comparatives), and it is
  not always the same number: restated comparatives, rounding, a flipped
  sign, or - found live on Recordati - an opening balance whose date is off
  by a day so that two different instants land in the same year. The old
  pivot took "first" over an un-ordered SQL result, so which one won was
  undefined. Rule: real value > NaN; the year's representative period (a
  ~365-day duration, or the latest instant); then the LATEST filing
  (restated comparatives supersede the original); then filing_id. Every
  contested key is reported, never silently absorbed.
- Currency-neutral ratios are valid for cross-company comparison now.
  Absolute-value ratios (net debt, revenue size) need FX conversion first.
- Some ratios are BLANKED on purpose for financial companies (gate_financial_
  ratios): working-capital, gross-margin, cash-conversion, ROIC and
  net-debt-vs-EBIT are built on cost-of-sales / trade-cycle / debt-as-
  financing ideas that mean nothing for a lender, insurer or payment
  processor (found live: Adyen showed DPO 1,018 days). A blank with a stored
  reason (ratio.note) is honest; a plausible-looking wrong number is not.

Usage:
    python scripts/11_ratio_engine.py
    python scripts/11_ratio_engine.py --company "L'Oreal"   # one company only
    python scripts/11_ratio_engine.py --no-db               # Excel only, no DB write
"""
import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

COMPANIES_YAML = Path(__file__).parent.parent / "data" / "companies.yaml"


# ---------------------------------------------------------------- fetch

ANNUAL_SPAN_DAYS = (300, 400)     # a duration this long is a fiscal year (52/53-week years included)


def fiscal_year_label(period_type, start_date, end_date) -> int:
    """The fiscal year a fact belongs to: the year the period ENDS (Arelle stores every end date one day
    late, hence the -1 day).

    Instants and annual durations therefore agree, so a company's P&L, cash flow and balance sheet for one
    fiscal year land in ONE row. Durations used to be labelled by the year they START, which is identical for
    a calendar-year filer but split a broken fiscal year in two: Pernod Ricard (FYE 30 June) had FY2025
    (1 Jul 2024 - 30 Jun 2025) revenue and EBIT under "2024" while its 30 Jun 2025 inventories and debt sat
    under "2025" - every ratio mixing flows and stocks (DIO, DPO, ROIC, net debt / EBITDA) was one year out.

    A duration that is not about a year long (a stub, a multi-year period from a filer defect such as
    Recordati's 2021-2033 one) keeps the start-year label it always had: it is never the annual figure, and
    moving it would create a phantom year."""
    end_year = (end_date - timedelta(days=1)).year
    if period_type == "instant" or start_date is None or pd.isna(start_date):
        return end_year
    lo, hi = ANNUAL_SPAN_DAYS
    return end_year if lo <= (end_date - start_date).days <= hi else start_date.year


def fetch_facts(engine, company_filter=None) -> pd.DataFrame:
    """Pull all facts from the database as a long-format DataFrame (one row
    per stored fact, with its filing_id so conflicts between filings can be
    resolved deterministically - see resolve_fact_conflicts())."""
    where = "WHERE c.name = :company" if company_filter else ""
    query = f"""
        SELECT
            c.name          AS company,
            c.company_id,
            fv.filing_id,
            ic.normalized_name,
            p.period_type,
            p.start_date,
            p.end_date,
            fv.value,
            fv.currency
        FROM fact_value fv
        JOIN ifrs_concept ic ON fv.concept_id = ic.concept_id
        JOIN period p        ON fv.period_id  = p.period_id
        JOIN filing fi       ON fv.filing_id  = fi.filing_id
        JOIN company c       ON fi.company_id = c.company_id
        {where}
    """
    params = {"company": company_filter} if company_filter else {}
    df = pd.read_sql(text(query), engine, params=params)
    if df.empty:
        return df

    df["year"] = [fiscal_year_label(pt, s, e)
                  for pt, s, e in zip(df["period_type"], df["start_date"], df["end_date"])]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


_KEY = ["company_id", "year", "normalized_name"]
# a relative difference below this between two filings' value for the SAME
# period is reported as rounding, not a restatement
ROUNDING_TOLERANCE = 1e-3
CONFLICT_COLUMNS = ["company", "company_id", "year", "normalized_name", "kind",
                    "chosen_value", "alt_value", "chosen_filing_id", "alt_filing_id"]


def filing_reporting_years(df: pd.DataFrame) -> pd.Series:
    """{filing_id: fiscal year the filing is primarily ABOUT}, derived from
    the filing's own facts - never from its filename or the archive's
    period_end label, both of which have been wrong in practice (the archive
    lists a Recordati FY2022 package as 2032-12-31).

    A filing repeats prior-year comparatives, so its own year is the LATEST
    end date that carries a substantial share (>= half) of its most
    populated end date. Requiring a substantial share is what stops one
    stray far-future-dated fact from making an old filing look like the
    newest one."""
    counts = df.groupby(["filing_id", "end_date"]).size().rename("n").reset_index()
    counts["max_n"] = counts.groupby("filing_id")["n"].transform("max")
    main = counts[counts["n"] >= 0.5 * counts["max_n"]]
    latest_end = main.groupby("filing_id")["end_date"].max()
    return latest_end.map(lambda d: (d - timedelta(days=1)).year)


def resolve_fact_conflicts(df: pd.DataFrame):
    """One row per (company_id, year, concept), chosen deterministically,
    plus a report of every key where the candidates disagreed.

    Selection order (first rule that separates the candidates wins):
      1. a real value beats NaN;
      2. the year's representative period: a duration closest to 365 days
         (a stub or multi-year period loses), and among instants the LATEST
         (the year-end balance beats an opening balance dated a day late -
         found live: Recordati's opening cash, dated 2021-01-02, was landing
         in the same year as its real 2021 close and being picked for it);
      3. the LATEST filing (by filing_reporting_years): restated comparatives
         supersede the number originally reported, so a trend is on one
         accounting basis;
      4. the highest filing_id, then the value - only so the result is a pure
         function of the data, never of row order.

    Returns (resolved, conflicts). `resolved` has the same columns as `df`.
    `conflicts` has one row per NON-chosen candidate whose value differs from
    the chosen one, with kind:
      sign_flip     same period, |value| equal, opposite sign
      rounding      same period, relative difference < ROUNDING_TOLERANCE
      restated      same period, a genuinely different number
      other_period  a different period in the same year (resolved by rule 2)
    Identical repeats (the normal case: comparatives repeated verbatim) are
    not conflicts."""
    empty = pd.DataFrame(columns=CONFLICT_COLUMNS)
    if df.empty:
        return df.copy(), empty

    d = df.copy()
    if "filing_id" not in d.columns:
        d["filing_id"] = 0
    d["_filing_year"] = d["filing_id"].map(filing_reporting_years(d))
    start = pd.to_datetime(d["start_date"])
    end = pd.to_datetime(d["end_date"])
    is_duration = d["period_type"].eq("duration")
    span_days = (end - start).dt.days.where(is_duration)
    d["_gap"] = (span_days - 365).abs().fillna(0)
    d["_end"] = end
    d["_has_value"] = d["value"].notna()

    d = d.sort_values(
        _KEY + ["_has_value", "_gap", "_end", "_filing_year", "filing_id", "value"],
        ascending=[True, True, True, False, True, False, False, False, True],
        na_position="last")
    chosen_mask = ~d.duplicated(_KEY, keep="first")
    chosen, alts = d[chosen_mask], d[~chosen_mask]

    if alts.empty:
        return chosen.drop(columns=["_filing_year", "_gap", "_end", "_has_value"]), empty

    m = alts.merge(
        chosen[_KEY + ["value", "start_date", "end_date", "filing_id"]],
        on=_KEY, suffixes=("_alt", "_chosen"))
    va, vc = m["value_alt"], m["value_chosen"]
    identical = (va == vc) | (va.isna() & vc.isna())
    same_period = (m["start_date_alt"].astype(str) == m["start_date_chosen"].astype(str)) & \
                  (m["end_date_alt"].astype(str) == m["end_date_chosen"].astype(str))
    flip = same_period & np.isclose(va.abs(), vc.abs(), rtol=1e-9) & (np.sign(va) != np.sign(vc))
    rel = (va - vc).abs() / np.maximum(va.abs(), vc.abs()).replace(0, np.nan)
    rounding = same_period & ~flip & (rel < ROUNDING_TOLERANCE)
    m["kind"] = np.select(
        [~same_period, flip, rounding], ["other_period", "sign_flip", "rounding"], default="restated")
    m = m[~identical & va.notna()]
    conflicts = m.rename(columns={"value_chosen": "chosen_value", "value_alt": "alt_value",
                                  "filing_id_chosen": "chosen_filing_id",
                                  "filing_id_alt": "alt_filing_id"})[CONFLICT_COLUMNS]
    return chosen.drop(columns=["_filing_year", "_gap", "_end", "_has_value"]), \
        conflicts.reset_index(drop=True)


def pivot_to_wide(df: pd.DataFrame, return_conflicts: bool = False):
    """
    Convert long-format facts into a wide table:
    one row per (company, year), one column per concept.

    Facts are first reduced to ONE row per (company, year, concept) by
    resolve_fact_conflicts() - a pure function of the data. (This used to
    pivot with aggfunc="first" over an un-ordered SQL result, so where two
    filings disagreed the winner was whatever the database happened to
    return first.)

    return_conflicts=True also returns the conflicts report, for callers
    (main) that want to print it; every other caller is unchanged.
    """
    resolved, conflicts = resolve_fact_conflicts(df)
    wide = resolved.pivot_table(
        index=["company", "company_id", "year"],
        columns="normalized_name",
        values="value",
        aggfunc="first",
    ).reset_index()
    return (wide, conflicts) if return_conflicts else wide


def print_conflict_summary(conflicts: pd.DataFrame):
    """Loud, per this project's convention (never a silent choice)."""
    if conflicts.empty:
        return
    by_kind = conflicts["kind"].value_counts().to_dict()
    print(f"Resolved {len(conflicts)} conflicting fact(s) deterministically "
          f"(latest filing wins): {by_kind}")
    flips = conflicts[conflicts["kind"] == "sign_flip"]
    if not flips.empty:
        print(f"*** {len(flips)} SIGN FLIP(S) between filings - the later filing's sign was used; "
              f"worth a look at the source:")
        for _, r in flips.head(10).iterrows():
            print(f"      {r['company']} {int(r['year'])} {r['normalized_name']}: "
                  f"chosen {r['chosen_value']:,.0f}, other filing {r['alt_value']:,.0f}")


# ---------------------------------------------------------------- ratio helpers

def safe_div(numerator, denominator, scale=1):
    """Divide two series safely. Returns NaN where denominator is 0 or NaN."""
    try:
        result = numerator / denominator.replace(0, float("nan")) * scale
        return result
    except Exception:
        return pd.Series([float("nan")] * len(numerator))


# The balance-sheet lines that make up financial debt, by normalized concept. Every one is a line a company
# prints (verified with scripts/reconcile_reports.py); the set follows the IFRS taxonomy's own elements.
NONCURRENT_DEBT_LINES = (
    "longterm_borrowings", "noncurrent_portion_of_noncurrent_bonds_issued",
    "noncurrent_portion_of_other_noncurrent_borrowings", "other_noncurrent_financial_liabilities",
    "noncurrent_lease_liabilities",
)
CURRENT_DEBT_LINES = (
    "shortterm_borrowings", "current_borrowings_and_current_portion_of_noncurrent_borr_etc",
    "current_bonds_issued_and_current_portion_of_noncurrent_bonds_etc",
    "other_current_borrowings_and_current_portion_of_other_noncur_etc", "other_current_financial_liabilities",
    "current_lease_liabilities",
)
# IFRS "financial liabilities" SUBTOTALS (IAS 1.54(m)). Whether a printed subtotal CONTAINS the detail lines
# or sits BESIDE them (Amplifon prints 984M of non-current financial liabilities beside 364M of lease
# liabilities; Kering 13M beside 10,026M of borrowings) cannot be told from the numbers - only from the
# statement structure. So the rule is deliberately one-directional: a subtotal is used ONLY when no borrowing
# line is present on its side. It can understate (a small sibling is left out) but can never double count.
FINANCIAL_LIABILITY_TOTALS = {
    "noncurrent_financial_liabilities": ("longterm_borrowings", "noncurrent_portion_of_noncurrent_bonds_issued",
                                         "noncurrent_portion_of_other_noncurrent_borrowings"),
    "current_financial_liabilities": ("shortterm_borrowings", "current_borrowings_and_current_portion_of_noncurrent_borr_etc",
                                      "current_bonds_issued_and_current_portion_of_noncurrent_bonds_etc",
                                      "other_current_borrowings_and_current_portion_of_other_noncur_etc"),
}
# parent -> child: the taxonomy's "current borrowings and current portion of non-current borrowings" contains
# short-term borrowings, so counting both would double count (no company stores both today)
CONTAINS = {"current_borrowings_and_current_portion_of_noncurrent_borr_etc": ("shortterm_borrowings",)}


def _financial_debt_row(row: pd.Series):
    """(financial debt, basis) for one company-year, or (NaN, '') when no debt line is stored."""
    used = {}
    for name in NONCURRENT_DEBT_LINES + CURRENT_DEBT_LINES:
        v = row.get(name)
        if pd.notna(v):
            used[name] = abs(float(v))
    for total, borrowing_lines in FINANCIAL_LIABILITY_TOTALS.items():
        t = row.get(total)
        if pd.notna(t) and not any(b in used for b in borrowing_lines):
            used[total] = abs(float(t))
    for parent, children in CONTAINS.items():
        if parent in used:
            for child in children:
                if child in used and used[parent] >= used[child]:
                    del used[child]
    if not used:
        return float("nan"), "", False
    non_current = set(NONCURRENT_DEBT_LINES) | {t for t in FINANCIAL_LIABILITY_TOTALS if t.startswith("noncurrent")}
    current = set(CURRENT_DEBT_LINES) | {t for t in FINANCIAL_LIABILITY_TOTALS if t.startswith("current")}
    complete = any(k in non_current for k in used) and any(k in current for k in used)
    return sum(used.values()), "+".join(sorted(used)), complete


def compute_financial_debt(wide: pd.DataFrame) -> pd.DataFrame:
    """Per row of the wide table: `financial_debt` (sum of the printed financial-liability lines, absolute
    values), `basis` (the concepts summed, for audit) and `complete` - True only when debt lines are stored on
    BOTH the non-current and the current side. Lease liabilities are included: they are IFRS 16 financial
    liabilities and EBIT/EBITDA are already stated after IFRS 16.

    `complete` exists because partial input yields a plausible-looking WRONG number: Recordati with only a
    24M current line stored looked like a net-cash company (-405M) while its printed loans make it about
    EUR 2.0bn of net debt. Where a side is missing, downstream net debt is blank, not a guess."""
    results = [_financial_debt_row(row) for _, row in wide.iterrows()]
    return pd.DataFrame({
        "financial_debt": pd.Series([r[0] for r in results], index=wide.index, dtype="float64"),
        "basis": pd.Series([r[1] for r in results], index=wide.index, dtype="object"),
        "complete": pd.Series([r[2] for r in results], index=wide.index, dtype="bool"),
    })


def get_col(wide: pd.DataFrame, name: str) -> pd.Series:
    """Return a column if it exists, else a NaN series of the same length."""
    if name in wide.columns:
        return wide[name]
    return pd.Series([float("nan")] * len(wide), index=wide.index)


def get_best(wide: pd.DataFrame, *names: str) -> pd.Series:
    """Try each name in order, combine_first so earlier names take priority.
    This is the core fix for concepts where companies use different tags
    for the same economic item (e.g. long-term borrowings, PBT).
    Returns NaN where none of the names exist.

    NOTE: we do NOT reverse here - first name listed has highest priority,
    combine_first(other) fills NaN positions from 'other', so we start
    with the highest-priority series and fill gaps from lower-priority ones.
    """
    result = pd.Series([float("nan")] * len(wide), index=wide.index)
    for name in reversed(names):  # reversed so first-listed name wins
        if name in wide.columns:
            result = wide[name].combine_first(result)
    return result


# ---------------------------------------------------------------- compute

def compute_ratios(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all ratios from a wide-format DataFrame.
    Each ratio becomes a column. NaN = inputs were missing.

    Uses get_best() to try multiple normalized names for the same concept,
    because companies use different tags for economically identical items.
    Priority is left-to-right: first name found wins.
    """
    r = wide[["company", "company_id", "year"]].copy()

    # --- revenue ---
    # Found via a real run (22_dcf.py's fetch_base_year failing for 6 of 11
    # companies with "missing required inputs", most of it unrelated to
    # D&A): get_col(wide, "revenue") was checking ONLY the bare "revenue"
    # tag, with no fallback - unlike almost every other concept in this
    # function. Kering, Pernod Ricard and Amplifon never use that tag at
    # all, in ANY year - they exclusively tag "revenue_from_contracts_with_
    # customers" (the IFRS 15-specific contract-revenue concept), which is
    # the same top-line revenue figure, just a different taxonomy element.
    # Unlike the D&A tag situation, this isn't ambiguous - it's the
    # identical concept under IFRS 15's own naming - so it's safe to add
    # as a fallback rather than leave flagged.
    rev = get_best(wide, "revenue", "revenue_from_contracts_with_customers").abs()

    # --- operating profit (EBIT) ---
    # Priority: standard IFRS tag > L'Oreal extension > Essity > LVMH > Shell
    # Shell doesn't tag operating profit directly - they tag revenue_and_other_income
    # and operating_expense separately, so we compute: revenue - opex
    shell_rev = get_col(wide, "revenue_and_other_income")
    shell_opex = get_col(wide, "operating_expense").abs()
    shell_ebit = shell_rev - shell_opex  # NaN if either input is missing

    ebit = get_best(wide,
        "profit_loss_from_operating_activities",   # standard IFRS - 9 companies
        "resultat_dexploitation",                   # L'Oreal
        "operating_profit_excl_i_a_c",              # Essity
        "profit_loss_from_operating_activities_after_share_of_prof_etc",  # LVMH
    ).combine_first(shell_ebit)  # Shell: computed from revenue - opex
    r["operating_margin"] = safe_div(ebit, rev, scale=100)
    r["_ebit"] = ebit

    # --- gross margin ---
    # Gross Profit = Revenue - Cost of Sales is a textbook accounting
    # identity, not a judgment call like the D&A tag ambiguity - safe to
    # derive whichever of the two a company doesn't explicitly tag, AS
    # LONG AS the other one is actually present (never invents a number
    # from nothing). Found via a real run: Danone tags cost_of_sales but
    # never a distinct gross_profit subtotal (its income statement jumps
    # straight from Cost of Sales to Operating Profit); Essity does the
    # reverse - tags gross_profit but never cost_of_sales. Companies that
    # tag NEITHER (Amplifon, Shell - "by nature" P&L presentation, no
    # COGS/gross-profit split exists in their statements at all) correctly
    # stay NaN here - there's nothing to derive from.
    gp_tag = get_col(wide, "gross_profit")
    cogs_tag = get_col(wide, "cost_of_sales").abs()
    gp = gp_tag.combine_first(rev - cogs_tag)
    cogs = cogs_tag.combine_first(rev - gp_tag)
    r["gross_margin"] = safe_div(gp, rev, scale=100)

    # --- net margin ---
    net = get_col(wide, "profit_loss_attributable_to_owners_of_parent")
    r["net_margin"] = safe_div(net, rev, scale=100)

    # --- cash conversion ---
    cfo = get_col(wide, "cash_flows_from_used_in_operating_activities")
    r["cash_conversion"] = safe_div(cfo, ebit, scale=100)

    # --- working capital: DSO / DIO / DPO / CCC ---
    # Controller's daily tool, not a bank's - this is exactly the kind of
    # ratio a Contrôleur de Gestion actually watches month to month.
    receivables = get_best(wide,
        "current_trade_receivables",              # specific tag - preferred
        "trade_and_other_current_receivables",    # broader fallback (includes non-trade)
    )
    inventory = get_best(wide, "inventories", "inventories_total")
    payables = get_best(wide,
        "trade_and_other_current_payables_to_trade_suppliers",  # specific - preferred
        "trade_and_other_current_payables",                     # broader fallback
        "other_current_payables",                               # weakest fallback
    )
    # `cogs` already computed above (tagged cost_of_sales, or derived from
    # Revenue - Gross Profit when a company tags gross_profit but never
    # cost_of_sales directly - see that section's comment, e.g. Essity).

    r["dso"] = safe_div(receivables, rev, scale=365)
    r["dio"] = safe_div(inventory, cogs, scale=365)
    r["dpo"] = safe_div(payables, cogs, scale=365)
    r["ccc"] = r["dso"] + r["dio"] - r["dpo"]  # NaN if any component is NaN - never fake a CCC

    # --- effective tax rate ---
    tax = get_col(wide, "income_tax_expense_continuing_operations")

    # PBT fallback chain:
    # 1. Standard IFRS tag (7 companies have this)
    # 2. L'Oreal French extension
    # 3. Essity extension
    # 4. APPROXIMATE: EBIT + finance items (for Danone, LVMH, Pernod Ricard
    #    which don't tag PBT directly but we can reconstruct it)
    #    PBT = Operating Profit + Finance Income/Cost + Share of Associates
    finance = get_col(wide, "finance_income_cost")
    associates = get_col(wide, "share_of_profit_loss_of_associates_and_joint_ventures_acc_etc")
    pbt_approx = ebit + finance.fillna(0) + associates.fillna(0)

    pbt = get_best(wide,
        "profit_loss_before_tax",
        "resultat_avant_impot_et_societes_mises_en_equivalence",
        "profit_before_tax_excl_i_a_c",
    ).combine_first(pbt_approx)  # use approximation where direct tag is missing

    # abs() handles sign convention differences across filers
    # Multiply by 100 to match the percentage scale used by all other ratios
    r["tax_rate"] = safe_div(tax.abs(), pbt.abs(), scale=100).clip(0, 60)

    # --- net debt ---
    # Financial debt = the financial-liability lines the company PRINTS on its balance sheet (IAS 1.54(m):
    # borrowings, bonds, lease liabilities, other financial liabilities; NOT derivatives, trade payables,
    # provisions or deferred tax), less cash and cash equivalents. See compute_financial_debt. Where no such
    # line is stored the result is blank - the old "use total non-current liabilities" fallback is gone: it
    # both included non-debt liabilities and missed all current debt (Recordati 2025: -8%, Danone 2024: -4% by
    # two offsetting mistakes) and ignored lease liabilities altogether (LVMH: EUR 17.8bn).
    debt = compute_financial_debt(wide)
    cash = get_col(wide, "cash_and_cash_equivalents").abs()
    # blank unless debt lines are stored on both sides; NaN stays NaN: debt unknown -> net debt unknown
    net_debt = debt["financial_debt"].where(debt["complete"]) - cash.fillna(0)
    r["_net_debt"] = net_debt
    r["_debt_basis"] = debt["basis"].where(debt["complete"], "incomplete: " + debt["basis"])   # audit trail

    # --- ROIC ---
    equity_parent = get_col(wide, "equity_attributable_to_owners_of_parent")
    nci = get_col(wide, "noncontrolling_interests").fillna(0)
    invested_capital = equity_parent + nci + r["_net_debt"]
    nopat = ebit * (1 - r["tax_rate"].clip(0, 40) / 100)  # tax_rate is in %, divide back
    r["roic"] = safe_div(nopat, invested_capital, scale=100)

    # --- ROE ---
    r["roe"] = safe_div(net, equity_parent, scale=100)

    # --- Net Debt / Operating Profit ---
    r["net_debt_ebitda_proxy"] = safe_div(r["_net_debt"], ebit)

    # --- absolute values kept for downstream use (valuation, EV multiples) ---
    # Prefixed with _ so RATIO_META (which drives print/DB/Excel) never picks
    # them up by accident - they're in native currency, not ratios.
    r["_revenue"] = rev
    r["_net_income"] = net

    # EBITDA proxy = EBIT + D&A add-back. IFRS filers don't tag "EBITDA"
    # directly (it's a non-GAAP/non-IFRS measure), so this reconstructs it
    # from whichever D&A line items are actually tagged. Missing pieces
    # default to 0 (fillna) rather than making the whole figure NaN - a
    # company that only discloses PP&E depreciation still gives a usable
    # (if slightly understated) EBITDA, which is better than nothing for
    # a trading-comps EV/EBITDA multiple.
    # D&A add-back for EBITDA reconstruction. Two disclosure styles exist
    # in practice: some companies (e.g. L'Oreal-style) break D&A into
    # granular sub-concepts (PP&E, right-of-use assets, intangibles);
    # others (e.g. Shell) report ONLY a single combined cash-flow-
    # statement adjustment line and never tag the granular breakdown at
    # all. Summing only the granular concepts silently gave Shell
    # _da_total=0 (none of the 3 granular concepts exist for it) - EBITDA
    # collapsed to just EBIT, understating it by ~$25-31bn/year and
    # roughly doubling the computed EV/EBITDA (10.2x vs the real ~4.3-5.2x
    # per GuruFocus/Multiples.vc/StockAnalysis/Investing.com). Fix:
    # PREFER the combined line when a company has one - it's already the
    # total, more complete than any sub-breakdown (captures exploration/
    # decommissioning-related D&A a granular PP&E/ROU/intangibles split
    # might miss) - and only fall back to summing the granular concepts
    # for companies that never disclose a combined total at all.
    da_ppe = get_col(wide, "depreciation_property_plant_and_equipment").fillna(0)
    da_rou = get_col(wide, "depreciation_rightofuse_assets").fillna(0)
    amort = get_col(wide, "amortisation_intangible_assets_other_than_goodwill").fillna(0)
    granular_da_sum = da_ppe + da_rou + amort

    # Found via a real run (22_dcf.py on L'Oreal): _da_total was silently
    # coming out as 0 for 8 of the 11 companies (L'Oreal, LVMH, Kering,
    # EssilorLuxottica, Pernod Ricard, Essity, Moncler, Puig Brands), not
    # just Shell - the "combined vs. granular" split above only covers
    # two disclosure styles, but filers also use OTHER extension tags
    # for the same combined D&A line. Added
    # "adjustments_for_depreciation_and_amortisation_expense" (no
    # "_and_etc" suffix) as a third, LOWER-priority candidate - verified
    # sane (3.9-14.0% of revenue, in line with each company's known
    # capital intensity) for Pernod Ricard, Moncler and Puig Brands.
    # Deliberately NOT extended to cover L'Oreal, LVMH, Kering,
    # EssilorLuxottica or Essity even though they also show _da_total=0:
    # each has only AMBIGUOUS tags available (bundled with provisions,
    # impairment, or split across several overlapping concepts - e.g.
    # Essity has both the "_and_etc" and bare variants POPULATED with
    # different values, LVMH's only candidates are "...provisions_and_
    # adjustments_for_depreciation..." or ROU-leases-only). Guessing
    # which one is the clean total risks quietly corrupting EBITDA/FCFF
    # for those companies - the da_total_is_fallback flag below (not a
    # guess) is the honest fix for them; a real fix needs a human to
    # read each filing's cash-flow statement and confirm which tag is
    # the true total, not an agent pattern-matching tag names.
    da_combined = get_best(
        wide,
        "adjustments_for_depreciation_and_amortisation_expense_and_etc",
        "depreciation_amortisation_and_impairment_loss_reversal_of_etc",
        "adjustments_for_depreciation_and_amortisation_expense",
    )
    r["_da_total"] = da_combined.where(da_combined.notna(), granular_da_sum)
    r["_ebitda"] = ebit + r["_da_total"]
    return r


# ---------------------------------------------------------------- format

RATIO_META = {
    "gross_margin":         ("Gross Margin",              True,  "%"),
    "operating_margin":     ("Operating Margin",          True,  "%"),
    "net_margin":           ("Net Margin",                True,  "%"),
    "cash_conversion":      ("Cash Conversion",           True,  "%"),
    "dso":                  ("DSO (Days Sales Outstanding)",      True,  "days"),
    "dio":                  ("DIO (Days Inventory Outstanding)",  True,  "days"),
    "dpo":                  ("DPO (Days Payables Outstanding)",   True,  "days"),
    "ccc":                  ("Cash Conversion Cycle",     True,  "days"),
    "tax_rate":             ("Effective Tax Rate",        True,  "%"),
    "roic":                 ("ROIC",                      True,  "%"),
    "roe":                  ("ROE",                       True,  "%"),
    "net_debt_ebitda_proxy":("Net Debt vs Op. Profit",  False, "x"),
}


# ---------------------------------------------------------------- financial-sector gating
#
# A financial company's statements are not built like an industrial's, so
# ratios that assume one are blanked (NaN + a stored reason), never computed.
# Which ratios, and why - each is a judgement worth reviewing:
FINANCIAL_NOT_MEANINGFUL = {
    "gross_margin":          "a lender, insurer or payment processor has no cost of sales",
    "cash_conversion":       "operating cash flow includes customer-deposit, policyholder or merchant-payable flows",
    "dso":                   "trade receivables are not its operating cycle",
    "dio":                   "inventory is not its operating cycle",
    "dpo":                   "payables are measured against a cost of sales it does not have",
    "ccc":                   "built from DSO, DIO and DPO",
    "roic":                  "invested capital (equity + net debt) is undefined when deposits, insurance "
                             "liabilities or client money are operating liabilities",
    "net_debt_ebitda_proxy": "its debt funds lending or client money, and EBIT is not its earnings base",
}
# Deliberately NOT gated: operating_margin, net_margin, tax_rate, roe. Whether
# revenue-based margins make sense is company-specific (fine for a payments
# firm; for a bank the revenue tag is usually absent, so they are NaN anyway),
# and ROE / tax rate are meaningful for every kind of company.

# company.sector_std is yfinance's own sector field (PLAN.md WP3a)
FINANCIAL_SECTORS = {"Financial Services"}


def load_reporting_model_overrides(path=None) -> dict:
    """{company name: reason} for companies.yaml entries that declare
    `reporting_model: financial`. Needed because the free sector source
    misclassifies some (yfinance calls Adyen 'Technology' although its balance
    sheet is EUR 6.4bn of merchant payables against EUR 0.3bn of trade
    payables). The override lives in reviewable config, with a stated reason,
    not in code."""
    path = Path(path) if path else COMPANIES_YAML
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = {}
    for stem, entry in (cfg.get("companies") or {}).items():
        entry = entry or {}
        model = entry.get("reporting_model")
        if not model:
            continue
        if str(model).strip().lower() != "financial":
            print(f"*** companies.yaml: '{stem}' has unknown reporting_model {model!r} - "
                  f"ignored (only 'financial' is recognised)")
            continue
        overrides[entry.get("name", stem)] = (
            entry.get("reporting_model_reason") or "reporting_model: financial in companies.yaml")
    return overrides


def fetch_company_profiles(engine) -> pd.DataFrame:
    """company_id, name, sector_std. sector_std comes from migration 002, so a
    database built only from schema.sql lacks it - degrade LOUDLY (sector-based
    gating off, config overrides still apply) instead of crashing the whole
    ratio run."""
    try:
        return pd.read_sql(text("SELECT company_id, name, sector_std FROM company"), engine)
    except Exception as e:
        print(f"*** could not read company.sector_std ({type(e).__name__}) - sector-based "
              f"financial gating is OFF; only companies.yaml overrides apply")
        profiles = pd.read_sql(text("SELECT company_id, name FROM company"), engine)
        profiles["sector_std"] = None
        return profiles


def financial_company_reasons(profiles: pd.DataFrame, overrides: dict) -> dict:
    """{company_id: basis} for every company treated as financial. An explicit
    override (with its stated reason) outranks the sector label."""
    reasons = {}
    for _, row in profiles.iterrows():
        cid, name, sector = int(row["company_id"]), row["name"], row.get("sector_std")
        if name in overrides:
            reasons[cid] = overrides[name]
        elif isinstance(sector, str) and sector in FINANCIAL_SECTORS:
            reasons[cid] = f"sector_std is '{sector}'"
    return reasons


def gate_financial_ratios(ratios: pd.DataFrame, financial: dict):
    """Blank (NaN) the FINANCIAL_NOT_MEANINGFUL ratios for financial companies.

    Returns (gated, notes, n_blanked). `notes` maps (company_id, year,
    ratio_name) -> the stored reason for EVERY gated cell (so the dashboard can
    say why it reads n/a even where no value existed); `n_blanked` counts only
    the values that actually existed and were removed. Private `_` columns are
    left alone - valuation reads absolute values from them."""
    gated = ratios.copy()
    notes, n_blanked = {}, 0
    if not financial:
        return gated, notes, 0
    is_fin = gated["company_id"].isin(list(financial))
    for ratio_name in FINANCIAL_NOT_MEANINGFUL:
        if ratio_name in gated.columns:
            n_blanked += int((is_fin & gated[ratio_name].notna()).sum())
            gated.loc[is_fin, ratio_name] = float("nan")
    for _, row in gated.loc[is_fin, ["company_id", "year"]].iterrows():
        text_note = f"Not meaningful for a financial company ({financial[int(row['company_id'])]})"
        for ratio_name in FINANCIAL_NOT_MEANINGFUL:
            notes[(int(row["company_id"]), int(row["year"]), ratio_name)] = text_note
    return gated, notes, n_blanked


def format_ratio(value, unit):
    if pd.isna(value):
        return "n/a"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "x":
        return f"{value:.1f}x"
    if unit == "days":
        return f"{value:.0f}d"
    return f"{value:.2f}"


def print_comps_table(ratios: pd.DataFrame):
    """Print a clean side-by-side comps table to the terminal."""
    for ratio_name, (label, neutral, unit) in RATIO_META.items():
        if ratio_name not in ratios.columns:
            continue
        subset = ratios[["company", "year", ratio_name]].dropna(subset=[ratio_name])
        if subset.empty:
            continue
        pivot = subset.pivot_table(
            index="company", columns="year",
            values=ratio_name, aggfunc="first"
        ).sort_index(axis=1)

        print(f"\n{label}" + ("  [currency-neutral]" if neutral else "  [NOT currency-neutral]"))
        print("-" * 70)
        for company, row in pivot.iterrows():
            vals = "  ".join(format_ratio(v, unit).rjust(10) for v in row)
            years = "  ".join(str(y).rjust(10) for y in pivot.columns)
            print(f"  {'':2s}{company:<22s} {vals}")
        # print year header once per ratio
        print(f"\n  {'':24s} {years}")


# ---------------------------------------------------------------- save

def save_to_db(engine, ratios: pd.DataFrame, company_ids: dict, notes: dict = None):
    """Upsert ratios into the ratio table. `notes` maps (company_id, year,
    ratio_name) -> why that cell is blank on purpose (see
    gate_financial_ratios); rows without one get NULL, so a company that stops
    being gated loses its note on the next run."""
    notes = notes or {}
    # create the ratio table if it doesn't exist
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ratio (
                ratio_id            SERIAL PRIMARY KEY,
                company_id          INTEGER REFERENCES company(company_id),
                year                INTEGER NOT NULL,
                ratio_name          TEXT NOT NULL,
                display_label       TEXT NOT NULL,
                value               NUMERIC,
                is_currency_neutral BOOLEAN DEFAULT TRUE,
                currency            TEXT,
                source_concepts     TEXT[],
                computed_at         TIMESTAMP DEFAULT now(),
                note                TEXT,
                UNIQUE(company_id, year, ratio_name)
            )
        """))
        # additive + idempotent: a table created before the gating fix lacks it
        conn.execute(text("ALTER TABLE ratio ADD COLUMN IF NOT EXISTS note TEXT"))
        conn.commit()

    rows_written = 0
    with engine.connect() as conn:
        for _, row in ratios.iterrows():
            cid = row.get("company_id")
            year = row["year"]
            for ratio_name, (label, neutral, unit) in RATIO_META.items():
                if ratio_name not in row.index:
                    continue
                val = row[ratio_name]
                conn.execute(text("""
                    INSERT INTO ratio
                        (company_id, year, ratio_name, display_label,
                         value, is_currency_neutral, computed_at, note)
                    VALUES
                        (:cid, :year, :rn, :label, :val, :neutral, now(), :note)
                    ON CONFLICT (company_id, year, ratio_name)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        display_label = EXCLUDED.display_label,
                        note = EXCLUDED.note,
                        computed_at = now()
                """), {"cid": int(cid), "year": int(year), "rn": ratio_name,
                       "label": label, "val": None if pd.isna(val) else float(val),
                       "neutral": neutral, "note": notes.get((int(cid), int(year), ratio_name))})
                rows_written += 1
        conn.commit()
    return rows_written


def sanitize_sheet_name(name: str) -> str:
    """Excel sheet names can't contain \\ / ? * [ ] : and are capped at 31
    chars. Strip the illegal characters rather than trusting every future
    RATIO_META label to avoid them - this is exactly the kind of bug a new
    ratio's label (like DSO's original 'O/S' abbreviation) can reintroduce
    without anyone noticing until save_to_excel crashes AFTER the DB write
    already succeeded."""
    for ch in '\\/?*[]:':
        name = name.replace(ch, "")
    return name[:31]


def save_to_excel(ratios: pd.DataFrame, out_path: str):
    """Save one sheet per ratio, plus a summary sheet."""
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        # summary sheet: all ratios, all companies, most recent year
        most_recent = ratios.groupby("company")["year"].max().reset_index()
        most_recent.columns = ["company", "max_year"]
        latest = ratios.merge(most_recent, left_on=["company", "year"],
                              right_on=["company", "max_year"])
        ratio_cols = [c for c in RATIO_META if c in latest.columns]
        summary = latest[["company", "year"] + ratio_cols].set_index("company")
        # format for display
        display = summary.copy()
        for ratio_name, (label, neutral, unit) in RATIO_META.items():
            if ratio_name in display.columns:
                display[ratio_name] = display[ratio_name].apply(
                    lambda v: format_ratio(v, unit))
        display.columns = [RATIO_META.get(c, (c,))[0] if c in RATIO_META else c
                           for c in display.columns]
        display.to_excel(writer, sheet_name=sanitize_sheet_name("Summary (latest year)"))

        # one sheet per ratio showing all companies and years
        for ratio_name, (label, neutral, unit) in RATIO_META.items():
            if ratio_name not in ratios.columns:
                continue
            pivot = ratios.pivot_table(
                index="company", columns="year",
                values=ratio_name, aggfunc="first"
            ).sort_index(axis=1)
            pivot = pivot.map(lambda v: format_ratio(v, unit))
            sheet_name = sanitize_sheet_name(label)
            pivot.to_excel(writer, sheet_name=sheet_name)

    print(f"Saved to {out_path}")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only compute ratios for this company")
    ap.add_argument("--no-db", action="store_true",
                    help="Skip writing to the database, produce Excel only")
    ap.add_argument("--out", default="data/raw/comps_ratios.xlsx",
                    help="Output Excel path")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)

    engine = create_engine(db_url)

    print("Fetching facts from database...")
    df = fetch_facts(engine, company_filter=args.company)
    if df.empty:
        print("No data found. Check that companies are loaded.")
        sys.exit(1)

    print(f"Loaded {len(df)} facts across "
          f"{df['company'].nunique()} companies and "
          f"{df['year'].nunique()} years\n")

    wide, conflicts = pivot_to_wide(df, return_conflicts=True)
    print_conflict_summary(conflicts)
    ratios = compute_ratios(wide)

    financial = financial_company_reasons(fetch_company_profiles(engine), load_reporting_model_overrides())
    ratios, notes, n_blanked = gate_financial_ratios(ratios, financial)
    gated_names = sorted(ratios.loc[ratios["company_id"].isin(list(financial)), "company"].unique())
    if gated_names:
        print(f"Financial-sector gating: blanked {n_blanked} value(s) that are not meaningful for "
              f"{', '.join(gated_names)} (working-capital, gross-margin, cash-conversion, ROIC, "
              f"net-debt/EBIT - reason stored in ratio.note)\n")

    # print the comps table to terminal
    years = sorted(ratios["year"].unique())
    print(f"{'=' * 70}")
    print(f"COMPS TABLE  |  Companies: {ratios['company'].nunique()}  |  Years: {min(years)}-{max(years)}")
    print(f"{'=' * 70}")
    print_comps_table(ratios)

    # save to database
    if not args.no_db:
        rows = save_to_db(engine, ratios, {}, notes)
        print(f"\nWrote {rows} ratio rows to database")

    # save to Excel
    out_path = args.out
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    save_to_excel(ratios, out_path)
