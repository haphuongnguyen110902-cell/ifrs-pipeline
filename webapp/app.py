"""
app.py

WHAT
----
The "walking skeleton" from ROADMAP.md Phase 2: a thin but REAL public
website. Pick a company (filterable by country/sector), see its ratios
across years and its forensics flags. Nothing here is new analysis -
every number already exists in the `company` and `ratio` tables; this
is the first UI layer on top of them.

WHY
---
Proves the filter-by-country/sector vision works end to end on real
data, and gives every phase after this one (comps, DCF, market risk...)
a running app to add its output to, instead of staying isolated in
Excel/DB until some later "build the dashboard" phase. See ROADMAP.md's
"Vision" section for the full reasoning.

Deploy: push to GitHub, then deploy for free at share.streamlit.io,
Main file path = webapp/app.py. Streamlit Cloud auto-detects
webapp/requirements.txt (a light dependency set, NOT the repo root's
requirements.txt which includes the heavy arelle dependency the pipeline
scripts need but this dashboard doesn't) because it searches the
entrypoint's own directory before falling back to the repo root - see
webapp/requirements.txt for why this file lives here specifically.
Set DATABASE_URL in the app's Secrets (same value as your local .env) -
never commit it to the repo.

Run locally (from the repo root, so paths match Streamlit Cloud):
    streamlit run webapp/app.py
"""
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

st.set_page_config(page_title="IFRS Pipeline", page_icon="📊", layout="wide")

SCREENER_SCHEMA = Path(__file__).parent / ".." / "sql" / "schema_screener.sql"


@st.cache_resource
def get_engine():
    load_dotenv()
    db_url = None
    try:
        db_url = st.secrets["DATABASE_URL"]
    except Exception:
        pass
    db_url = db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        st.error(
            "DATABASE_URL not found. Locally: set it in .env. "
            "On Streamlit Cloud: add it under this app's Settings → Secrets."
        )
        st.stop()
    # This engine is cached for the life of the app, but Neon (serverless
    # Postgres) closes idle connections. Without pre-ping the pool hands out
    # a dead one and the first click after a quiet spell raises
    # OperationalError / "connection already closed" - seen live: selecting a
    # company crashed the Ratios tab, then worked after a reload. pre_ping
    # tests the connection on checkout and transparently reconnects;
    # pool_recycle retires connections before Neon's idle timeout would.
    return create_engine(db_url, pool_pre_ping=True, pool_recycle=300)


@st.cache_data(ttl=3600)
def load_companies(_engine):
    return pd.read_sql(text(
        "SELECT company_id, name, country, sector FROM company ORDER BY name"
    ), _engine)


@st.cache_resource
def ensure_screener_view(_engine):
    """Creates/replaces company_latest_metrics (PLAN.md WP5, see
    sql/schema_screener.sql) - idempotent (CREATE OR REPLACE VIEW), and
    cached with st.cache_resource (not cache_data) since it's a one-time
    side effect against the DB, not data to reuse across reruns."""
    ddl = SCREENER_SCHEMA.read_text(encoding="utf-8")
    with _engine.begin() as conn:
        conn.execute(text(ddl))
    return True


@st.cache_data(ttl=3600)
def load_screener(_engine):
    """One query for the whole universe (PLAN.md WP5's own requirement -
    the landing page must not do one query per company)."""
    return pd.read_sql(text("SELECT * FROM company_latest_metrics ORDER BY name"), _engine)


