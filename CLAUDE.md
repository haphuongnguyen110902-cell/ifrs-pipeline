# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An IFRS/XBRL financial analysis pipeline: parses ESEF/XBRL filings for 11
European listed companies via Arelle, normalises raw tags to standardised
IFRS concepts, stores everything in PostgreSQL (Neon), and computes ratios,
earnings-quality forensics, forecasts, trading comps, a linked 3-statement
model, and a DCF valuation on top. A public Streamlit dashboard
(`webapp/app.py`) reads live from the database. See `README.md` for the full
feature list and `ROADMAP.md` for current phase status — **ROADMAP.md is the
single source of truth for project direction**; update it when finishing a
phase or fixing a notable bug, following its existing per-phase style (what
was built, what real bug was found running it on live data, what's
deliberately deferred and why).

## Commands

Windows/PowerShell is primary; the venv is at `.venv`.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt        # root: includes arelle (heavy) for the pipeline scripts
```

Requires a `.env` with `DATABASE_URL=postgresql://...` (Neon). Most scripts
`sys.exit(1)` with a clear message if it's missing, rather than failing
deep in SQLAlchemy.

```bash
# full test suite - no DATABASE_URL needed, every test uses synthetic data
python -m pytest tests/ -v

# a single test file / test
python -m pytest tests/test_dcf.py -v
python -m pytest tests/test_dcf.py::TestWacc::test_matches_hand_calculation -v

# run one pipeline script directly (most accept --company and --no-db)
python scripts/22_dcf.py --company "L'Oreal"
python scripts/22_dcf.py --company "L'Oreal" --no-db     # skip writing to the live DB

# orchestrate multiple stages
python run_pipeline.py --mode full       # parse+load+everything, first run (needs data/raw/*.zip locally)
python run_pipeline.py --mode analyze    # forensics+forecast+backtest only, DB-only, what CI runs weekly
# other modes: discover, load, historical, validate, ratios, report

# dashboard, from repo root
streamlit run webapp/app.py
```

`data/raw/*` is gitignored (raw XBRL zips are large binaries) — `full`/
`load`/`historical` only work on a machine that already has the source
`.zip` files; CI can only ever run `analyze`/`ratios`/`validate`.

## Architecture

**Numbered scripts = pipeline stages, not execution order within a run.**
`scripts/NN_name.py` filenames are chronological/dependency-ordered (parse →
map → load → ratios → forensics → forecast → valuation → 3-statement model →
DCF), not a strict 1-2-3 run sequence — `run_pipeline.py` wires together
which ones a given `--mode` actually calls.

