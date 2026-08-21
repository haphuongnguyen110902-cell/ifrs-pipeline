# IFRS/XBRL Pipeline (V0)

Parses ESEF/XBRL filings and turns them into clean, structured, standardized
financial statements, with full provenance from raw XBRL fact to normalized
concept.

## Status: V0 (foundations)
One company, full pipeline: parse -> map -> store -> reconstruct statements.
See `ROADMAP.md` (add the full 24-section vision doc there) for V1-V4.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You'll also need a running PostgreSQL instance. Create a `.env` file:

```
DATABASE_URL=postgresql://user:password@localhost:5432/ifrs_pipeline
```

Then create the schema:

```bash
psql $DATABASE_URL -f sql/schema.sql
```

## Getting a filing

Download an ESEF package (zip containing the .xhtml + taxonomy) for one
company. Good starting points: L'Oréal, Schneider Electric (both are
large, well-documented ESEF filers). Search their investor relations page
for "ESEF" or "XBRL annual report". Drop the zip into `data/raw/`.

## Week 1: first parse

```bash
python scripts/01_explore_filing.py data/raw/<company>.zip
```

This loads the filing with Arelle and dumps every numeric fact to a CSV
in `data/raw/`, so you can see the raw shape of the data before designing
the mapping layer.

## Project structure

```
sql/schema.sql              -- PostgreSQL schema (company, filing, period,
                                ifrs_concept, concept_mapping, fact_value)
data/mappings/               -- XBRL tag -> normalized concept mapping (YAML)
scripts/                     -- pipeline scripts, run in order (01_, 02_, ...)
src/                          -- reusable library code once scripts stabilize
tests/                        -- pytest tests
```

## Roadmap

- **V0** (this phase): one company, full pipeline, PostgreSQL storage
- **V1**: 8-10 companies, ratio engine, earnings-quality flags
- **V2**: forecasting tournament, DCF/comps, scenario engine
- **V3**: credit analysis, event studies, FX exposure
- **V4**: dashboard, automated Excel output, CI/CD automation