@st.cache_data(ttl=3600)
def load_ratios(_engine, company_id: int):
    # SELECT *: the optional `note` column (why a ratio is blank on purpose -
    # see gating_caption) only exists once 11_ratio_engine.py has run since
    # the financial-sector gating fix. Naming it here would break this query,
    # and so the whole tab, on a database that hasn't been recomputed yet.
    return pd.read_sql(text(
        "SELECT * FROM ratio WHERE company_id = :cid ORDER BY year"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def last_updated(_engine):
    df = pd.read_sql(text("SELECT MAX(computed_at) AS ts FROM ratio"), _engine)
    ts = df["ts"].iloc[0]
    return ts.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(ts) else "unknown"


# ---------------------------------------------------------------- Phases 4-6 loaders
# Added so the dashboard actually shows the comps/3-statement/DCF work
# (Phases 4-6), not just the Phase 2 ratio browser this file started as -
# see this module's own docstring: every phase after Phase 2 was meant to
# "add its output to" this app rather than stay isolated in Excel/DB, and
# that hadn't actually happened until now.
#
# company_id-keyed as of PLAN.md WP1 (previously `company` TEXT, matching
# how 19_valuation.py/21_three_statement_model.py/22_dcf.py/23_market_risk.py/
# 24_credit.py used to write them - all five now also write company_id
# alongside the legacy name column). Signature changed from company_name
# to company_id deliberately, per WP1's own wording, "so a name can no
# longer be passed by accident" - the same accent-drift/rename failure
# mode `ratio`'s company_id key was already immune to. Still returns an
# EMPTY frame rather than raising when nothing's been computed for that
# company yet - every render_* below treats empty as "not computed", not
# an error.

@st.cache_data(ttl=3600)
def load_comps(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT * FROM valuation WHERE company_id = :cid ORDER BY year DESC LIMIT 1"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_three_statement(_engine, company_id: int):
    # Only the run from the latest base year: the table keeps earlier runs on purpose (schema_three_statement.sql), and
    # once a newer annual report moves the base year, returning every run would show two overlapping projections.
    return pd.read_sql(text(
        "SELECT * FROM three_statement_projection WHERE company_id = :cid "
        "AND base_year = (SELECT MAX(base_year) FROM three_statement_projection WHERE company_id = :cid) "
        "ORDER BY forecast_year ASC"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_dcf(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT * FROM dcf_valuation WHERE company_id = :cid ORDER BY base_year DESC LIMIT 1"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_market_risk(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT * FROM market_risk WHERE company_id = :cid ORDER BY computed_at DESC LIMIT 1"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_credit_profile(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT * FROM credit_profile WHERE company_id = :cid ORDER BY year ASC"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_backtest(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT ratio_name, method, n_folds, mae, rmse, bias, mape, is_winner, confidence "
        "FROM backtest WHERE company_id = :cid ORDER BY ratio_name, method"
    ), _engine, params={"cid": company_id})


@st.cache_data(ttl=3600)
def load_precedents(_engine):
    return pd.read_sql(text(
        "SELECT deal, sector, acquirer, target, announced, ev_eur_m, ev_sales, ev_ebitda, "
        "ev_ebitda_is_estimate, current_trading_comps_ev_sales_median, "
        "implied_control_premium_pct, source FROM precedent_transaction ORDER BY announced DESC"
    ), _engine)


def format_eur(value) -> str:
    """EUR value in native units (not millions) -> a readable bn/m string.
    Every DB column this reads (market_cap_eur, ev_eur, revenue, dcf
    enterprise_value, ...) is stored in native EUR, not millions."""
    if pd.isna(value):
        return "n/a"
    value = float(value)
    if abs(value) >= 1e9:
        return f"€{value / 1e9:,.1f}bn"
    if abs(value) >= 1e6:
        return f"€{value / 1e6:,.0f}m"
    return f"€{value:,.0f}"


NOT_SHOWN = "n/a*"

LEVERAGE_FOOTNOTE = (
    "* n/a*: this company's statements do not print depreciation and amortisation separately, so EBITDA "
    "cannot be derived. A multiple built on EBIT but labelled EBITDA would overstate leverage, so it is not "
    "shown; net debt and EBIT are still shown, and \"Net Debt vs Op. Profit\" on the Ratios tab is on an "
    "EBIT basis. Net debt here includes IFRS 16 lease liabilities, whereas many companies' own headline "
    "\"net financial debt\" excludes them (LVMH 2024: about €9.2bn as reported, about €31bn here, of which "
    "€17.8bn is leases), so figures can differ from a company's press release."
)


def _is_true(value) -> bool:
    return bool(value) if pd.notna(value) else False


def ebitda_multiple_text(value, is_da_fallback, fmt: str = "{:.1f}x") -> str:
    """A multiple whose denominator is EBITDA, as a visitor sees it. Where D&A is not printed separately the
    pipeline's "EBITDA" is really EBIT (flagged is_da_fallback), so the multiple is overstated - LVMH showed
    1.65x on EBIT against roughly 1.0x on its own operating cash flow. Shown as n/a* instead of a figure that
    reads as EBITDA-based but is not."""
    if _is_true(is_da_fallback):
        return NOT_SHOWN
    return fmt.format(value) if pd.notna(value) else "n/a"


def leverage_label(label, is_da_fallback) -> str:
    """A band / trend derived from such a multiple: hidden for the same reason."""
    if _is_true(is_da_fallback):
        return NOT_SHOWN
    return str(label) if pd.notna(label) else "n/a"


def format_pct_fraction(value) -> str:
    """For columns stored as a FRACTION (0.0713, not 7.13) - dcf_valuation's
    wacc/cost_of_equity/pct_ev_from_terminal are all fractions."""
    if pd.isna(value):
        return "n/a"
    return f"{float(value):.1%}"


def format_ratio_value(ratio_name: str, value) -> str:
    """Same unit logic as 11_ratio_engine.py's format_ratio() - days
    ratios (DSO/DIO/DPO/CCC) get a 'd' suffix, leverage gets 'x',
    everything else is a percentage. Kept as a small local copy rather
    than importing 11_ratio_engine.py here, since that module pulls in
    heavier dependencies (arelle-adjacent imports elsewhere in the
    scripts/ chain) not worth loading just for formatting."""
    if pd.isna(value):
        return "n/a"
    if ratio_name in ("dso", "dio", "dpo", "ccc"):
        return f"{value:.0f}d"
    if ratio_name == "net_debt_ebitda_proxy":
        return f"{value:.1f}x"
    return f"{value:.1f}%"


def gating_caption(ratios: pd.DataFrame):
    """Why some ratios read n/a ON PURPOSE (not because data is missing), or
    None. `note` is written by 11_ratio_engine.py (gate_financial_ratios and
    net_margin_notes); a database the engine hasn't run against since that fix
    has no such column, which just means no caption."""
    if "note" not in ratios.columns:
        return None
    gated = ratios.dropna(subset=["note"])
    if gated.empty:
        return None
    labels = ", ".join(sorted(gated["display_label"].unique()))
    reasons = "; ".join(sorted(gated["note"].unique()))
    return f"Blank on purpose, not missing data: {labels}. {reasons}."


BROADER_PAYABLES = ("trade_and_other_current_payables", "other_current_payables")


def payables_basis_caption(ratios: pd.DataFrame):
    """A caption when this company's days-payables figures are built on a broader line than trade payables, or None.
    `source_concepts` (which stored line DPO was read from) exists once the ratio calculation has run since that was
    added; a database without it, or a company on trade payables alone, just has no caption."""
    if "source_concepts" not in ratios.columns:
        return None
    dpo = ratios[ratios["ratio_name"] == "dpo"].dropna(subset=["source_concepts"])
    broader = [r for r in dpo["source_concepts"] if any(c in BROADER_PAYABLES for c in r)]
    if not broader:
        return None
    return ("Days payables outstanding and the cash conversion cycle are built on the balance-sheet line tagged \"trade "
            "and other payables\". Depending on the company that line holds trade payables only or also other operating "
            "payables (accruals, taxes, social charges), so these two figures may not be comparable with companies that "
            "report trade payables on their own line.")


def equity_basis_caption(ratios: pd.DataFrame):
    """A caption when this company's ROIC/ROE are built on total equity rather than a printed equity-attributable-to-
    owners split, or None. Only happens where a reviewed check confirmed the company has no non-controlling interests
    to carve out - a company that simply doesn't disclose the split stays blank instead, with no caption to show."""
    if "source_concepts" not in ratios.columns:
        return None
    hits = ratios[ratios["ratio_name"].isin(["roic", "roe"])].dropna(subset=["source_concepts"])
    flagged = [r for r in hits["source_concepts"] if any("non-controlling" in c for c in r)]
    if not flagged:
        return None
    return ("ROIC and ROE here use total equity as equity attributable to owners of the parent: this company's own "
            "statements were checked and confirmed to carry no non-controlling interests, so the two are the same "
            "number.")


def net_basis_caption(ratios: pd.DataFrame):
    """A caption when a net margin here excludes a discontinued operation (IFRS 5), or None. `source_concepts` names the
    continuing-operations basis only in the years where one was taken out."""
    if "source_concepts" not in ratios.columns:
        return None
    hits = ratios[ratios["ratio_name"] == "net_margin"].dropna(subset=["source_concepts"])
    years = sorted(int(y) for y, src in zip(hits["year"], hits["source_concepts"])
                   if any("continuing operations" in c for c in src))
    if not years:
        return None
    return (f"Net margin in {', '.join(map(str, years))} uses profit attributable to owners from continuing "
            f"operations: the company reported a discontinued operation (IFRS 5), which is left out of revenue, so "
            f"its result is left out of the profit too.")


def ebit_basis_caption(ratios: pd.DataFrame):
    """A caption when this company prints no operating-profit line and its EBIT is derived, or None. `source_concepts`
    of operating_margin names the derived basis in the years where it was used."""
    if "source_concepts" not in ratios.columns:
        return None
    hits = ratios[ratios["ratio_name"] == "operating_margin"].dropna(subset=["source_concepts"])
    if not any(any("no operating profit printed" in c for c in src) for src in hits["source_concepts"]):
        return None
    return ("This company's income statement has no operating-profit line. Operating profit (EBIT) here is profit "
            "before tax plus finance costs, both printed lines; the operating margin, ROIC, EBITDA and the multiples "
            "built on them use it.")


RATIO_BASIS_CAPTION = ("Margins and the tax rate use each year as the latest report presents it, restatements "
                       "included, so years compare like for like. Ratios that combine the balance sheet with a flow "
                       "(days, ROIC, ROE, leverage) use each year's own report: when a business is discontinued, IFRS 5 "
                       "re-presents earlier income statements but not earlier balance sheets. The effective tax "
                       "rate is tax expense over profit before deducting tax (IAS 12.86), share of associates "
                       "included, so it can differ slightly from a rate a company computes on its own subtotal.")


def render_ratio_table(ratios: pd.DataFrame):
    if ratios.empty:
        st.info("No ratios computed yet for this company.")
        return
    # dropna=False: pivot_table's default (True) silently DROPS any ratio
    # that's all-NaN for this company - found by actually clicking through
    # the live app (Amplifon showed 5 of its 12 ratios with no indication
    # 7 were missing, not zero). A ratio with no matching XBRL tag for this
    # company should show as "n/a" (format_ratio_value already handles
    # pd.isna -> "n/a" per cell below), never disappear silently - see
    # CLAUDE.md's "prefer an explicit not available state" principle.
    pivot = ratios.pivot_table(index="display_label", columns="year", values="value",
                                aggfunc="first", dropna=False)
    pivot = pivot.sort_index(axis=1)
    # object dtype from the start - pivot's columns are float64 (raw
    # numeric ratio values), and pandas rejects assigning formatted
    # strings ('45d', '74.3%') into a float64 column in place, even via
    # .loc. Building fresh with dtype=object avoids that entirely.
    display = pd.DataFrame(index=pivot.index, columns=pivot.columns, dtype=object)
    for row_label in display.index:
        ratio_name = ratios[ratios["display_label"] == row_label]["ratio_name"].iloc[0]
        display.loc[row_label] = [format_ratio_value(ratio_name, v) for v in pivot.loc[row_label]]
    st.dataframe(display, width="stretch")
    caption = gating_caption(ratios)
    if caption:
        st.caption(caption)
    payables_caption = payables_basis_caption(ratios)
    if payables_caption:
        st.caption(payables_caption)
    equity_caption = equity_basis_caption(ratios)
    if equity_caption:
        st.caption(equity_caption)
    net_caption = net_basis_caption(ratios)
    if net_caption:
        st.caption(net_caption)
    ebit_caption = ebit_basis_caption(ratios)
    if ebit_caption:
        st.caption(ebit_caption)
    st.caption(RATIO_BASIS_CAPTION)


@st.cache_data(ttl=3600)
def load_forensics(_engine, company_id: int):
    """Reads the persisted forensics_flag table (PLAN.md WP2) instead of
    importlib-loading 15_forensics.py and recomputing on every render -
    that used to couple the web layer to scripts/'s directory layout and
    made a "every company with >= 2 HIGH flags" screener query impossible
    with nothing stored. Same company_id-keyed pattern as the other
    Phase 4-6 loaders above."""
    return pd.read_sql(text(
        "SELECT year, flag_id, label, severity, value, detail, what_to_check "
        "FROM forensics_flag WHERE company_id = :cid ORDER BY year DESC"
    ), _engine, params={"cid": company_id})


def render_forensics(flags: pd.DataFrame):
    if flags.empty:
        st.success("No flags - nothing unusual detected in this company's ratio history "
                    "(or not enough ratio history yet to compute any).")
        return

    severity_order = {"high": 0, "medium": 1, "low": 2}
    flags = flags.assign(_sev=flags["severity"].map(severity_order)).sort_values(
        ["_sev", "year"], ascending=[True, False])

    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for _, f in flags.iterrows():
        with st.expander(f"{icon.get(f['severity'], '⚪')} {f['year']} — {f['label']} ({f['severity']})"):
            st.write(f["detail"])
            if f.get("what_to_check"):
                st.caption(f"What to check: {f['what_to_check']}")


EBITDA_FALLBACK_NOTE = (
    "EV/EBITDA is not shown for this company: no depreciation & amortisation was found in its "
    "filing, so EBITDA can't be built (it would just equal EBIT and overstate the multiple). "
    "It is also left out of the sector peer medians. EV/EBIT is shown instead.")


def peer_comparison_text(r):
    """The sector peer comparison sentence, or None when there are fewer than
    2 usable peers. Prefers the EV/EBITDA basis; a company whose EBITDA can't
    be built (no D&A in its filing) gets the same comparison on EV/EBIT,
    labelled as such, instead of a wrong EBITDA one or none at all."""
    bases = (
        ("EV/EBITDA", "n_peers_in_sector", "ev_ebitda_sector_median",
         "implied_ev_from_peers", "premium_vs_peers_pct", ""),
        ("EV/EBIT", "n_peers_ebit_in_sector", "ev_ebit_sector_median",
         "implied_ev_from_peers_ebit", "premium_vs_peers_ebit_pct",
         " (EV/EBIT basis - this company's EBITDA isn't available)"),
    )
    for label, n_col, median_col, implied_col, premium_col, suffix in bases:
        n, implied, premium = r.get(n_col), r.get(implied_col), r.get(premium_col)
        if pd.isna(n) or n < 2 or pd.isna(implied) or pd.isna(premium):
            continue
        return (f"**Sector peer comparison**{suffix} - {int(n)} peers in {r['sector']}: "
                f"peer median {label} {r[median_col]:.1f}x implies an EV of {format_eur(implied)}; "
                f"this company trades at a {premium:+.0f}% "
                f"{'premium' if premium >= 0 else 'discount'} to that.")
    return None


def ebitda_is_fallback(row) -> bool:
    """True when comps flagged this row's EBITDA as unavailable. The column is
    written by 19_valuation.py; a database it has not re-run against yet has no
    such column, which simply means no flag."""
    value = row.get("ebitda_is_fallback") if hasattr(row, "get") else None
    return bool(value) if value is not None and not pd.isna(value) else False


def render_comps(df: pd.DataFrame, n_companies: int = 11):
    if df.empty:
        st.info("Trading comps haven't been computed for this company yet. They need a stock "
                "ticker and a full set of fundamentals (revenue, operating profit, net debt) "
                "from its latest filing.")
        return
    r = df.iloc[0]
    st.caption(f"Fiscal year {int(r['year'])} fundamentals · ticker {r['ticker']} · "
               f"all figures converted to EUR")

    # 3 columns, not 4 - found by actually clicking through this tab: a
    # 4-column layout truncated longer EUR values ("€202.7bn" -> "€202....")
    # at this app's typical viewport width, while the DCF tab's existing
    # 3-column layout never did. Matches that tab's width instead of
    # re-discovering the same truncation independently.
    c1, c2, c3 = st.columns(3)
    c1.metric("Enterprise Value", format_eur(r["ev_eur"]))
    c2.metric("Market Cap", format_eur(r["market_cap_eur"]))
    c3.metric("Net Debt", format_eur(r["net_debt_eur"]))

    c4, c5, c6 = st.columns(3)
    c4.metric("EV / EBITDA", f"{r['ev_ebitda']:.1f}x" if pd.notna(r["ev_ebitda"]) else "n/a")
    c5.metric("EV / Sales", f"{r['ev_sales']:.1f}x" if pd.notna(r["ev_sales"]) else "n/a")
    c6.metric("P/E", f"{r['pe']:.1f}x" if pd.notna(r["pe"]) else "n/a")

    c7, c8 = st.columns(2)
    c7.metric("Revenue", format_eur(r["revenue_eur"]))
    if ebitda_is_fallback(r):
        # no D&A in the filing: EBITDA cannot be built, so show the honest multiple
        ev_ebit = r.get("ev_ebit")
        c8.metric("EV / EBIT", f"{ev_ebit:.1f}x" if pd.notna(ev_ebit) else "n/a")
        st.caption(EBITDA_FALLBACK_NOTE)
    else:
        c8.metric("EBITDA (reconstructed)", format_eur(r["ebitda_eur"]))

    comparison = peer_comparison_text(r)
    if comparison:
        st.markdown(comparison)
    else:
        st.caption("No peer comparison: fewer than 2 companies in this sector have a usable "
                   f"multiple yet (this universe has {n_companies} companies).")

    if pd.notna(r.get("fwd_ev_ebitda")):
        st.caption(f"Forward (NTM, CAGR-projected) EV/EBITDA: {r['fwd_ev_ebitda']:.1f}x · "
                   f"EV/Sales: {r['fwd_ev_sales']:.1f}x" if pd.notna(r.get("fwd_ev_sales")) else "")


def render_three_statement(df: pd.DataFrame):
    if df.empty:
        st.info("A projected 3-statement model hasn't been built for this company yet.")
        return
    base_year = int(df["base_year"].iloc[0])
    growth = df["growth_assumption"].iloc[0]
    rate = df["interest_rate_assumption"].iloc[0]
    st.caption(f"Projected from base year {base_year} · revenue growth "
               f"{growth:.1%} · interest rate {rate:.1%} · linked income statement, cash "
               f"flow and net debt, with a circularity-solved debt schedule.")

    display_cols = {
        "forecast_year": "Year", "revenue": "Revenue", "ebit": "EBIT",
        "interest_expense": "Interest Expense", "net_income": "Net Income",
        "dividends": "Dividends", "fcf": "FCF (levered)", "net_debt_end": "Net Debt (year-end)",
    }
    table = df[list(display_cols.keys())].rename(columns=display_cols).set_index("Year").T
    # DataFrame.applymap was removed in pandas 3.0 (deprecated since 2.1) -
    # .map() is its direct replacement for elementwise application.
    formatted = table.map(format_eur)
    st.dataframe(formatted, width="stretch")
    st.caption("FCF here is LEVERED (net of interest expense) - NOT the unlevered FCFF the "
               "DCF tab discounts, so the two must not be mixed.")
    capex_caption = capex_basis_caption(df)
    if capex_caption:
        st.caption(capex_caption)


def capex_basis_caption(df: pd.DataFrame):
    """A caption when capex comes from a company's own line via a reviewed override, or None. The wording here is
    generic and holds for every override (the loader refuses one without the printed label and evidence); anything
    specific to one company - such as how closely the line matched its gross purchases - is that override's own
    dashboard_note, carried in capex_basis_note. Older databases lack the columns, which just means no caption."""
    first = lambda col: df[col].iloc[0] if col in df.columns and not df.empty else None
    label, note, checked = first("capex_basis_label"), first("capex_basis_note"), first("capex_basis_checked")
    if pd.isna(label) or not label:
        return None
    if checked is False or checked == 0:
        text_ = (f"Capex is read from this company's own line \"{label}\": a company-defined figure rather than the "
                 f"standard capex line. It has not yet been checked for this base year against the company's gross "
                 f"purchases of fixed assets, so treat the capex-driven figures with care.")
    else:
        text_ = (f"Capex is read from this company's own line \"{label}\": a company-defined figure rather than the "
                 f"standard capex line, used here only after a reviewed check against the company's own report.")
    return f"{text_} {note}" if pd.notna(note) and note else text_


def render_dcf(df: pd.DataFrame):
    if df.empty:
        st.info("A DCF valuation hasn't been computed for this company yet. It needs the "
                "projected 3-statement model and a stock ticker for market data.")
        return
    r = df.iloc[0]
    st.caption(f"Base year {int(r['base_year'])} · WACC built up via CAPM, unlevered FCFF "
               f"discounted to Enterprise Value, Gordon-growth terminal value.")

    c1, c2, c3 = st.columns(3)
    c1.metric("WACC", format_pct_fraction(r["wacc"]))
    c2.metric("Cost of Equity", format_pct_fraction(r["cost_of_equity"]))
    c3.metric("After-tax Cost of Debt", format_pct_fraction(r["after_tax_cost_of_debt"]))

    c4, c5, c6 = st.columns(3)
    c4.metric("Enterprise Value", format_eur(r["enterprise_value"]))
    c5.metric("Equity Value", format_eur(r["equity_value"]))
    c6.metric("% of EV from Terminal Value", format_pct_fraction(r["pct_ev_from_terminal"]))

    st.caption("A DCF's precision is illusory when terminal value drives most of EV - see "
               "the % above before treating Equity Value as a precise number rather than a "
               "range. Cross-check against the Comps tab's EV for the same company.")


def render_market_risk(df: pd.DataFrame):
    if df.empty:
        st.info("Market-risk metrics haven't been computed for this company yet.")
        return
    r = df.iloc[0]
    st.caption(f"{r['period_start']} to {r['period_end']} ({int(r['n_observations'])} aligned "
               f"trading days) vs {r['benchmark']} (STOXX Europe 600) - beta and correlation are "
               f"computed directly from daily price history, not read from a third-party number.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Annualized Volatility", format_pct_fraction(r["annualized_volatility"]))
    c2.metric("Sharpe Ratio", f"{r['sharpe_ratio']:.2f}" if pd.notna(r["sharpe_ratio"]) else "n/a")
    c3.metric("Max Drawdown", format_pct_fraction(r["max_drawdown"]))

    c4, c5, c6 = st.columns(3)
    c4.metric("Beta", f"{r['beta']:.2f}" if pd.notna(r["beta"]) else "n/a")
    c5.metric("Correlation", f"{r['correlation']:.2f}" if pd.notna(r["correlation"]) else "n/a")
    c6.metric("60d Rolling Correlation (avg)",
              f"{r['rolling_corr_mean']:.2f}" if pd.notna(r["rolling_corr_mean"]) else "n/a")

    st.caption(f"Benchmark's own annualized volatility over the same window: "
               f"{format_pct_fraction(r['benchmark_volatility'])} - compare against this "
               f"company's volatility above to see if it's more or less volatile than the "
               f"broader European market, not just in absolute terms.")


def render_credit_profile(df: pd.DataFrame):
    if df.empty:
        st.info("A credit profile hasn't been computed for this company yet.")
        return

    st.caption("Net Debt / EBITDA trajectory - NOT the same as the Ratios tab's "
               "\"Net Debt vs Op. Profit\" (that one is Net Debt / EBIT; reusing it here would "
               "overstate leverage). Bands are a fixed, sector-agnostic heuristic, not a real "
               "agency rating.")

    latest = df.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Latest Net Debt/EBITDA",
              ebitda_multiple_text(latest["net_debt_ebitda"], latest["is_da_fallback"], "{:.2f}x"))
    c2.metric("Band", leverage_label(latest["band"], latest["is_da_fallback"]))
    c3.metric("Trend", leverage_label(latest["trend"], latest["is_da_fallback"]))

    display = df.copy()
    flags = df["is_da_fallback"]
    display["net_debt_ebitda"] = [ebitda_multiple_text(v, f, "{:.2f}x") for v, f in zip(df["net_debt_ebitda"], flags)]
    display["band"] = [leverage_label(v, f) for v, f in zip(df["band"], flags)]
    display["trend"] = [leverage_label(v, f) for v, f in zip(df["trend"], flags)]
    display["net_debt"] = display["net_debt"].apply(format_eur)
    display["ebitda"] = display["ebitda"].apply(format_eur)
    display["is_da_fallback"] = flags.map(lambda f: "⚠ D&A not found: EBITDA is EBIT" if _is_true(f) else "")
    display = display[["year", "net_debt", "ebitda", "net_debt_ebitda", "band", "trend", "is_da_fallback"]].rename(
        columns={"year": "Year", "net_debt": "Net Debt", "ebitda": "EBITDA",
                 "net_debt_ebitda": "Net Debt/EBITDA", "band": "Band", "trend": "Trend",
                 "is_da_fallback": ""})
    st.dataframe(display, width="stretch", hide_index=True)

    if df["is_da_fallback"].any():
        st.caption(LEVERAGE_FOOTNOTE)


def render_backtest(df: pd.DataFrame):
    if df.empty:
        st.info("A forecast backtest hasn't been computed for this company yet.")
        return
    display = df.copy()
    display["is_winner"] = display["is_winner"].map({True: "★ winner", False: ""})
    for col in ("mae", "rmse", "bias", "mape"):
        display[col] = display[col].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "n/a")
    # confidence is SQL NULL (17_backtest.py's save_to_db stores "" as NULL)
    # whenever only one method had any valid fold - nothing to compare, not
    # a missing-data bug. pandas reads that back as None, which st.dataframe
    # would otherwise print as the literal string "None" - found by
    # actually clicking through this tab (L'Oreal's ccc/linreg row).
    display["confidence"] = display["confidence"].fillna("n/a")
    display = display.rename(columns={
        "ratio_name": "Ratio", "method": "Method", "n_folds": "Folds", "mae": "MAE",
        "rmse": "RMSE", "bias": "Bias", "mape": "MAPE", "is_winner": "", "confidence": "Confidence",
    })
    st.dataframe(display, width="stretch", hide_index=True)
    st.caption("CAGR vs. linear regression, scored by rolling-origin backtest (not just "
               "last-year fit) - a winner picked from 1 fold is labelled low confidence, "
               "not presented the same as a 4-fold pick.")


def render_precedents(df: pd.DataFrame):
    st.caption("Curated, publicly-sourced M&A deals in this project's sectors - deliberately "
               "a small, well-verified list rather than padded with uncertain figures. Not "
               "specific to the company selected above.")
    if df.empty:
        st.info("No precedent transactions loaded yet.")
        return
    display = df.copy()
    display["ev_eur_m"] = display["ev_eur_m"].apply(lambda v: f"€{v:,.0f}m" if pd.notna(v) else "n/a")
    display["ev_ebitda"] = display.apply(
        lambda row: (f"{row['ev_ebitda']:.1f}x" + (" (est.)" if row["ev_ebitda_is_estimate"] else ""))
        if pd.notna(row["ev_ebitda"]) else "n/a", axis=1)
    display["ev_sales"] = display["ev_sales"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "n/a")
    display["current_trading_comps_ev_sales_median"] = display["current_trading_comps_ev_sales_median"].apply(
        lambda v: f"{v:.1f}x" if pd.notna(v) else "n/a")
    display["implied_control_premium_pct"] = display["implied_control_premium_pct"].apply(
        lambda v: f"{v:+.0f}%" if pd.notna(v) else "n/a")
    display = display.drop(columns=["ev_ebitda_is_estimate"]).rename(columns={
        "deal": "Deal", "sector": "Sector", "acquirer": "Acquirer", "target": "Target",
        "announced": "Announced", "ev_eur_m": "EV", "ev_sales": "EV/Sales",
        "ev_ebitda": "EV/EBITDA", "current_trading_comps_ev_sales_median": "Current peers' EV/Sales",
        "implied_control_premium_pct": "Implied Control Premium", "source": "Source",
    })
    st.dataframe(display, width="stretch", hide_index=True)


# ---------------------------------------------------------------- app

engine = get_engine()
ensure_screener_view(engine)
companies = load_companies(engine)
screener = load_screener(engine)
n_companies_total = len(companies)

st.title("📊 IFRS Pipeline")
st.caption(f"Automated fundamentals for European listed companies · last data refresh: {last_updated(engine)}")

with st.sidebar:
    st.header("Filter")
    countries = sorted(c for c in companies["country"].dropna().unique())
    sectors_std = sorted(s for s in screener["sector_std"].dropna().unique())
    # Default to NOTHING pre-selected (PLAN.md WP5) - at 300 companies,
    # pre-checking every value produces 30+ sidebar chips on load, which
    # is noise, not a useful default. An empty selection is treated as
    # "no filter" below (still shows everything), rather than "filter to
    # nothing" - a blank landing page on first load would be a worse
    # default than today's, not a fix.
    selected_countries = st.multiselect("Country", countries, default=[])
    selected_sectors_std = st.multiselect("Sector", sectors_std, default=[])


# NOTE: each mask must stay a boolean Series even when its filter is
# empty - a real bug caught running this live, not assumed: when BOTH
# filters are empty (the very first page load, before WP5's new empty-
# default is touched at all), `True & True` collapses to a plain Python
# bool rather than a Series, and `screener[True]` raises KeyError(True)
# (pandas tries to look up a column literally named True). pd.Series(True,
# index=...) keeps it a real elementwise mask in every case.
country_mask = (screener["country"].isin(selected_countries) if selected_countries
                 else pd.Series(True, index=screener.index))
sector_mask = (screener["sector_std"].isin(selected_sectors_std) if selected_sectors_std
               else pd.Series(True, index=screener.index))
screener_filtered = screener[country_mask & sector_mask].reset_index(drop=True)

st.subheader(f"{len(screener_filtered)} of {n_companies_total} companies")

if screener_filtered.empty:
    st.warning("No companies match the current filter.")
    st.stop()

display_screener = pd.DataFrame({
    "Company": screener_filtered["name"],
    "Country": screener_filtered["country"],
    "Sector": screener_filtered["sector_std"].fillna("Unclassified"),
    "Op. Margin": screener_filtered["operating_margin"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "n/a"),
    "ROIC": screener_filtered["roic"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "n/a"),
    "EV/EBITDA": [ebitda_multiple_text(v, f) for v, f in
                  zip(screener_filtered["ev_ebitda"], screener_filtered["credit_is_da_fallback"])],
    "Net Debt/EBITDA": [ebitda_multiple_text(v, f) for v, f in
                        zip(screener_filtered["net_debt_ebitda"], screener_filtered["credit_is_da_fallback"])],
    "Credit Band": [leverage_label(v, f) for v, f in
                    zip(screener_filtered["credit_band"], screener_filtered["credit_is_da_fallback"])],
    "High Flags": screener_filtered["high_flag_count"],
})

# on_select/selection_mode: click a row -> that company's detail loads
# below (PLAN.md WP5's own "click a row" spec), native to Streamlit
# 1.35+ (this app runs 1.63) - no custom JS needed.
selection_event = st.dataframe(
    display_screener, width="stretch", hide_index=True,
    on_select="rerun", selection_mode="single-row",
)
if any(_is_true(f) for f in screener_filtered["credit_is_da_fallback"]):
    st.caption(LEVERAGE_FOOTNOTE)

selected_positions = selection_event.selection.rows if selection_event and selection_event.selection else []
# Default to the first row so the detail view below always shows
# something useful rather than an empty "click a row" placeholder on
# first load - same "show something by default" reasoning as the empty
# filter-selection handling above.
selected_position = selected_positions[0] if selected_positions else 0
selected_company_id = int(screener_filtered.iloc[selected_position]["company_id"])
selected_row = companies[companies["company_id"] == selected_company_id].iloc[0]

st.markdown(f"## {selected_row['name']}")
st.caption(f"{selected_row['country']} · {selected_row['sector']}")

(tab_ratios, tab_forensics, tab_comps, tab_3stmt, tab_dcf,
 tab_market_risk, tab_credit, tab_backtest, tab_precedents) = st.tabs([
    "Ratios", "Forensics flags", "Trading Comps", "3-Statement Model",
    "DCF Valuation", "Market Risk", "Credit Profile", "Forecast Backtest", "Precedent Transactions",
])

with tab_ratios:
    ratios = load_ratios(engine, int(selected_row["company_id"]))
    render_ratio_table(ratios)

with tab_forensics:
    render_forensics(load_forensics(engine, int(selected_row["company_id"])))

with tab_comps:
    render_comps(load_comps(engine, int(selected_row["company_id"])), n_companies=n_companies_total)

with tab_3stmt:
    render_three_statement(load_three_statement(engine, int(selected_row["company_id"])))

with tab_dcf:
    render_dcf(load_dcf(engine, int(selected_row["company_id"])))

with tab_market_risk:
    render_market_risk(load_market_risk(engine, int(selected_row["company_id"])))

with tab_credit:
    render_credit_profile(load_credit_profile(engine, int(selected_row["company_id"])))

with tab_backtest:
    render_backtest(load_backtest(engine, int(selected_row["company_id"])))

with tab_precedents:
    render_precedents(load_precedents(engine))
