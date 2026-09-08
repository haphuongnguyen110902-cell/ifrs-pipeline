# IFRS/XBRL Financial Analysis Pipeline

**Live demo:** [ifrs-pipeline dashboard](https://haphuongnguyen110902-cell-ifrs-pipeline-webappapp-iwnn9s.streamlit.app/) — filter by country/sector, browse ratios and earnings-quality flags for 11 European listed companies.

Automated pipeline for parsing, normalising, and analysing IFRS/XBRL financial filings from European listed companies.

## What it does

- **Parses** ESEF/XBRL packages directly from ESMA-regulated filings using Arelle's Python API
- **Normalises** raw XBRL tags to standardised IFRS concepts via a 640+-tag semantic mapping layer
- **Stores** facts in PostgreSQL (Neon) with full provenance — every number traceable back to its original XBRL tag and filing
- **Validates** data against accounting identities and published annual reports
- **Loads historical depth** — up to 9 years per company (2017–2025), one filing row per company/year, not just the latest annual report
- **Computes** financial ratios: gross/operating/net margin, ROIC, ROE, cash conversion, DSO/DIO/DPO/CCC (working capital), effective tax rate, leverage
- **Flags earnings-quality issues** — rule-based forensics (leverage spikes, cash-conversion deterioration, thin-denominator distortion) with automatic severity downgrades when a flag's own baseline is itself distorted
- **Forecasts** each ratio (CAGR + linear regression) and **backtests** both methods with rolling-origin cross-validation to pick a winner per company/ratio, with a confidence label based on how many folds actually support the pick
- **Converts currencies** per IAS 21 (ECB average rate for P&L, closing rate for balance sheet), auto-detecting which currencies are actually in use
- **Builds trading comps** (EV/EBITDA, EV/Sales, P/E) in EUR, combining historical filing data with live market data
- **Auto-classifies** new companies using the IFRS taxonomy's own presentation linkbase — standard tags require zero manual work
- **Orchestrates** the full pipeline end-to-end with `run_pipeline.py --mode full`, or just the analysis layer with `--mode analyze`
- **Serves** a public Streamlit dashboard (`webapp/app.py`) — filter by country/sector, browse ratios and forensics flags per company
- **Refreshes automatically** every Monday via GitHub Actions (`--mode analyze`, no local files needed — see [Automation](#automation))
- **Tests itself** — 51 pytest regression tests covering every bug found and fixed during development, run on every push via CI

## Current status — V2 complete, dashboard live, automation running weekly

| | |
|---|---|
| Companies | 11 European listed companies (consumer, luxury, energy, hygiene) |
| Countries | France, Italy, Spain, Sweden, United Kingdom |
| Concepts mapped | 642 XBRL tags across income statement, balance sheet, cash flow |
| Facts in database | 13,997 across all companies and years |
| Years covered | 2017–2025 (varies by company; up to 9 years for some) |
| Ratios computed | 10: margins, ROIC, ROE, cash conversion, DSO/DIO/DPO/CCC, tax rate, leverage |
| Regression tests (data) | 5/5 pass — verified against L'Oréal's published 2024 annual report |
| Regression tests (code) | 51 pytest tests, run on every push via GitHub Actions CI |
| Forensics flags | 62 across 10 companies (26 high / 12 medium / 24 low severity) |
| Backtest coverage | 66/70 company-ratio pairs have enough rolling folds to pick a method |
| Unmapped facts | 0 — all non-dimensional facts fully mapped |
| Dashboard | Live and public (see link above), refreshed weekly by CI |

**Companies:** L'Oréal, LVMH, Kering, EssilorLuxottica, Puig Brands, Danone, Pernod Ricard, Essity, Moncler, Shell, Amplifon

See [`ROADMAP.md`](ROADMAP.md) for what's done, in progress, and next — that file is the single source of truth for project direction.

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
Arelle parser  →  raw facts        18_fx_convert.py      19_valuation.py
      ↓                                     ↓                     ↓
Semantic mapping (642 tags)  →  normalised IFRS concepts        (EUR)
      ↓
PostgreSQL / Neon  ←─── single source of truth
      ↓         ↓         ↓          ↓            ↓           ↓
Statements   Ratios   Validation  Forensics   Forecast    Backtest
 (Excel)  (DB+Excel)   (5/5)     (DB+Excel)  (DB+Excel)  (DB+Excel)
                                        ↑ all read from the `ratio` table,
                                          not from fact_value directly
                                        ↓
                              webapp/app.py (Streamlit) → public dashboard
```

`run_pipeline.py --mode full` runs the left-to-right chain once end to
end; `--mode analyze` re-runs just forensics → forecast → backtest
without re-parsing anything, for fast iteration on the analysis layer.

Two independent CI workflows sit on top of this:
- **`.github/workflows/tests.yml`** — runs `pytest tests/` on every push, no database needed (every test uses synthetic data)
- **`.github/workflows/pipeline.yml`** — runs `run_pipeline.py --mode analyze` every Monday against the live Neon database (see [Automation](#automation) below)

## Automation

The pipeline refreshes itself weekly with no manual intervention:

1. Every Monday 06:00 UTC, GitHub Actions triggers `pipeline.yml`
2. It runs `run_pipeline.py --mode analyze` — forensics → forecast → backtest, reading straight from the already-populated Neon database
3. Results write back to the database; the dashboard (which queries live) reflects the refresh automatically

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

# or all at once:
python run_pipeline.py --mode full      # everything, first run
python run_pipeline.py --mode analyze   # forensics+forecast+backtest only, fast re-run

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
| `19_valuation.py` | Trading comps (EV/EBITDA, EV/Sales, P/E) in EUR |
| `run_pipeline.py` | Orchestrates the above (`--mode full` / `--mode analyze` / others) |
| `webapp/app.py` | Public Streamlit dashboard — filter by country/sector, browse ratios + forensics |
| `tests/` | pytest regression suite (51 tests) — see each file's docstring for the real bug it locks in |

## Known limitations

- Dimensional facts not loaded — segment/geographic breakdowns excluded
- Sign conventions partially handled
- Canonical concept layer missing — adjusted operating profit definitions not yet unified across companies
- Forensics thresholds (thin-margin, extreme cash-conversion, leverage) are fixed, sector-agnostic numbers, sanity-checked against the current 11-company dataset but not statistically derived — will need to become sector-relative once the universe is larger and more diverse
- `19_valuation.py`'s yfinance calls are written against the documented API but still untested against a live call as of this writing — see the script's own docstring
- `compute_flags()` in `15_forensics.py` assumes a ratio column exists if ANY company in the filtered input has it — true for the full 11-company universe, but a single-company filter (as `webapp/app.py` uses) can hit a company with zero rows for some ratio_name. Guarded with `safe_year_col()` where found so far; a new flag added later that reads a prior-year value the same unsafe way could reintroduce this class of bug
- Pernod Ricard's June 30 fiscal year end doesn't align with the calendar-year FX rates and comps snapshots used elsewhere (flagged explicitly at query time, not silently ignored)
- `pipeline.yml`'s scheduled run only executes `--mode analyze` (see Automation above) — `full`/`load`/`historical` still require manually running on a machine that has the raw filing `.zip` files

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the current phase-by-phase plan, what's
done, and what's explicitly deferred — kept in one place rather than
duplicated here.
