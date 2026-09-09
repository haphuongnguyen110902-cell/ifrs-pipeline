"""
scripts/27_onepager.py

WHAT
----
Generates one polished, single-page PDF per company - a real IB/PE-style
"Trading Comps & DCF Summary" tearsheet - pulling numbers already computed
and persisted by Phases 4-9 (19_valuation.py, 22_dcf.py, 24_credit.py,
15_forensics.py, 20_precedents.py). Nothing here is new analysis; this is
a presentation layer on top of what already exists in the DB, same
relationship webapp/app.py has to the pipeline (ROADMAP.md Phase 10).

WHY THIS SPECIFICALLY, NOW
---------------------------
Everything above lives in Excel/DB/terminal/a web dashboard - a real
analyst deliverable for an interview or application is a document you
hand someone, not a link they have to click through nine tabs to
understand. PLAN.md WP6 / ROADMAP.md Phase 10 rate this the single
highest-leverage-per-hour item for interviews specifically, and it's
placed deliberately BEFORE Phase 11's breadth work because it serves the
Contrôleur de Gestion job search directly, which breadth does not.

THE ACTUAL DELIVERABLE IS THE RECONCILIATION, NOT THE NUMBERS
----------------------------------------------------------------
A one-pager that prints three valuation methods with no view connecting
them is not an analyst's output (this script's own instructions say so
explicitly). Each PDF's headline section states the DCF Enterprise
Value, the peer-implied EV (from trading comps), and the company's
actual market EV side by side, with one sentence on whether they
converge or an explicit reason why they don't (illustrative-only D&A
fallback, no peer group, base year mismatch, etc.) - never silently
picking one number and hiding the other two.

NEVER FABRICATE A MISSING NUMBER
----------------------------------
Amplifon and Shell have no DCF/3-statement model (no COGS/gross-profit
split in their filings at all - see README's Known limitations) - their
one-pagers say so explicitly in the DCF section rather than being
silently blank or, worse, showing a guessed number.

Usage:
    python scripts/27_onepager.py                      # all companies
    python scripts/27_onepager.py --company "L'Oreal"
    python scripts/27_onepager.py --out-dir data/raw/onepagers
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------- fetch

def fetch_company_meta(engine, company: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT company_id, name, country, sector, sector_std, ticker FROM company WHERE name = :n"
        ), {"n": company}).fetchone()
    return dict(row._mapping) if row else None


def fetch_latest_ratios(engine, company_id: int) -> dict:
    """One row per ratio_name, latest year that ratio_name has a
    non-NULL value - same NULL-skip-before-picking-latest fix as
    sql/schema_screener.sql (PLAN.md WP5) and
    21_three_statement_model.py's select_base_year_row(), applied here
    too rather than assumed unnecessary."""
    df = pd.read_sql(text("""
        SELECT DISTINCT ON (ratio_name) ratio_name, display_label, value, year
        FROM ratio WHERE company_id = :cid AND value IS NOT NULL
        ORDER BY ratio_name, year DESC
    """), engine, params={"cid": company_id})
    return {r["ratio_name"]: r for _, r in df.iterrows()}


def fetch_latest_valuation(engine, company: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM valuation WHERE company = :c ORDER BY year DESC LIMIT 1"
        ), {"c": company}).fetchone()
    return dict(row._mapping) if row else None


def fetch_latest_dcf(engine, company: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM dcf_valuation WHERE company = :c ORDER BY base_year DESC LIMIT 1"
        ), {"c": company}).fetchone()
    return dict(row._mapping) if row else None


def fetch_latest_credit(engine, company: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM credit_profile WHERE company = :c ORDER BY year DESC LIMIT 1"
        ), {"c": company}).fetchone()
    return dict(row._mapping) if row else None


def fetch_flag_summary(engine, company_id: int) -> dict:
    df = pd.read_sql(text("""
        SELECT severity, COUNT(*) AS n FROM forensics_flag
        WHERE company_id = :cid GROUP BY severity
    """), engine, params={"cid": company_id})
    counts = {row["severity"]: int(row["n"]) for _, row in df.iterrows()}
    top = pd.read_sql(text("""
        SELECT year, label, severity, detail FROM forensics_flag
        WHERE company_id = :cid AND severity = 'high'
        ORDER BY year DESC LIMIT 3
    """), engine, params={"cid": company_id})
    return {"counts": counts, "top_high": top.to_dict("records")}


def fetch_matching_precedent(engine, sector_detail: str) -> dict:
    """Matches on the free-text `sector` column (precedent_transaction
    predates WP3a's sector_std) - only 2 curated deals exist today
    (20_precedents.py's own docstring: deliberately few, well-verified,
    not padded), so most companies will have none, which is correct, not
    a gap to fill with a guess."""
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM precedent_transaction WHERE sector = :s LIMIT 1"
        ), {"s": sector_detail}).fetchone()
    return dict(row._mapping) if row else None


def fetch_all_company_names(engine) -> list:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT name FROM company ORDER BY name"))]


# ---------------------------------------------------------------- assemble

def assemble_onepager_data(engine, company: str) -> dict:
    meta = fetch_company_meta(engine, company)
    if not meta:
        return None
    ratios = fetch_latest_ratios(engine, meta["company_id"])
    valuation = fetch_latest_valuation(engine, company)
    dcf = fetch_latest_dcf(engine, company)
    credit = fetch_latest_credit(engine, company)
    flags = fetch_flag_summary(engine, meta["company_id"])
    precedent = fetch_matching_precedent(engine, meta["sector"])

    return {
        "meta": meta, "ratios": ratios, "valuation": valuation,
        "dcf": dcf, "credit": credit, "flags": flags, "precedent": precedent,
    }


def build_reconciliation(data: dict) -> dict:
    """The actual deliverable, per this script's own docstring: state
    the DCF EV, the peer-implied EV, and the company's own market EV
    together, with one sentence on whether they converge - never pick
    one and hide the others. Returns {rows: [...], narrative: str}."""
    valuation = data["valuation"]
    dcf = data["dcf"]
    rows = []

    def _real(v):
        """True only for an actual usable number - `v is not None` alone
        is NOT enough: a real bug found running this on live data, not
        assumed - one company's implied_ev_from_peers came back as a
        genuine float NaN (not SQL NULL, so `is not None` let it through),
        which then crashed reportlab's own renderer deep inside the bar
        chart (`cannot convert float NaN to integer`) rather than being
        caught here as "not available"."""
        return v is not None and not pd.isna(v)

    # NUMERIC columns come back from psycopg2/SQLAlchemy as decimal.Decimal,
    # not float - Decimal doesn't mix with the float math the bar chart
    # and reportlab's own units (mm) do below, so every value is coerced
    # to plain float right here, once, rather than at each later use site.
    market_ev = valuation.get("ev_eur") if valuation else None
    if _real(market_ev):
        rows.append(("Actual market EV", float(market_ev), "today's market cap + latest net debt"))

    if dcf is not None and _real(dcf.get("enterprise_value")):
        rows.append(("DCF Enterprise Value", float(dcf["enterprise_value"]),
                      f"WACC {float(dcf['wacc']):.1%}, base year {dcf['base_year']}"))
    else:
        rows.append(("DCF Enterprise Value", None,
                      "not available - see DCF section for why"))

    implied_ev = valuation.get("implied_ev_from_peers") if valuation else None
    n_peers = valuation.get("n_peers_in_sector") if valuation else None
    if _real(implied_ev):
        rows.append(("Peer-implied EV", float(implied_ev),
                      f"peer median EV/EBITDA x own EBITDA, {n_peers} peers"))
    else:
        rows.append(("Peer-implied EV", None,
                      f"fewer than 2 sector peers ({n_peers or 0} found) - no meaningful peer median"))

    # Narrative: only compare values that actually exist - never invent
    # a comparison against a missing number.
    present = [(label, v) for label, v, _ in rows if _real(v)]
    if len(present) >= 2:
        spread = (max(v for _, v in present) - min(v for _, v in present)) / min(v for _, v in present)
        if spread < 0.15:
            narrative = (f"The {len(present)} valuation method(s) available converge within "
                         f"{spread:.0%} of each other - a consistent read on value.")
        else:
            narrative = (f"The {len(present)} valuation method(s) available diverge by {spread:.0%} - "
                         f"see each section below before treating any single number as definitive.")
    else:
        narrative = ("Only one valuation method is available for this company - see the "
                      "sections below for why the others aren't (data-completeness limitation, "
                      "not an oversight).")

    return {"rows": rows, "narrative": narrative}


# ---------------------------------------------------------------- format helpers

def fmt_eur(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    v = float(v)
    if abs(v) >= 1e9:
        return f"EUR {v/1e9:,.1f}bn"
    if abs(v) >= 1e6:
        return f"EUR {v/1e6:,.0f}m"
    return f"EUR {v:,.0f}"


def fmt_pct(v, decimals=1) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v):.{decimals}f}%"


def fmt_x(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v):.1f}x"


def fmt_days(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v):.0f}d"


RATIO_FORMATTERS = {
    "dso": fmt_days, "dio": fmt_days, "dpo": fmt_days, "ccc": fmt_days,
    "net_debt_ebitda_proxy": fmt_x,
}


def ratio_value(ratios: dict, name: str):
    r = ratios.get(name)
    if r is None:
        return None
    return r["value"]


def fmt_ratio(ratios: dict, name: str) -> str:
    v = ratio_value(ratios, name)
    formatter = RATIO_FORMATTERS.get(name, fmt_pct)
    return formatter(v)


# ---------------------------------------------------------------- PDF build

PAGE_MARGIN = 13 * mm

NAVY = colors.HexColor("#1a2a4a")
LIGHT_GREY = colors.HexColor("#f0f0f0")
MID_GREY = colors.HexColor("#666666")
RED = colors.HexColor("#b03030")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("OPTitle", parent=ss["Title"], fontSize=24, leading=28,
                           textColor=NAVY, spaceAfter=3))
    ss.add(ParagraphStyle("OPSubtitle", parent=ss["Normal"], fontSize=11,
                           textColor=MID_GREY, spaceAfter=12))
    ss.add(ParagraphStyle("OPSection", parent=ss["Heading2"], fontSize=12,
                           textColor=NAVY, spaceBefore=9, spaceAfter=5))
    ss.add(ParagraphStyle("OPBody", parent=ss["Normal"], fontSize=10, leading=13))
    ss.add(ParagraphStyle("OPNarrative", parent=ss["Normal"], fontSize=10, leading=13,
                           spaceAfter=6, spaceBefore=3))
    ss.add(ParagraphStyle("OPFooter", parent=ss["Normal"], fontSize=7.5,
                           textColor=MID_GREY, spaceBefore=8))
    ss.add(ParagraphStyle("OPFlag", parent=ss["Normal"], fontSize=9.5, leading=13))
    return ss


_CELL_STYLE = ParagraphStyle("OPCell", fontName="Helvetica", fontSize=10, leading=12)
_CELL_STYLE_BOLD = ParagraphStyle("OPCellBold", fontName="Helvetica-Bold", fontSize=10,
                                   leading=12, textColor=NAVY)


def _metric_table(rows, col_widths, header=None):
    # Plain Python strings in a reportlab Table cell do NOT wrap - only
    # Paragraph flowables do (a real bug found running this: "Sector peer
    # median EV/EBITDA" ran straight into its value with no visible gap,
    # since the label just overflowed the column instead of wrapping).
    # Wrapping every cell in a Paragraph fixes it generally, not just for
    # this one label.
    def _cell(value, bold=False):
        # Escaped because Paragraph content is parsed as restricted XML -
        # a real bug found looking at the rendered PDF: an un-escaped "&"
        # in dynamic text (e.g. "D&A fallback") renders as "D&A;" (the
        # parser tries to read "&A;" as an XML entity), silently garbling
        # real analytical content rather than raising an error.
        return Paragraph(xml_escape(str(value)), _CELL_STYLE_BOLD if bold else _CELL_STYLE)

    data = []
    if header:
        data.append([_cell(h, bold=True) for h in header])
    for row in rows:
        data.append([_cell(v) for v in row])

    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        # bold/color already comes from _CELL_STYLE_BOLD above (Table-level
        # FONTNAME/TEXTCOLOR commands don't reach inside a Paragraph
        # flowable) - only the header's own underline rule belongs here.
        style += [("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY)]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle(style))
    return t


def build_valuation_bar_chart(reco_rows, width=170 * mm, row_height=9 * mm):
    """A 'football field' - the classic IB one-pager visual for exactly
    this reconciliation: one horizontal bar per valuation method, length
    proportional to its Enterprise Value, so the reader SEES the spread
    (or convergence) rather than only reading three numbers in a table.
    Built with reportlab's own shapes (no extra dependency) since the
    library's built-in HorizontalBarChart flowable doesn't give enough
    control over per-bar labels/colors for this layout. Only bars for
    methods that actually have a value are drawn - a missing method
    (e.g. no DCF for Amplifon/Shell) is omitted, never shown as a zero-
    length bar, which would misleadingly imply "valued at zero" rather
    than "not available"."""
    present = [(label, value) for label, value, _ in reco_rows if value is not None]
    if not present:
        return None

    label_col_width = 42 * mm
    bar_area_width = width - label_col_width - 25 * mm
    max_value = max(v for _, v in present)
    height = row_height * len(present) + 4 * mm

    d = Drawing(width, height)
    bar_colors = [NAVY, colors.HexColor("#3a6ea5"), colors.HexColor("#7fa8d9")]
    for i, (label, value) in enumerate(present):
        y = height - (i + 1) * row_height + 3 * mm
        bar_len = (value / max_value) * bar_area_width if max_value else 0
        d.add(String(0, y + 3, label, fontSize=8, fontName="Helvetica"))
        d.add(Rect(label_col_width, y, bar_len, row_height - 5 * mm,
                    fillColor=bar_colors[i % len(bar_colors)], strokeColor=None))
        d.add(String(label_col_width + bar_len + 3, y + 3, fmt_eur(value),
                      fontSize=8, fontName="Helvetica-Bold"))
    return d


def build_onepager_pdf(data: dict, out_path: str):
    meta = data["meta"]
    ratios = data["ratios"]
    valuation = data["valuation"]
    dcf = data["dcf"]
    credit = data["credit"]
    flags = data["flags"]
    precedent = data["precedent"]
    reco = build_reconciliation(data)

    ss = _styles()
    story = []

    story.append(Paragraph(xml_escape(meta["name"]), ss["OPTitle"]))
    subtitle = f"{meta['country']} · {meta['sector']}"
    if meta.get("ticker"):
        subtitle += f" · {meta['ticker']}"
    story.append(Paragraph(xml_escape(subtitle), ss["OPSubtitle"]))

    # --- Valuation reconciliation (the actual deliverable) ---
    story.append(Paragraph("Valuation Reconciliation", ss["OPSection"]))
    reco_rows = [[label, fmt_eur(value), note] for label, value, note in reco["rows"]]
    story.append(_metric_table(
        reco_rows, col_widths=[45 * mm, 30 * mm, 95 * mm],
        header=["Method", "Enterprise Value", "Basis"],
    ))
    story.append(Spacer(1, 6))
    chart = build_valuation_bar_chart(reco["rows"])
    if chart:
        story.append(chart)
    story.append(Spacer(1, 4))
    story.append(Paragraph(reco["narrative"], ss["OPNarrative"]))

    # --- Key ratios + Trading comps side by side ---
    ratio_rows = [
        ["Gross Margin", fmt_ratio(ratios, "gross_margin")],
        ["Operating Margin", fmt_ratio(ratios, "operating_margin")],
        ["Net Margin", fmt_ratio(ratios, "net_margin")],
        ["ROIC", fmt_ratio(ratios, "roic")],
        ["ROE", fmt_ratio(ratios, "roe")],
        ["Cash Conversion", fmt_ratio(ratios, "cash_conversion")],
        ["DSO / DIO / DPO", f"{fmt_ratio(ratios, 'dso')} / {fmt_ratio(ratios, 'dio')} / {fmt_ratio(ratios, 'dpo')}"],
        ["Cash Conversion Cycle", fmt_ratio(ratios, "ccc")],
        ["Effective Tax Rate", fmt_ratio(ratios, "tax_rate")],
    ]
    ratio_table = _metric_table(ratio_rows, col_widths=[42 * mm, 32 * mm], header=["Ratio", "Latest"])

    if valuation:
        comps_rows = [
            ["EV / EBITDA", fmt_x(valuation.get("ev_ebitda"))],
            ["EV / Sales", fmt_x(valuation.get("ev_sales"))],
            ["P/E", fmt_x(valuation.get("pe"))],
            ["Sector peer median EV/EBITDA", fmt_x(valuation.get("ev_ebitda_sector_median"))],
            ["Peers in sector", str(int(valuation["n_peers_in_sector"])) if pd.notna(valuation.get("n_peers_in_sector")) else "n/a"],
        ]
    else:
        comps_rows = [["Trading comps", "not computed for this company"]]
    comps_table = _metric_table(comps_rows, col_widths=[50 * mm, 32 * mm], header=["Trading Comps", "Value"])

    if credit:
        da_note = " (D&A fallback)" if credit.get("is_da_fallback") else ""
        credit_rows = [
            ["Net Debt / EBITDA", fmt_x(credit.get("net_debt_ebitda")) + da_note],
            ["Leverage Band", credit.get("band") or "n/a"],
            ["YoY Trend", credit.get("trend") or "n/a"],
        ]
    else:
        credit_rows = [["Credit profile", "not computed for this company"]]
    credit_table = _metric_table(credit_rows, col_widths=[50 * mm, 32 * mm], header=["Credit Profile", "Value"])

    right_col_stack = Table([[comps_table], [Spacer(1, 6)], [credit_table]], colWidths=[85 * mm])

    story.append(Paragraph("Fundamentals, Trading Comps &amp; Credit", ss["OPSection"]))
    two_col = Table([[ratio_table, right_col_stack]], colWidths=[85 * mm, 90 * mm])
    two_col.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (1, 0), (1, 0), 8)]))
    story.append(two_col)

    # --- Forensics ---
    counts = flags["counts"]
    flag_summary = f"{counts.get('high', 0)} high / {counts.get('medium', 0)} medium / {counts.get('low', 0)} low"
    story.append(Paragraph("Earnings-Quality Flags", ss["OPSection"]))
    flag_lines = [Paragraph(f"<b>{flag_summary}</b> (all years on record)", ss["OPFlag"])]
    for f in flags["top_high"]:
        # label/detail are dynamic text from forensics_flag - escaped
        # individually (not the whole string) so the literal &#8226;
        # bullet entity keeps working as markup, not literal text.
        flag_lines.append(Paragraph(
            f"&#8226; {f['year']} {xml_escape(f['label'])}: {xml_escape(f['detail'])}", ss["OPFlag"]))
    if not flags["top_high"] and counts.get("high", 0) == 0:
        flag_lines.append(Paragraph("No high-severity flags on record.", ss["OPFlag"]))
    for line in flag_lines:
        story.append(line)

    if precedent:
        story.append(Spacer(1, 4))
        # KeepTogether: a section heading orphaned alone at the bottom of
        # the page, with its own content pushed to a near-empty next page,
        # is worse than moving both together - found by actually looking
        # at the rendered PDF (L'Oreal's "Precedent Transaction" heading
        # landed right at the page-1 boundary with its one paragraph
        # spilling to page 2 alone).
        story.append(KeepTogether([
            Paragraph("Precedent Transaction (same sector)", ss["OPSection"]),
            Paragraph(
                f"{xml_escape(precedent['deal'])} ({precedent['announced']}): "
                f"EV/EBITDA {fmt_x(precedent.get('ev_ebitda'))}"
                f"{' (estimate)' if precedent.get('ev_ebitda_is_estimate') else ''}, "
                f"source: {xml_escape(precedent.get('source') or 'n/a')}",
                ss["OPBody"],
            ),
        ]))

    story.append(Paragraph(
        "Computed by an automated IFRS/XBRL pipeline from ESEF regulatory filings - "
        "not investment advice. See the project's README/ROADMAP for methodology, "
        "data-completeness notes, and known limitations.",
        ss["OPFooter"],
    ))

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        title=f"{meta['name']} - Trading Comps & DCF Summary",
    )
    doc.build(story)


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="Only generate this one company's one-pager")
    ap.add_argument("--out-dir", default="data/raw/onepagers")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found.")
        sys.exit(1)
    engine = create_engine(db_url)

    companies = [args.company] if args.company else fetch_all_company_names(engine)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    n_ok, n_skipped = 0, 0
    for company in companies:
        data = assemble_onepager_data(engine, company)
        if data is None:
            print(f"  *** '{company}' not found in company table - skipped")
            n_skipped += 1
            continue
        slug = company.replace("'", "").replace(" ", "_").replace("/", "-")
        out_path = str(Path(args.out_dir) / f"{slug}.pdf")
        build_onepager_pdf(data, out_path)
        print(f"{company:20s} -> {out_path}")
        n_ok += 1

    print(f"\n{n_ok} one-pager(s) generated, {n_skipped} skipped.")
