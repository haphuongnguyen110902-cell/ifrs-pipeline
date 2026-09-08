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
pointing at this file. Set DATABASE_URL in the app's Secrets (same value
as your local .env) - never commit it to the repo.

Run locally:
    streamlit run app.py
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
    pivot = ratios.pivot_table(index="display_label", columns="year", values="value", aggfunc="first")
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


def render_forensics(engine, company_name: str):
    """Flags are recomputed live from the ratio table rather than read
    from a stored table - 15_forensics.py doesn't persist to the DB (see
    its own docstring), and recomputing is cheap at this dataset size."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "forensics_15", Path(__file__).parent / "scripts" / "15_forensics.py")
    forensics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(forensics)

    ratios = forensics.fetch_ratios(engine, company_filter=company_name)
    if ratios.empty:
        st.info("No forensics flags - not enough ratio history yet.")
        return
    wide = forensics.pivot_ratios(ratios)
    flags = forensics.compute_flags(wide)
    if flags.empty:
        st.success("No flags - nothing unusual detected in this company's ratio history.")
        return

    severity_order = {"high": 0, "medium": 1, "low": 2}
    flags = flags.sort_values(
        by=["year"], key=lambda s: s, ascending=False
    ).assign(_sev=flags["severity"].map(severity_order)).sort_values(["_sev", "year"], ascending=[True, False])

    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for _, f in flags.iterrows():
        with st.expander(f"{icon.get(f['severity'], '⚪')} {f['year']} — {f['label']} ({f['severity']})"):
            st.write(f["detail"])
            if f.get("what_to_check"):
                st.caption(f"What to check: {f['what_to_check']}")


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

tab_ratios, tab_forensics = st.tabs(["Ratios", "Forensics flags"])

with tab_ratios:
    ratios = load_ratios(engine, int(selected_row["company_id"]))
    render_ratio_table(ratios)

with tab_forensics:
    render_forensics(engine, selected_name)
