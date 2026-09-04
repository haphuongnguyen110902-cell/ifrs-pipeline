# IFRS/XBRL Financial Analysis Pipeline

Automated pipeline for parsing, normalising, and analysing IFRS/XBRL financial filings from European listed companies.

## What it does

- **Parses** ESEF/XBRL packages directly from ESMA-regulated filings using Arelle's Python API
- **Normalises** raw XBRL tags to standardised IFRS concepts via a 536-tag semantic mapping layer
- **Stores** facts in PostgreSQL (Neon) with full provenance — every number traceable back to its original XBRL tag and filing
- **Validates** data against accounting identities and published annual reports
- **Computes** financial ratios across 11 companies and 5 years: gross margin, operating margin, net margin, ROIC, ROE, cash conversion, effective tax rate
- **Auto-classifies** new companies using the IFRS taxonomy's own presentation linkbase — standard tags require zero manual work

## Current status — V1 complete

| | |
|---|---|
| Companies | 11 European listed companies (consumer, luxury, energy, hygiene) |
| Concepts mapped | 536 XBRL tags across income statement, balance sheet, cash flow |
| Facts in database | 3,231 across all companies and years |
| Years covered | 2021–2025 (varies by company) |
| Validation | 53/53 accounting identity checks; 5/5 regression tests vs L'Oréal published 2024 report |
| Unmapped facts | 0 — all non-dimensional facts fully mapped |

**Companies:** L'Oréal, LVMH, Kering, EssilorLuxottica, Puig Brands, Danone, Pernod Ricard, Essity, Moncler, Shell, Amplifon

## Sample output — comps table (2024)

| Company | Gross Margin | Op. Margin | ROIC | Cash Conversion |
|---|---|---|---|---|
| Moncler | 78.1% | 29.5% | 19.5% | 108.0% |
| L'Oréal | 74.2% | 19.0% | 17.6% | 100.4% |
| Puig Brands | 74.9% | 15.8% | 13.8% | 97.5% |
| LVMH | 67.0% | 22.3% | 16.4% | 100.1% |
| EssilorLuxottica | 63.4% | 13.0% | 5.4% | 141.4% |
| Danone | — | 12.3% | 8.2% | 113.4% |
| Essity | 32.4% | 12.6% | 10.4% | 91.8% |
| Shell (USD) | — | 10.5% | 8.2% | 182.8% |

*Currency-neutral ratios are comparable across EUR/SEK/USD without FX conversion.*

## Architecture

```
ESEF filing (.zip)
      ↓
Arelle parser  →  raw facts (concept, value, period, unit, currency)
      ↓
Semantic mapping layer (536 tags)  →  normalised IFRS concepts
      ↓
PostgreSQL / Neon  ←─── single source of truth
      ↓           ↓            ↓            ↓
 Statements    Ratios     Validation   Coverage
 (Excel)    (DB + Excel)  (53/53)      checks
```

**Key design decisions:**

**Database as single source of truth.** Nothing is stored as a snapshot. All outputs are computed from `fact_value` at query time. Fix a mapping → reload → everything updates automatically.

**Taxonomy-driven classification.** New companies use the IFRS taxonomy's own presentation linkbase (role numbers: `[2xxxxx]`=balance sheet, `[3xxxxx]`=P&L, `[5xxxxx]`=cash flow). Standard `ifrs-full:` tags classify themselves. Only company extensions need human review (~10 min per company).

**Human-in-the-loop extension review.** `12_prep_company.py` separates auto-classifiable tags from company-specific extensions. Extensions go to a REVIEW file; the human classifies only the genuinely ambiguous part. `13_batch_prep.py` pools extensions across multiple companies into one review pass.

**IAS 21 FX design.** Facts stored in native currency (EUR, SEK, USD). Ratios are currency-neutral by construction. Planned FX conversion follows IAS 21: closing rate for balance sheet, average rate for P&L and cash flow.

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
python scripts/08_validate.py
python scripts/11_ratio_engine.py
```

## Scripts reference

| Script | Purpose |
|---|---|
| `00_find_filing.py` | Find and download ESEF filings from filings.xbrl.org |
| `04_create_schema.py` | Create PostgreSQL schema |
| `08_validate.py` | Accounting identity checks + regression tests |
| `09_batch_load.py` | Load all companies into the database |
| `11_ratio_engine.py` | Compute ratios, write to DB + Excel |
| `12_prep_company.py` | Prep a new company: auto-classify + flag extensions |
| `12_apply_review.py` | Apply human-reviewed extension classifications |
| `13_batch_prep.py` | Batch prep multiple companies at once |

## Known limitations

- Dimensional facts not loaded — segment/geographic breakdowns excluded (planned V3)
- FX conversion not yet built — ratios are valid without it; absolute values are not cross-comparable
- Sign conventions partially handled
- Canonical concept layer missing — adjusted operating profit definitions not yet unified across companies

## Roadmap

| Phase | Status | Description |
|---|---|---|
| V0 | ✅ Complete | Single company, full pipeline |
| V1 | ✅ Complete | 11 companies, ratio engine, 536 concepts, validation |
| V2 | Planned | Forecasting, DCF, scenario analysis |
| V3 | Planned | FX conversion, segment data, credit analysis |
| V4 | Planned | Autonomous filing discovery, scheduled updates, notification-based extension review |
