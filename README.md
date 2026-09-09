# IFRS/XBRL Financial Analysis Pipeline

[![Tests](https://github.com/haphuongnguyen110902-cell/ifrs-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/haphuongnguyen110902-cell/ifrs-pipeline/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

**Live demo:** [ifrs-pipeline dashboard](https://haphuongnguyen110902-cell-ifrs-pipeline-webappapp-iwnn9s.streamlit.app/) — filter by country/sector, browse ratios, earnings-quality flags, trading comps, a linked 3-statement model, and a DCF valuation for 11 European listed companies.

An end-to-end pipeline that turns raw ESEF/XBRL regulatory filings into the kind of output a financial analyst actually produces: normalised fundamentals with full provenance, ratio and earnings-quality analysis, forecasting validated by rolling-origin backtest, trading comps and precedent transactions, a linked 3-statement projection with a circularity-solved debt schedule, and a DCF valuation — cross-checked against each other, not presented as isolated calculators. Every non-trivial bug found while building it (data-loss incidents, currency-mixing errors, silent zero-as-real-data cases) is documented where it was fixed, with the regression test that now locks it in — see [`ROADMAP.md`](ROADMAP.md) for that build log in full.

## What it does

- **Parses** ESEF/XBRL packages directly from ESMA-regulated filings using Arelle's Python API
- **Normalises** raw XBRL tags to standardised IFRS concepts via a 714-tag semantic mapping layer
- **Stores** facts in PostgreSQL (Neon) with full provenance — every number traceable back to its original XBRL tag and filing
- **Validates** data against accounting identities and published annual reports
- **Loads historical depth** — up to 9 years per company (2017–2025), one filing row per company/year, not just the latest annual report
- **Computes** financial ratios: gross/operating/net margin, ROIC, ROE, cash conversion, DSO/DIO/DPO/CCC (working capital), effective tax rate, leverage
- **Flags earnings-quality issues** — rule-based forensics (leverage spikes, cash-conversion deterioration, thin-denominator distortion) with automatic severity downgrades when a flag's own baseline is itself distorted
- **Forecasts** each ratio (CAGR + linear regression) and **backtests** both methods with rolling-origin cross-validation to pick a winner per company/ratio, with a confidence label based on how many folds actually support the pick
- **Converts currencies** per IAS 21 (ECB average rate for P&L, closing rate for balance sheet), auto-detecting which currencies are actually in use
- **Builds trading comps** (EV/EBITDA, EV/Sales, P/E) in EUR with sector-peer benchmarking and implied valuation, plus a small set of hand-verified precedent transactions
- **Projects a linked 3-statement model** — revenue growth → EBIT → interest expense ↔ debt balance (the "circularity" every IB technical test asks about, solved with a fixed-point loop and verified against an independently-derived closed-form solution) → net income → FCF → dividends → debt paydown
- **Values companies by DCF** — WACC built up via CAPM (live beta, sourced risk-free rate/ERP), unlevered FCFF discounted with a Gordon-growth terminal value, WACC × terminal-growth sensitivity table, cross-checked against the trading comps above
- **Measures market risk** — annualized volatility, Sharpe ratio, max drawdown, and beta/correlation vs. STOXX Europe 600 computed directly from daily price history (not read from a third-party number)
- **Classifies credit profile** — Net Debt/EBITDA trajectory bucketed into sector-agnostic leverage bands with a YoY trend label, computed from true EBITDA (not the differently-named `net_debt_ebitda_proxy` ratio, which is actually Net Debt/EBIT — see `24_credit.py`)
- **Runs scenario/sensitivity analysis** — perturb revenue, margin, growth, cost of debt, or capex (any combination) and see the resulting shift in FCFF, Enterprise Value and leverage band, with WACC held constant so the delta is attributable to the operating shock alone
- **Auto-classifies** new companies using the IFRS taxonomy's own presentation linkbase — standard tags require zero manual work
- **Orchestrates** the full pipeline end-to-end with `run_pipeline.py --mode full`, or just the analysis layer with `--mode analyze`
- **Serves** a public Streamlit dashboard (`webapp/app.py`) with 9 tabs per company — ratios, forensics flags, trading comps, the 3-statement model, DCF valuation, market risk, credit profile, forecast backtest results, and precedent transactions
- **Refreshes automatically** every Monday via GitHub Actions (`--mode analyze`, no local files needed — see [Automation](#automation))
- **Tests itself** — 131 pytest regression tests covering every bug found and fixed during development, run on every push via CI

## Current status — V2 complete, valuation + market risk + credit layer live, automation running weekly

| | |
|---|---|
| Companies | 11 European listed companies (consumer, luxury, energy, hygiene) |
| Countries | France, Italy, Spain, Sweden, United Kingdom |
| Concepts mapped | 714 XBRL tags across income statement, balance sheet, cash flow |
| Facts in database | 13,997 across all companies and years |
| Years covered | 2017–2025 (varies by company; up to 9 years for some) |
| Ratios computed | 12: margins, ROIC, ROE, cash conversion, DSO/DIO/DPO/CCC, tax rate, leverage |
| Trading comps | 11/11 companies (EV/EBITDA, EV/Sales, P/E, sector peer benchmarking) |
| 3-statement model / DCF | 9/11 companies — the other 2 report costs "by nature" with no COGS/gross-profit split in their filings at all, so no fabricated number is shown for them (see [Known limitations](#known-limitations)) |
| Market risk (volatility, Sharpe, beta, drawdown) | 11/11 companies — price-history-only, doesn't inherit the fundamentals side's data gaps |
| Credit profile (Net Debt/true EBITDA trajectory) | computed for every company/year with both net debt and EBITDA available; years with no D&A tag matched are flagged, not hidden |
| Regression tests (data) | 5/5 pass — verified against L'Oréal's published 2024 annual report |
| Regression tests (code) | 131 pytest tests, run on every push via GitHub Actions CI |
| Forensics flags | 62 across 10 companies (26 high / 12 medium / 24 low severity) |
| Backtest coverage | 78/82 company-ratio pairs have enough rolling folds to pick a method |
| Unmapped facts | 0 — all non-dimensional facts fully mapped |
| Dashboard | Live and public (see link above), 9 tabs per company, refreshed weekly by CI |

**Companies:** L'Oréal, LVMH, Kering, EssilorLuxottica, Puig Brands, Danone, Pernod Ricard, Essity, Moncler, Shell, Amplifon

See [`ROADMAP.md`](ROADMAP.md) for the phase-by-phase engineering log behind this — what's done, in progress, and next, plus every real bug found along the way and how it was fixed.

## Sample output — comps table (2024)

| Company | Gross Margin | Op. Margin | ROIC | Cash Conversion | DSO |
|---|---|---|---|---|---|
| Moncler | 78.1% | 29.5% | 19.5% | 108.0% | 38d |
| L'Oréal | 74.2% | 19.0% | 17.6% | 100.4% | 47d |
| Puig Brands | 74.9% | 15.8% | 13.8% | 97.5% | 43d |
| LVMH | 67.0% | 22.3% | 16.4% | 100.1% | 20d |
| EssilorLuxottica | 63.4% | 13.0% | 5.4% | 141.4% | 45d |
| Danone | — | 12.3% | 8.2% | 113.4% | 39d |
| Essity | 32.4% | 12.6% | 10.4% | 91.8% | 59d |
| Shell (USD) | — | 10.5% | 8.2% | 182.8% | 59d |

*Currency-neutral ratios are comparable across EUR/SEK/USD without FX conversion.*

## Sample output — forensics flag (earnings-quality)

```
🟢 LOW
  EssilorLuxottica     2020  Ratio Distorted by Thin Operating Profit  [3.1]
         Operating margin only 3.1% this year, which makes cash_conversion=653.3% unreliable as a signal
```

Verified against EssilorLuxottica's actual 2020 published results (operating
profit €452M / revenue €14,429M = 3.13%, a COVID-year outlier). The same
module then automatically downgrades the following year's
`CASH_CONVERSION_DROP` flag from HIGH to LOW severity, since a -457.9pp
"drop" from 2020 to 2021 mostly reflects 2020's distorted baseline
unwinding, not real 2021 deterioration.

## Architecture

```
ESEF filing (.zip)                    ECB FX rates          yfinance (live)
      ↓                                     ↓                     ↓
Arelle parser  →  raw facts        18_fx_convert.py      19_valuation.py / 22_dcf.py
      ↓                                     ↓                     ↓
Semantic mapping (714 tags)  →  normalised IFRS concepts        (EUR)
      ↓
PostgreSQL / Neon  ←─── single source of truth
      ↓         ↓         ↓          ↓            ↓           ↓
Statements   Ratios   Validation  Forensics   Forecast    Backtest
 (Excel)  (DB+Excel)   (5/5)     (DB+Excel)  (DB+Excel)  (DB+Excel)
                    ↓ (`ratio` table - everything below reads from here,
                      not from fact_value directly)
        ┌───────────┼──────────────────┬──────────────────┬──────────────┬─────────────┐
        ↓           ↓                  ↓                  ↓              ↓             ↓
  Trading comps  3-statement    DCF valuation      Precedent      Market risk   Credit profile
  (19_valuation)  model (21_*)      (22_dcf)      transactions   (23_market_risk, (24_credit -
                                                       (20_*)      price history   true EBITDA,
                                                                   only - no       NOT the
                                                                   `ratio` table   differently-named
                                                                   dependency)     net_debt_ebitda_
                                                                                   proxy ratio)
        └───────────┴──────────────────┴──────────────────┴──────────────┴─────────────┘
                                  ↓
                    webapp/app.py (Streamlit) → public dashboard
                    (9 tabs per company - ratios, forensics, comps,
                     3-statement, DCF, market risk, credit profile,
                     backtest, precedents)
```

`run_pipeline.py --mode full` runs the left-to-right chain once end to
end; `--mode analyze` re-runs ratios → forensics → forecast → backtest
without re-parsing anything, for fast iteration on the analysis layer.
The valuation/market-risk/credit layer (comps/3-statement/DCF/precedents/
market risk/credit profile) is currently run by hand, not yet part of
either pipeline mode - see [Known limitations](#known-limitations).

Two independent CI workflows sit on top of this:
- **`.github/workflows/tests.yml`** — runs `pytest tests/` on every push, no database needed (every test uses synthetic data)
- **`.github/workflows/pipeline.yml`** — runs `run_pipeline.py --mode analyze` every Monday against the live Neon database (see [Automation](#automation) below)

## Automation

The pipeline refreshes itself weekly with no manual intervention:

1. Every Monday 06:00 UTC, GitHub Actions triggers `pipeline.yml`
2. It runs `run_pipeline.py --mode analyze` — ratios → forensics → forecast → backtest, reading straight from the already-populated Neon database
3. Results write back to the database; the dashboard (which queries live) reflects the refresh automatically

**A real gap found and fixed, not a hypothetical:** `--mode analyze` used
to skip straight to forensics/forecast/backtest, silently assuming the
`ratio` table was already fresh. That meant a CODE fix to the ratio
engine would never reach production via the automated weekly run, only
a manual `--mode ratios`/`--mode full` - exactly the situation a real
session hit. `step_ratios()` is now the first thing `--mode analyze`
does.

**Why `--mode analyze` specifically, not `--mode full`:** `data/raw/*` is
gitignored on purpose (raw XBRL filings are large binaries that don't
belong in git), so a fresh CI checkout has none of the source `.zip`
files on disk. `full`/`load`/`historical` modes need those local files
and would find nothing to parse in an automated run — they're for a
developer's own machine, triggered manually. `analyze` needs only
database connectivity, which is exactly what CI has.

Trigger a run manually anytime: repo → Actions tab → "IFRS Pipeline" → Run workflow.

## Key design decisions

**Database as single source of truth.** Nothing is stored as a snapshot. All outputs are computed from `fact_value` at query time. Fix a mapping → reload → everything updates automatically.

**Taxonomy-driven classification.** New companies use the IFRS taxonomy's own presentation linkbase (role numbers: `[2xxxxx]`=balance sheet, `[3xxxxx]`=P&L, `[5xxxxx]`=cash flow). Standard `ifrs-full:` tags classify themselves. Only company extensions need human review (~10 min per company).

**Human-in-the-loop extension review.** `12_prep_company.py` separates auto-classifiable tags from company-specific extensions. Extensions go to a REVIEW file; the human classifies only the genuinely ambiguous part. `13_batch_prep.py` pools extensions across multiple companies into one review pass.

**IAS 21 FX design — built, not just planned.** Facts stored in native currency (EUR, SEK, USD). Ratios are currency-neutral by construction, so most of the pipeline never needs FX conversion at all. Where it IS needed (trading comps), `18_fx_convert.py` follows IAS 21: closing rate for balance-sheet items, average rate for P&L items — both computed once from ECB data and stored immutably per (currency, year), never recomputed against "today's" rate. Market cap, which genuinely IS a "today" number, uses a separately-fetched live rate — two different rate sources for two conceptually different kinds of number, not an inconsistency.

**Denominator-aware forensics.** `15_forensics.py`'s `THIN_DENOMINATOR` flag catches ratios distorted by a near-zero Operating Profit (found via EssilorLuxottica's 2020 COVID year: 3.1% margin inflated `cash_conversion` to 653.3%). The same logic then downgrades any YoY-delta flag (`CASH_CONVERSION_DROP`, `HIGH_LEVERAGE`) whose own baseline sits on a thin-denominator year, so a distorted 2020 value doesn't also produce a false-alarm HIGH-severity flag in 2021.

**Backtest confidence, not just a winner.** `17_backtest.py` picks CAGR vs. linear regression by rolling-origin MAE, but a "winner" decided from a single fold is statistically meaningless — it's labelled `confidence: low` rather than presented the same as a 4-fold `high` pick.

**EBITDA is reconstructed, not tagged.** IFRS filers don't tag "EBITDA" directly (it's a non-IFRS measure) — `19_valuation.py` builds it as EBIT + D&A add-back from whichever depreciation/amortisation line items are actually tagged. This is the standard trading-comps approximation, not each company's own disclosed "adjusted EBITDA".

**Arelle taxonomy resolution.** ESEF filings reference schemas via namespace URLs that fail over the internet. The parser registers the filing's internal catalog via `PackageManager.addPackage()` + `rebuildRemappings()` before loading, so Arelle resolves everything locally from the zip.

**Duplicate prevention.** A UNIQUE constraint on `(filing_id, period_id, concept_id)` prevents duplicate facts. The loader uses `ON CONFLICT DO UPDATE SET value = MAX(ABS(...))` so when multiple XBRL contexts map to the same fact (a known ESEF pattern), the total wins over the component.

## Adding a new company

```bash
# 1. Find and download
python scripts/00_find_filing.py --search "CompanyName"
python scripts/00_find_filing.py --entity <id> --download --out data/raw/company.zip

# 2. Auto-classify (standard tags automatic, extensions flagged for review)
python scripts/12_prep_company.py --zip data/raw/company.zip

# 3. Review extension tags (~10 min), then apply
notepad data/mappings/REVIEW_extensions.yaml
python scripts/12_apply_review.py

# 4. Load and validate
python scripts/09_batch_load.py --only company
python scripts/08_validate.py --company "Company Name"
```

For multiple companies at once:
```bash
python scripts/13_batch_prep.py --only company1.zip company2.zip company3.zip
python scripts/12_apply_review.py
python scripts/09_batch_load.py --reset-facts
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create `.env`:
```
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

```bash
python scripts/04_create_schema.py
python scripts/09_batch_load.py
python scripts/load_historical.py       # multi-year depth (V2)
python scripts/08_validate.py
python scripts/11_ratio_engine.py
python scripts/15_forensics.py
python scripts/16_forecasting.py
python scripts/17_backtest.py
python scripts/18_fx_convert.py
python scripts/19_valuation.py
python scripts/20_precedents.py
python scripts/21_three_statement_model.py --company "COMPANY NAME"
python scripts/22_dcf.py --company "COMPANY NAME"
python scripts/23_market_risk.py --company "COMPANY NAME"
python scripts/24_credit.py --company "COMPANY NAME"
python scripts/25_scenario.py --company "COMPANY NAME" --margin-shock -2

# or all at once:
python run_pipeline.py --mode full      # everything, first run
python run_pipeline.py --mode analyze   # ratios+forensics+forecast+backtest, fast re-run
                                         # (valuation layer above still run separately, per company)

# run the test suite
python -m pytest tests/ -v

# run the dashboard locally (from repo root)
streamlit run webapp/app.py
```

## Scripts reference

| Script | Purpose |
|---|---|
| `00_find_filing.py` | Find and download ESEF filings from filings.xbrl.org |
| `04_create_schema.py` | Create PostgreSQL schema |
| `08_validate.py` | Accounting identity checks + regression tests |
| `09_batch_load.py` | Load all companies (latest filing) into the database |
| `load_historical.py` | Load historical multi-year filings (V2), separate filing row per company/year |
| `11_ratio_engine.py` | Compute ratios (incl. DSO/DIO/DPO/CCC), write to DB + Excel |
| `12_prep_company.py` | Prep a new company: auto-classify + flag extensions |
| `12_apply_review.py` | Apply human-reviewed extension classifications |
| `13_batch_prep.py` | Batch prep multiple companies at once |
| `15_forensics.py` | Earnings-quality / leverage flags |
| `16_forecasting.py` | CAGR + linear regression forecasts per ratio |
| `17_backtest.py` | Rolling-origin backtest of both forecasting methods |
| `18_fx_convert.py` | ECB average/closing FX rates per IAS 21 |
| `19_valuation.py` | Trading comps (EV/EBITDA, EV/Sales, P/E) in EUR, sector peer benchmarking |
| `20_precedents.py` | Curated precedent M&A transactions vs. current trading comps |
| `21_three_statement_model.py` | Linked 3-statement projection with circularity-solved debt schedule |
| `22_dcf.py` | DCF valuation (CAPM WACC, unlevered FCFF, Gordon-growth terminal value) |
| `23_market_risk.py` | Volatility, Sharpe ratio, max drawdown, beta/correlation vs. STOXX Europe 600 |
| `24_credit.py` | Net Debt/true-EBITDA trajectory → leverage band + trend classification |
| `25_scenario.py` | Perturb revenue/margin/growth/cost of debt/capex, recompute FCFF/EV/leverage vs. base case |
| `run_pipeline.py` | Orchestrates the above (`--mode full` / `--mode analyze` / others) |
| `webapp/app.py` | Public Streamlit dashboard — 7 tabs per company (ratios, forensics, comps, 3-statement, DCF, backtest, precedents) |
| `tests/` | pytest regression suite (87 tests) — see each file's docstring for the real bug it locks in |

## Known limitations

- Dimensional facts not loaded — segment/geographic breakdowns excluded
- Sign conventions partially handled
- Canonical concept layer missing — adjusted operating profit definitions not yet unified across companies
- Forensics thresholds (thin-margin, extreme cash-conversion, leverage) are fixed, sector-agnostic numbers, sanity-checked against the current 11-company dataset but not statistically derived — will need to become sector-relative once the universe is larger and more diverse
- `compute_flags()` in `15_forensics.py` assumes a ratio column exists if ANY company in the filtered input has it — true for the full 11-company universe, but a single-company `--company` filter can hit a company with zero rows for some ratio_name. Guarded with `safe_year_col()` where found so far; a new flag added later that reads a prior-year value the same unsafe way could reintroduce this class of bug. (`webapp/app.py` no longer calls `compute_flags()` directly — as of the `forensics_flag` table below, it reads the persisted result instead, so this specific fragility only matters when running `15_forensics.py` itself with `--company`.)
- Forensics flags are persisted to a `forensics_flag` table (`sql/schema_forensics.sql`), written by `15_forensics.py`'s `save_to_db()` as a delete-then-insert scoped to whichever companies were just computed — not an upsert, since a flag that stops triggering (e.g. after a ratio-engine fix) needs to actually disappear rather than linger with nothing to overwrite it
- Pernod Ricard's June 30 fiscal year end doesn't align with the calendar-year FX rates and comps snapshots used elsewhere (flagged explicitly at query time, not silently ignored)
- `pipeline.yml`'s scheduled run executes `--mode analyze`, which now includes ratios (see Automation above) — `full`/`load`/`historical` still require manually running on a machine that has the raw filing `.zip` files
- Amplifon and Shell have no `gross_profit` or `cost_of_sales` tagged at all (a "by nature" P&L presentation with no COGS/gross-profit split in their statements) — 3-statement model, DCF, DIO and DPO are correctly left unavailable for them rather than derived from a guess. Every other company either tags one directly or derives it from the other via the textbook Revenue − COGS identity (see `11_ratio_engine.py`)
- Beta is a raw live yfinance value per company, not unlevered/relevered by each peer's own capital structure before averaging — fine when comparing companies with similar leverage, understated rigor for a company like Shell whose leverage differs meaningfully from its DCF peer set
- The comps/3-statement/DCF/precedents/market-risk/credit layer (`19`-`24_*.py`) is run by hand, not yet wired into `run_pipeline.py`'s `--mode analyze` or the weekly cron — see the note under [Architecture](#architecture)
- `net_debt_ebitda_proxy` (in `11_ratio_engine.py`/the Ratios tab, display label "Net Debt vs Op. Profit") is Net Debt / EBIT despite its name — a real naming inconsistency found while building `24_credit.py`, which computes true Net Debt/EBITDA separately rather than reusing that column. Not renamed here because other code already depends on the existing name; worth a rename pass on its own

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the current phase-by-phase plan, what's
done, and what's explicitly deferred — kept in one place rather than
duplicated here.