**Scripts import each other via `importlib`, not `import`.** Filenames like
`11_ratio_engine.py` can't start with a digit, so any script or test that
reuses another script's functions loads it with
`importlib.util.spec_from_file_location` + `exec_module` (see the top of
`21_three_statement_model.py` or `22_dcf.py` for the pattern, and
`tests/conftest.py`'s `load_script` fixture for the test-side equivalent).
When adding a new script that reuses logic from an existing one, follow this
same loading pattern rather than trying to make it a normal importable
package.

**PostgreSQL is the single source of truth; nothing is a cached snapshot.**
`fact_value` holds raw normalised facts with full provenance back to the
original XBRL tag/filing. Ratios, forensics, forecasts, valuation and the
3-statement/DCF outputs are all *computed from it at query time* (ratios are
also persisted to their own tables for reuse, but re-derivable from facts).
Fix a mapping, reload, and everything downstream updates automatically — no
stage caches a stale copy.

**Each analysis module owns its own schema file.** `sql/schema_*.sql` is one
file per module (`schema_dcf.sql`, `schema_three_statement.sql`,
`schema_forecast.sql`, etc.), applied by that module's own
`ensure_*_table(engine)` function (`CREATE TABLE IF NOT EXISTS ...`) rather
than a central migration tool — there's no Alembic/migrations directory.

**XBRL concept-name variance is handled by layered fallback, not
exceptions.** `11_ratio_engine.py`'s `get_best(wide, *names)` tries several
known tag-name variants for the same economic concept in priority order
(different filers tag the same line item differently — e.g. D&A has at
least 3 different combined-line tag names across the 11 companies, plus a
granular 3-concept breakdown some filers use instead). When a concept truly
isn't tagged for a company, the convention is an explicit `<field>_is_fallback`
flag alongside a generic fallback value (see `capex_is_fallback`,
`payout_ratio_is_fallback`, `da_total_is_fallback` in
`21_three_statement_model.py`'s `fetch_base_year()`) and a printed `***`
warning at the call site — never silently treating a missing value as a
real zero. Extending a fallback to cover a new filer's tag variant requires
checking the resulting value looks sane first (e.g. D&A as % of revenue in a
plausible range) — guessing wrong here silently corrupts EBITDA/FCFF, and
ambiguous cases (a tag that bundles the concept with something else, or no
tag at all) should stay flagged as a fallback rather than be guessed at.

**Two separate `requirements.txt` on purpose.** The root one (used by
pipeline scripts and CI's `tests.yml`/`pipeline.yml`) includes `arelle-release`,
a heavy XBRL-parsing dependency `webapp/app.py` never needs. `webapp/requirements.txt`
is the light subset for the Streamlit Cloud deploy — Streamlit Community
Cloud auto-detects a requirements file by searching the entrypoint's own
directory first, so keeping `webapp/app.py` as the entrypoint and this file
alongside it avoids a slow/failed Cloud build. Don't merge these back into one.

**Tests are synthetic and DB-free.** Everything under `tests/` builds an
in-memory DataFrame (see `make_wide_row()`-style helpers per test file) and
asserts against a hand-calculated expected value — no test touches the live
database, which is why `tests.yml` runs on every push with no `DATABASE_URL`
secret. Each test file's docstring/tests document the real bug or real-data
case being locked in; when fixing a bug found by running a script against
live data, add the regression test at this synthetic-data layer rather than
asserting against a live DB call.

**Two independent CI workflows**, deliberately not merged:
`.github/workflows/tests.yml` (pytest on every push, no DB) and
`.github/workflows/pipeline.yml` (scheduled `--mode analyze` against the live
Neon DB every Monday, needs the `DATABASE_URL` GitHub secret — a *different*
secret value than the Streamlit Cloud one, and unlike Streamlit's TOML
format, the GitHub Actions secret must have no surrounding quotes).

## Product and financial principles

This project is not an AI chatbot that generates financial numbers. It is a
deterministic financial-data and analysis platform.

The system must distinguish clearly between:

1. Source facts
2. Normalised financial concepts
3. Derived financial metrics
4. Analytical interpretations
5. AI-generated explanations

AI must never be treated as the source of financial facts.

### Source-of-truth hierarchy

```
Official filings and structured regulatory/company data
    ↓
Raw extracted facts
    ↓
Normalised canonical financial concepts
    ↓
Deterministic calculations
    ↓
Financial analysis
    ↓
Optional AI-generated narrative
```

The lower layers must never depend on an LLM.

### Financial correctness over convenience

When source data is ambiguous, missing, or inconsistent:

- do not invent a value;
- do not silently substitute zero;
- preserve provenance;
- expose fallback status;
- prefer an explicit "not available" state;
- document the reason for the fallback.

A plausible-looking number is worse than a missing number. This is the
governing principle behind the `_is_fallback` flag convention described
under Architecture above — that convention is this principle's concrete
implementation in code, not a separate rule.

### Company-specific heterogeneity

European issuers may use different:

- XBRL taxonomies;
- extension concepts;
- labels;
- units;
- dimensional structures;
- reporting periods;
- presentation structures;
- annual-report formats.

Do not solve this by adding company-specific conditions to core analytics.
Use connectors, adapters, mappings, and fallback layers.

### Investment banking orientation

The platform should support workflows relevant to investment banking, including:

- historical financial analysis;
- revenue and EBITDA analysis;
- margin analysis;
- working-capital analysis;
- cash-flow analysis;
- leverage analysis;
- trading comparables;
- transaction-style financial analysis;
- three-statement modelling;
- DCF valuation;
- sensitivity analysis.

Calculations must be transparent and traceable to source facts.

### Asset management orientation

The platform should also support:

- profitability trends;
- earnings quality;
- ROIC / ROE;
- capital allocation;
- balance-sheet quality;
- free cash flow;
- growth quality;
- valuation;
- peer comparison;
- historical trend analysis.

### Financial controlling orientation

The platform should also support:

- budget vs actual analysis where data is available;
- margin evolution;
- cost structure;
- working capital;
- operational KPIs;
- forecasting;
- variance analysis;
- management-oriented reporting.

### AI usage

LLMs may assist with:

- semantic mapping proposals;
- documentation;
- code development;
- anomaly investigation;
- financial commentary;
- natural-language explanations.

LLMs must not:

- invent financial facts;
- silently change source values;
- replace deterministic financial calculations;
- override validation rules;
- make an untraceable accounting judgement in production.

Whenever AI proposes a mapping or interpretation, the underlying source fact
and reasoning must remain inspectable.
