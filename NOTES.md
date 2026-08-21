# IFRS Pipeline Project - Running Notes
_Last updated: 2026-08-21_

## Where things stand
- **L'Oreal**: fully through the pipeline (parsed -> mapped -> loaded -> statements generated). V0 complete.
- **11 filings downloaded** in `data/raw/`: loreal_2025, LVMH_2024, Kering_2023, essilorluxottica_new, puig, danone, pernod_ricard, essity, Moncler_2025, Shell_2025, Amplifon_2025
- **Mapping covers 303 concepts** after the batch classification (was 100 for L'Oreal alone)
- **Only L'Oreal is actually loaded into the database.** Everything else is parsed-and-classified but not loaded.

## Open / Unfinished
- [ ] Run `_extend_mapping_batch.py` on the local machine (adds 203 concepts -> 303 total)
- [ ] Load LVMH, then the other 9 companies, into the database
- [ ] Run `08_validate.py` and fix whatever mapping errors it surfaces
- [ ] Build the FX rate table + conversion layer (design decided, see below)
- [ ] Build the canonical-concept layer so company-specific tags become comparable
- [ ] Build the ratio engine (can start NOW - ratios are currency-neutral, no FX needed)
- [ ] Connect to a remote GitHub repo (still local-only)

## Key Decisions
- **Group/consolidated filings only**, never subsidiary-level
- **Skip Germany and Ireland** - officially unindexed on filings.xbrl.org
- **V1 stops at ~10 companies**, not "all of Europe" - full coverage is V4 territory
- **Different fiscal year ends and gappy year coverage are fine** - schema stores real dates, assumes no shared matrix
- **Automated downloader built** (`00_find_filing.py`) - `--search`, `--entity --download`, and `--batch "A,B,C"` modes via filings.xbrl.org JSON:API
- **Scripts are delivered as downloadable files, not chat copy-paste** - the paste workflow already lost one update silently

### FX / currency (decided 2026-08-21, not yet built)
- **Principle: IAS 21.** Balance sheet (instant facts) -> closing rate at period end. Income statement and cash flow (duration facts) -> average rate over the period. The `period_type` column already encodes this distinction, so no schema change is needed to apply the right rate.
- **Store local currency only in `fact_value`.** Never overwrite as-reported values. Add a separate `fx_rate` table (currency, date, rate, source) and convert at QUERY time, not load time. Reasons: rates get revised; presentation currency may change; keeps database-as-single-source-of-truth intact.
- **Expose both local and EUR** in outputs, local as primary.
- **Ratios need no FX at all** - currency cancels out in a ratio, so margins/ROIC/cash conversion can be built across all companies before any FX work.
- **Growth rates DO get contaminated by FX.** Build both reported and constant-currency (organic) growth once growth metrics exist.
- **Scope: only 2 of 11 companies are non-EUR** (Essity=SEK, Shell=USD). Real but small.
- **Rate source: ECB euro reference rates** (free, official, ~30 currencies, published ~16:00 CET each working day, history back to 1999).
  - Daily XML: `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`
  - Full history + SDMX API via ECB Data Portal, dataflow `EXR`, key pattern like `D.SEK.EUR.SP00.A`
  - Python option: `pandaSDMX`; simpler wrapper: Frankfurter API
  - CAVEAT: ECB states these are for information only and discourages transactional use. Fine for analysis/presentation, worth a footnote in the README.

## Bugs Found & Fixed
- **Arelle loaded 0 facts silently** -> fix: `PackageManager.addPackage()` + `rebuildRemappings()` before loading, so namespace URLs resolve to files inside the zip instead of the live internet
- **Balance Sheet years one year ahead** -> fix: subtract 1 day from `end_date` for instant facts (Arelle's exclusive-boundary convention applies to instants too)
- **Statement lines printed alphabetically** -> fix: explicit `STATEMENT_ORDER` per statement, alphabetical fallback for anything unlisted
- **`00_find_filing.py` returned internal DB id (`14`) instead of a real identifier** -> fix: extract from the entity's `relationships.filings.links.related` URL
- **Currency stored but never displayed** -> fix: `07_generate_statements.py` now prints reporting currency in the header and per statement

## Known weaknesses (acknowledged, not all fixed)

### 0. WE WERE HAND-DOING WORK THE TAXONOMY ALREADY CONTAINS (biggest finding)
Verified directly against Arelle: every concept exposes
`concept.label()` (official name, multi-language), `concept.periodType`
(instant/duration), and `concept.balance` (debit/credit). Every filing also
carries a PRESENTATION LINKBASE declaring its own statement structure, and
IFRS taxonomy roles are numbered ([2xxxxx]=balance sheet, [3xxxxx]=P&L,
[4xxxxx]=OCI, [5xxxxx]=cash flow, [6xxxxx]=changes in equity).

So the ~300 hand-classifications were largely unnecessary:
- `periodType == instant` is definitionally a balance sheet item - zero judgment needed
- `balance` (debit/credit) solves the sign-convention problem for free
- `label()` gives official display labels, better than my hand-written ones
- The presentation linkbase gives the filer's own statement structure AND ordering,
  which is what `STATEMENT_ORDER` in 07_generate_statements.py reinvents by hand

**`scripts/10_auto_classify.py` implements this.** Three tiers, each labelled
in the output so confidence is visible:
  1. Presentation linkbase role number (authoritative - the filer's own view)
  2. periodType (instant -> balance_sheet, definitionally certain)
  3. Concept-name keywords (LOW CONFIDENCE, explicitly marked)
Anything none of the three resolve is marked REVIEW rather than guessed.

Tested 4/4 correct against a purpose-built synthetic filing. NOT yet run
against a real ESEF filing - that's the first thing to do with it.
Use `--compare data/mappings/ifrs_concepts_v0.yaml` to diff the taxonomy's
answer against my hand-classifications. Where they disagree, the taxonomy
is almost certainly right.

### 1. OUTPUT FORMATTING NEEDS REAL WORK (do this before showing anyone)
Current statement output is functionally correct but visually raw. For a
portfolio piece the presentation matters as much as the numbers. Backlog:
- **Scale**: show EUR millions or billions, not 43486800000. Filings are
  rounded to the nearest 100k anyway (`decimals=-5`), so full precision is fake.
- **Subtotals and indentation**: real statements nest (Revenue, then indented
  cost lines, then a ruled Gross Profit subtotal). The presentation linkbase
  already encodes this hierarchy - use it instead of a flat list.
- **Sign presentation**: expenses in brackets, per accounting convention.
- **Excel styling**: openpyxl can do number formats, bold subtotals, frozen
  header rows, column widths. Currently it's a bare dump.
- **Currency + scale in the header**, e.g. "in EUR millions" - partially done.
- **Percentage/ratio formatting** once the ratio engine exists (1 decimal, % sign).
- **Consistent year column ordering** and clear FY labelling for any
  off-calendar fiscal years.

### 2. Company-specific tags are not comparable
`loreal:ResultatDexploitation`, `LVM:ProfitLossFromOperatingActivitiesRecurring...`,
`essi:OperatingProfitExclIAC` are all "adjusted operating profit" with different
definitions, currently three separate concepts. Needs a canonical layer - schema
already supports it (`xbrl_tags` is a list, `concept_mapping` is many-to-one).

### 3. The "exactly 3 facts" filter silently drops data
Anything appearing 2x or 4x vanishes with no trace. Balance sheets commonly
show only 2 comparative years, so for any 2-year filing we may be dropping
balance sheet items entirely. Should be a RANGE (1-5), not exactly 3 - the
dimensional filter is doing the real work. Amplifon showing 0 new concepts
from 431 facts is a candidate symptom.

### 4. Parsing re-runs from scratch every time
Each batch run re-parses all 11 filings (minutes). Filings never change once
published, so: skip parsing if the facts CSV is newer than the zip. Note
Arelle's global state is NOT thread-safe, so cache rather than parallelise.

### 5. Dimensional facts dropped entirely
No segment or geographic data. Blocks V3 (FX/geographic exposure). The
`dimensions` JSONB column exists but is never populated.

### 6. No regression tests
One test asserting L'Oreal FY2024 revenue = 43,486,800,000 would catch any
future mapping change that breaks known-good numbers. `tests/` exists, empty.

### 7. Screenshots are a poor feedback channel
They truncate. Better: `python scripts\08_validate.py > validation_output.txt`
then upload the file - complete and copyable.

### 8. README is still the generic starter version
For the portfolio goal this file matters more than any script - it's what a
recruiter actually opens.

### 9. Scope: stop adding companies, verify the ones loaded
11 companies with unverified mappings is worth less than 4 verified. Shell
(oil & gas) and Amplifon aren't real comparables for a beauty/luxury set -
they were useful to prove the pipeline generalises, but would be noise in
a comps table.

## Project risks (flagged 2026-08-21)

**Reassuring first: almost nothing here is unfixable.** The raw `.zip` filings
are immutable and the whole pipeline is re-runnable from them, so every
downstream decision (the 3x filter, dropped dimensions, all 303
classifications, the schema, currency handling) is *recomputable*. Delete the
database, re-run, done. This property is called reproducibility, and it's what
converts design mistakes from permanent into "an afternoon of re-running."

The only genuinely irreplaceable things are the source zips - and even those
can be re-downloaded via `00_find_filing.py`. So: no known dead ends.

### R1. Single point of failure - no backup, no remote repo
Everything lives in one folder on one laptop. Mitigations: (a) copy the
project folder to cloud storage - 5 min, do immediately; (b) GitHub remote -
the proper answer, but a first-time setup can take an hour, so give it its own
session rather than letting it eat a working day. All scripts also exist as
downloadable files in the chat history, so code loss is recoverable rather
than catastrophic.

### R2. All engineering, zero analysis so far
Parsing, mapping, loading, validating - not one computed margin. The stated
goal is financial analysis; for controlling/IB roles the analysis IS the
deliverable. Real failure mode: beautiful infrastructure, no insight about
L'Oreal vs LVMH. **Mitigation: the ratio engine is the next session's
priority.** Ratios are currency-neutral, so FX work doesn't block it.

### R3. Nothing has been checked against reality
The three accounting identities in `08_validate.py` check INTERNAL
consistency. They would all pass even if every number were scaled wrong - if
a bug divided everything by 1,000, Assets would still equal
Equity+Liabilities. Mitigation: manually compare five line items against
L'Oreal's published annual report. Once. 20 minutes. This is the difference
between verification ("built it right") and validation ("built the right
thing"), and no script substitutes for it.

### R4. Code ownership - almost all of it was written by Claude
If the scripts can't be explained or modified by the user, it isn't really
their project, and an interview will establish that in about 90 seconds.
"How does your parser handle taxonomy resolution?" is a fair question about
claimed work. Mitigation: rewrite one small script from scratch without
copying - `06_check_revenue.py` is ~15 lines. Slower, uglier code that is
understood beats better code that isn't.

### R5. Scope creep
Every question so far has been good (all of Europe, more companies, full
automation) but each expands the project. Five things at 80% is worth less
than two things finished. For the portfolio goal, a COMPLETE V0+V1 with a
strong README beats a sprawling half-built V3.

### R6. Credential exposure
`.gitignore` covers `.env` - verify before any GitHub push. A password
committed to a public repo remains in that repo's history even after
deletion. The Neon password was already exposed once in chat and reset.

### R7. Neon free-tier retention
Free tiers may suspend or delete inactive projects. Worth reading Neon's
retention policy. Recoverable (the pipeline can re-run) but better known in
advance than discovered.

## How to link this together autonomously (the plan)

The current scripts are standalone CLI tools that each re-implement loading, DB connection, and year logic. To make the pipeline self-running, three refactors in order:

**Step 1 - Extract shared logic into `src/`.** Right now `01_explore_filing.py`, `04_batch_scan_concepts.py`, and the loaders each contain their own copy of `load_filing()` / `dump_facts()`. Move these into `src/parsing.py`, `src/mapping.py`, `src/database.py`, `src/fx.py`. Scripts become thin CLI wrappers over those functions. This is what makes step 2 possible at all.

**Step 2 - One orchestrator, `run_pipeline.py`, driven by a config file.** A `companies.yaml` lists each company (name, search term or identifier, expected currency). The orchestrator loops:
  1. Download if the zip isn't already present (skip otherwise - idempotent)
  2. Parse
  3. Diff concepts against the mapping. **If unmapped concepts appear, FAIL LOUDLY and stop** rather than silently skipping - this is the one step needing human judgment, and hiding it is how bad data enters
  4. Load into the database (already idempotent via get_or_create)
  5. Run validation; report failures but don't necessarily halt
  6. Refresh FX rates, recompute ratios, regenerate outputs

**Step 3 - Schedule it.** GitHub Actions on a cron (weekly is plenty - annual filings don't change often). The workflow runs `run_pipeline.py` against the Neon database, which is already cloud-hosted, so nothing needs migrating. Store `DATABASE_URL` as a GitHub secret, never in the repo.

**Why the database-first design already makes this work:** every downstream artifact (statements, ratios, dashboard, Excel) reads from `fact_value` and recomputes rather than storing its own frozen copy. So refreshing the source automatically refreshes everything downstream on next run. That property was designed in from V0 and is what makes "everything updates together" achievable rather than a rewrite.

**One thing NOT to automate:** classification of new concepts. That needs judgment about what a tag means. The orchestrator should surface them and stop, not guess.

## Note on Arelle (worth revisiting)
We use `Cntlr.Cntlr()` directly. Arelle's own docs describe `Cntlr` as a base class not intended for direct use, and point to a newer `Session` API (`from arelle.api.Session import Session`) as the supported integration path. It also has **built-in ESEF validation** (`disclosureSystemName='esef'`, `plugins='validate/ESEF'`) which could complement our accounting-identity checks with real taxonomy-level validation. Our current approach works; this is an improvement to consider, not an emergency. Also note: Arelle's global state is NOT thread-safe, only one Session at a time - relevant if parallelising the batch parse later.

## Environment reminders
- Project folder: `C:\Users\User\Downloads\ifrs-pipeline\ifrs-pipeline\`
- Each new session: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` then `.venv\Scripts\Activate.ps1`
- `.env` holds the real Neon `DATABASE_URL` - confirm it's the real one, it was swapped for a test value during debugging at one point
- Neon password was exposed once in chat early on and reset
