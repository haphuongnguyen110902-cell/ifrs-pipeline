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

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

st.set_page_config(page_title="IFRS Pipeline", page_icon="📊", layout="wide")


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
    return create_engine(db_url)


@st.cache_data(ttl=3600)
def load_companies(_engine):
    return pd.read_sql(text(
        "SELECT company_id, name, country, sector FROM company ORDER BY name"
    ), _engine)


@st.cache_data(ttl=3600)
def load_ratios(_engine, company_id: int):
    return pd.read_sql(text(
        "SELECT year, ratio_name, display_label, value FROM ratio "
        "WHERE company_id = :cid ORDER BY year"
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
    return pd.read_sql(text(
        "SELECT * FROM three_statement_projection WHERE company_id = :cid "
        "ORDER BY base_year DESC, forecast_year ASC"
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


def render_comps(df: pd.DataFrame):
    if df.empty:
        st.info("No trading comps computed yet for this company (see 19_valuation.py) - "
                "usually because a required field (gross margin, DSO/DIO/DPO...) isn't "
                "tagged for this company's latest filing. See CLAUDE.md's data-completeness "
                "notes rather than assuming this is a bug.")
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
    c8.metric("EBITDA (reconstructed)", format_eur(r["ebitda_eur"]))

    n_peers = int(r["n_peers_in_sector"]) if pd.notna(r["n_peers_in_sector"]) else 0
    if n_peers >= 2 and pd.notna(r["implied_ev_from_peers"]):
        st.markdown(
            f"**Sector peer comparison** ({n_peers} peers in {r['sector']}): "
            f"peer median EV/EBITDA {r['ev_ebitda_sector_median']:.1f}x implies an EV of "
            f"{format_eur(r['implied_ev_from_peers'])} — this company trades at a "
            f"{r['premium_vs_peers_pct']:+.0f}% {'premium' if r['premium_vs_peers_pct'] >= 0 else 'discount'} "
            f"to that."
        )
    else:
        st.caption(f"Fewer than 2 sector peers in this 11-company universe ({n_peers} found) - "
                   "no meaningful implied valuation from peers (see 19_valuation.py's "
                   "'median of one' guard).")

    if pd.notna(r.get("fwd_ev_ebitda")):
        st.caption(f"Forward (NTM, CAGR-projected) EV/EBITDA: {r['fwd_ev_ebitda']:.1f}x · "
                   f"EV/Sales: {r['fwd_ev_sales']:.1f}x" if pd.notna(r.get("fwd_ev_sales")) else "")


def render_three_statement(df: pd.DataFrame):
    if df.empty:
        st.info("No 3-statement projection computed yet for this company "
                "(see 21_three_statement_model.py).")
        return
    base_year = int(df["base_year"].iloc[0])
    growth = df["growth_assumption"].iloc[0]
    rate = df["interest_rate_assumption"].iloc[0]
    st.caption(f"Projected from base year {base_year} · revenue growth "
               f"{growth:.1%} · interest rate {rate:.1%} — see 21_three_statement_model.py "
               f"for the full linked-model assumptions (circularity-solved debt schedule).")

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
               "DCF tab discounts. See 22_dcf.py's module docstring for why the two must not "
               "be mixed.")


def render_dcf(df: pd.DataFrame):
    if df.empty:
        st.info("No DCF computed yet for this company (see 22_dcf.py) - either a required "
                "3-statement input is missing, or no ticker is mapped for market data.")
        return
    r = df.iloc[0]
    st.caption(f"Base year {int(r['base_year'])} · WACC built up via CAPM, unlevered FCFF "
               f"discounted to Enterprise Value, Gordon-growth terminal value - see "
               f"22_dcf.py's module docstring for the full method and sourced assumptions.")

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
        st.info("No market risk metrics computed yet for this company (see 23_market_risk.py).")
        return
    r = df.iloc[0]
    st.caption(f"{r['period_start']} to {r['period_end']} ({int(r['n_observations'])} aligned "
               f"trading days) vs {r['benchmark']} (STOXX Europe 600) - beta and correlation are "
               f"computed directly from daily price history, not read from a third-party number. "
               f"See 23_market_risk.py's module docstring for the full method.")

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
        st.info("No credit profile computed yet for this company (see 24_credit.py).")
        return

    st.caption("Net Debt / EBITDA trajectory - NOT the same as the Ratios tab's "
               "\"Net Debt vs Op. Profit\" (that's Net Debt / EBIT, despite its column name "
               "elsewhere; see 24_credit.py's module docstring for why reusing it would have "
               "overstated leverage here). Bands are a fixed, sector-agnostic heuristic, not a "
               "real agency rating - see the script's docstring.")

    latest = df.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Latest Net Debt/EBITDA",
              f"{latest['net_debt_ebitda']:.2f}x" if pd.notna(latest["net_debt_ebitda"]) else "n/a")
    c2.metric("Band", latest["band"])
    c3.metric("Trend", latest["trend"])

    display = df.copy()
    display["net_debt_ebitda"] = display["net_debt_ebitda"].apply(
        lambda v: f"{v:.2f}x" if pd.notna(v) else "n/a")
    display["net_debt"] = display["net_debt"].apply(format_eur)
    display["ebitda"] = display["ebitda"].apply(format_eur)
    display["is_da_fallback"] = display["is_da_fallback"].map(
        {True: "⚠ EBITDA=EBIT (overstated)", False: ""})
    display = display[["year", "net_debt", "ebitda", "net_debt_ebitda", "band", "trend", "is_da_fallback"]].rename(
        columns={"year": "Year", "net_debt": "Net Debt", "ebitda": "EBITDA",
                 "net_debt_ebitda": "Net Debt/EBITDA", "band": "Band", "trend": "Trend",
                 "is_da_fallback": ""})
    st.dataframe(display, width="stretch", hide_index=True)

    if df["is_da_fallback"].any():
        st.caption("⚠ Years marked above have no D&A tag matched for this company - EBITDA "
                   "silently equals EBIT for those years, so leverage is likely overstated. "
                   "See 11_ratio_engine.py's D&A fallback notes.")


def render_backtest(df: pd.DataFrame):
    if df.empty:
        st.info("No forecast backtest computed yet for this company (see 17_backtest.py).")
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
               "not presented the same as a 4-fold pick. See 17_backtest.py.")


def render_precedents(df: pd.DataFrame):
    st.caption("Curated, publicly-sourced M&A deals in this project's sectors - deliberately "
               "a small, well-verified list rather than padded with uncertain figures. Not "
               "specific to the company selected above (see 20_precedents.py).")
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
companies = load_companies(engine)

st.title("📊 IFRS Pipeline")
st.caption(f"Automated fundamentals for European listed companies · last data refresh: {last_updated(engine)}")

with st.sidebar:
    st.header("Filter")
    countries = sorted(c for c in companies["country"].dropna().unique())
    sectors = sorted(s for s in companies["sector"].dropna().unique())
    selected_countries = st.multiselect("Country", countries, default=countries)
    selected_sectors = st.multiselect("Sector", sectors, default=sectors)

filtered = companies[
    companies["country"].isin(selected_countries) & companies["sector"].isin(selected_sectors)
]

st.subheader(f"{len(filtered)} companies")
st.dataframe(filtered[["name", "country", "sector"]], width="stretch", hide_index=True)

if filtered.empty:
    st.warning("No companies match the current filter.")
    st.stop()

selected_name = st.selectbox("View company detail", filtered["name"].tolist())
selected_row = filtered[filtered["name"] == selected_name].iloc[0]

st.markdown(f"## {selected_name}")
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
    render_comps(load_comps(engine, int(selected_row["company_id"])))

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
