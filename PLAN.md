# Next plan — detailed execution plan (2026-09-09)

Decided sequence: **prerequisites → Phase 10 → staged breadth.**
Rationale in [`SCOPE.md`](SCOPE.md) §6. This file is the how.

Every work package below states: objective, why now, dependencies, exact
changes, verification, rollback, risk, and what it unblocks. Nothing here is
"do it better" — each item is a specific, checkable change.

**Effort is given in work sessions (a focused half-day), not hours**, because
the learning time matters as much as the typing time.

*A note on where this file came from: it was reviewed against the actual
repo before being committed — see WP0's status below and the two corrections
folded into WP3a/WP3c. Everything else checked out (the TEXT-keyed table
list, `TICKER_MAP`, the `pernod_companies` hardcode) against the real code
before being trusted.*

---

## Ground rules for this whole plan

1. **One work package per branch, per PR.** Never mix a schema migration with
   a feature. If something breaks, you want to know which change did it.
2. **Tests are written inside the WP that creates the behaviour**, per the
   standing rule already in ROADMAP.md. No batching tests at the end.
3. **Every WP ends with the pipeline actually executed**, not just imported.
   `run_pipeline.py --mode analyze` at minimum.
4. **Additive over destructive** (project rule #5). Old columns get deprecated,
   not dropped, until a full green run proves nothing reads them.
5. **The database is the only source of truth** (rule #14). Every fix below
   moves a hardcoded Python constant *into* the database, never the reverse.

---

# WP0 — Safety net (do not skip) ✅ DONE

**Objective:** make every later step reversible.

**Why now:** WP1 rewrites five tables. Phase 4 already produced one genuine
data-loss incident in this project (`09_batch_load.py --reset-facts` silently
wiping 2017-2020 history). That happened *once*; the cost of preventing the
second one is twenty minutes.

**What was actually built, and one correction made along the way:**
1. `tests/baseline/*.csv` — a read-only golden snapshot of `ratio`,
   `valuation`, `dcf_valuation`, `credit_profile`, `market_risk` pulled
   directly from the live DB (804 / 11 / 9 / 52 / 11 rows respectively).
2. `tests/test_baseline_regression.py` — compares a live SELECT of those five
   tables against the frozen CSVs.

**The correction:** this WP's original wording said "re-run the analysis
layer and assert the numbers still match, byte-identical." That's right for
`ratio` (computed purely from stored `fact_value`, genuinely deterministic),
but wrong for `valuation`, `dcf_valuation` and `market_risk` — all three embed
**live market data** (today's market cap, a live risk-free rate/beta from
yfinance, a trailing price window), so re-running those scripts tomorrow
legitimately produces different numbers with zero bugs involved. A
"must be byte-identical after recompute" test built that way would be
permanently flaky. What WP1 actually needs is narrower: prove the re-keying
migration doesn't change any row's *existing* values. The test does a
**snapshot-compare** instead (read now, migrate, read again, diff) — same
protection WP1 needs, without ever depending on market data holding still.

**Verification done:** the test caught two real bugs *while it was being
written* — a CSV round-trip turning the genuine string `'n/a'` into `NaN`
(pandas' default `na_values` list includes that string) and a `DATE` column
comparing as a Python `date` object on one side and a string on the other.
Both were bugs in the comparison code, not the data; fixed, and the test now
passes cleanly comparing the live DB to itself (5/5 tables). The plan's own
suggested extra step — deliberately corrupt one live value and confirm the
test fails, then restore it — was **not** run, since it requires a write
against production; the two bugs caught above already demonstrate the test
is sensitive to a real mismatch. Do that live rehearsal before relying on
this test for WP1 if you want the extra confidence.

**Rollback:** n/a — this WP only adds.

**Risk:** low. **Effort:** 0.5 session.

**Unblocks:** everything. WP1 is unsafe without it.

---

# WP1 — Migrate the five TEXT-keyed tables to `company_id` ✅ DONE

**Objective:** every analysis table joins to `company` by integer FK.

**Why now:** at 11 companies a company *name* is unique and stable, so this is
invisible. At 200+ it breaks on accent drift (`L'Oreal` vs `L'Oréal`),
renames, and near-collisions — and because there is **no FK constraint**, a
mismatch renders an empty dashboard tab instead of raising. That is
wrong-looking-right, the failure mode this project's own rules (#13) forbid.
It is cheap today and requires rewriting hundreds of thousands of rows later.

**Exact surface — measured, not assumed:**

| Table | Current key | Written by |
|---|---|---|
| `valuation` | `company TEXT`, `UNIQUE(company, year)` | `19_valuation.py` |
| `dcf_valuation` | `company TEXT`, `UNIQUE(company, base_year)` | `22_dcf.py` |
| `market_risk` | `company TEXT`, `UNIQUE(company, benchmark)` | `23_market_risk.py` |
| `credit_profile` | `company TEXT`, `UNIQUE(company, year)` | `24_credit.py` |
| `three_statement_projection` | `company TEXT`, `UNIQUE(company, base_year, forecast_year)` | `21_three_statement_model.py` |

Already correct (leave alone): `ratio`, `forecast`, `backtest` — all
`company_id INTEGER REFERENCES company(company_id)`.

**Two ways to do this:**

| | Approach | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | Add `company_id` alongside, backfill by name, add FK, switch all readers/writers, keep `company TEXT` as a deprecated denormalised label | Fully reversible at every step. Old code keeps working during the transition. Matches rule #5 | Two columns coexist for a while; must remember to drop later |
| B | Drop `company` TEXT, replace with `company_id` in one migration | Clean immediately | Irreversible mid-flight. Any missed reader breaks with no fallback. Rejected |

**Steps (approach A):**
1. New file `sql/migration_001_company_id.sql`:
   - `ALTER TABLE <t> ADD COLUMN IF NOT EXISTS company_id INTEGER;` ×5
   - Backfill: `UPDATE <t> SET company_id = c.company_id FROM company c WHERE c.name = <t>.company;`
   - **Guard:** `SELECT company FROM <t> WHERE company_id IS NULL;` must return
     zero rows. If it doesn't, a name in an analysis table has no match in
     `company` — investigate before proceeding, do not force it.
   - `ALTER TABLE <t> ADD CONSTRAINT fk_<t>_company FOREIGN KEY (company_id) REFERENCES company(company_id);`
   - Replace each `UNIQUE(company, ...)` with `UNIQUE(company_id, ...)`.
   - Index on `company_id`.
2. Update the five writer scripts to look up `company_id` once and bind it.
   Keep writing `company` TEXT for now (belt and braces).
3. Update `webapp/app.py`'s five loaders to query on `company_id`. They
   currently take `company_name`; change the signature to `company_id` so a
   name can no longer be passed by accident.
4. Migration is idempotent (`IF NOT EXISTS`, guarded `UPDATE`) — rule #15.
   Run it twice in testing and confirm identical state.

**Verification:**
- `tests/test_migration_company_id.py`: asserts all five tables have a non-null
  `company_id` for every row, and that the FK exists.
- Re-run [`tests/test_baseline_regression.py`](tests/test_baseline_regression.py)
  (WP0) before and after the migration — it snapshot-compares rather than
  recomputing, so it isn't sensitive to live market data moving; a failure
  here means the backfill mismatched a row.
- Click every tab in the live app for at least 3 companies including
  EssilorLuxottica (the accented/edge-case names).

**Rollback:** drop the added columns and constraints; `company TEXT` still
carries everything.

**Risk:** medium — it touches existing data. Mitigated by WP0 and by the
null-guard. **Effort:** 1–1.5 sessions.

**Unblocks:** the screener (cross-table joins), and all of breadth.

**What actually happened, and one bug found running it:**
- The guard was checked read-only *before* writing the migration: all 11
  companies matched cleanly across all five tables by name, zero mismatches
  (no accent drift, no missing rows) — so the backfill ran clean on the
  first try.
- **Real bug found running this, not assumed:** `sql/migration_001_company_id.sql`'s
  first draft claimed to be idempotent (`IF NOT EXISTS` everywhere) but
  wasn't — PostgreSQL's `ADD CONSTRAINT` has no `IF NOT EXISTS` form, so
  running the file a second time (the plan's own required idempotency
  check) failed with `DuplicateObject`. Caught because the check was
  actually run, not assumed to pass from the `IF NOT EXISTS` on the column
  additions alone. Fixed by wrapping each `ADD CONSTRAINT` in a `DO` block
  that checks `pg_constraint` first; re-ran a third time with no error and
  [`tests/test_baseline_regression.py`](tests/test_baseline_regression.py)
  still green.
- Deliberately diverged from this WP's own "replace `UNIQUE(company,...)`"
  wording: the old constraint is kept alongside the new
  `UNIQUE(company_id, ...)` rather than dropped, per the plan's own ground
  rule #4 (additive over destructive) — that's what makes the documented
  rollback ("drop the added columns/constraints; `company` TEXT still
  carries everything") actually true.
- All five writer scripts (`19_valuation.py`, `22_dcf.py`,
  `23_market_risk.py`, `24_credit.py`, `21_three_statement_model.py`) now
  resolve `company_id` before every write, skip-with-a-printed-warning
  (never write a NULL) if a company name has no match, and `ON CONFLICT`
  on `company_id` instead of `company` TEXT. Verified by actually calling
  each modified `save_to_db()` against the live DB with real data (not
  just reading the diff) — all five wrote successfully through the new
  path with zero value drift afterward.
- `webapp/app.py`'s five Phase 4-6 loaders (`load_comps`, `load_three_statement`,
  `load_dcf`, `load_market_risk`, `load_credit_profile`) now take
  `company_id: int` instead of `company_name: str`, matching how
  `load_ratios`/`load_backtest` already worked.
- Clicked through all five migrated tabs for **three** companies live
  (not just one): EssilorLuxottica (the accented-name edge case named in
  this WP), Shell (non-EUR reporter — confirmed its correctly-empty DCF
  tab still renders as "not computed" rather than erroring, since Shell
  is one of the two companies with no DCF by design), and Kering (confirmed
  its FY2021-2023-only data window still shows correctly). Zero console
  errors, zero server errors.
- 20 new regression tests (5 in `tests/test_migration_company_id.py`'s
  non-null/FK checks, plus a third check — that `company_id` and the
  legacy `company` TEXT column agree on every row — not originally
  specified but added since a mismatch there would be worse than a NULL,
  it would silently look right). Full suite: 151/151 passing
  (131 original + 5 baseline regression + 15 migration checks).

---

# WP2 — Persist forensics flags ✅ DONE

**Objective:** `15_forensics.py` writes to a `forensics_flag` table.

**Why now:** it is the only analysis module that persists nothing.
`webapp/app.py` currently `importlib`-loads `scripts/15_forensics.py` at
request time and recomputes flags on every page render. Three problems:
- it couples the web layer to the `scripts/` directory layout
- at 300 companies it becomes the app's bottleneck
- **it makes the screener impossible** — you cannot answer "every company with
  ≥2 HIGH flags" without a stored table

**Changes:**
1. `sql/schema_forensics.sql` — the row shape already exists in code
   (`compute_flags()` emits exactly these keys):
   ```
   forensics_flag_id SERIAL PRIMARY KEY,
   company_id     INTEGER NOT NULL REFERENCES company(company_id),
   year           INTEGER NOT NULL,
   flag_id        TEXT NOT NULL,
   label          TEXT,
   severity       TEXT CHECK (severity IN ('high','medium','low')),
   value          NUMERIC,
   detail         TEXT,
   what_to_check  TEXT,
   computed_at    TIMESTAMP DEFAULT now(),
   UNIQUE(company_id, year, flag_id)
   ```
   Note it is born with `company_id` — never TEXT. This is why WP1 comes first.
2. Add `save_to_db()` to `15_forensics.py`, upsert on the unique key
   (idempotent, rule #15). Wire it into `run_pipeline.py --mode analyze`.
3. `webapp/app.py`: replace `render_forensics()`'s `importlib` block with a
   plain `SELECT`. Delete the dynamic import entirely.

**Also fix while here (a scaling landmine):** `compute_flags()` contains
`pernod_companies = ["Pernod Ricard"]` (confirmed at `15_forensics.py:323`) —
a hardcoded company name driving the FYE warning. Replace with a
`company.fiscal_year_end` check (see WP3c — this already exists on `filing`,
just not read here). One hardcoded name is a note; at 300 companies it is
a bug.

**Verification:** flag count in the DB must equal the count the old live
recompute produced for the same 11 companies (README says 62: 26 high / 12
medium / 24 low). Any difference is a real discrepancy to explain, not to
accept. Extend `tests/test_forensics.py`.

**Rollback:** the app can fall back to the recompute path; keep it behind a
flag for one release.

**Risk:** low. **Effort:** 0.5–1 session.

**Unblocks:** the screener.

**What actually happened:**
- `sql/schema_forensics.sql` created with exactly the row shape specified
  above, born with a real `company_id` FK from the start — no legacy TEXT
  column to carry forward, since this table postdates WP1.
- `save_to_db()` does a **delete-then-insert scoped to the companies in
  the current run**, not an upsert — a deliberate design decision beyond
  what this WP originally specified: a forensics flag can legitimately
  *stop* triggering (e.g. a future ratio-engine fix corrects an input),
  and an upsert alone would leave that now-wrong flag sitting in the
  table forever with no incoming row to overwrite it. Verified this is
  the actual behavior with a dedicated test
  (`test_rerunning_save_to_db_is_idempotent_not_additive`).
- `webapp/app.py`'s `render_forensics()` now takes a pre-fetched
  DataFrame from a new `load_forensics(engine, company_id)` loader (same
  `company_id`-keyed pattern WP1 established for the other five tabs)
  instead of `importlib`-loading `15_forensics.py` and recomputing —
  the dynamic import is gone entirely, as specified.
- **The `pernod_companies = ["Pernod Ricard"]` hardcode fix from this
  WP's original scope was deliberately NOT done here.** It depends on a
  `company`-level fiscal-year-end field that doesn't exist yet — only
  `filing.fiscal_year_end` does, per-filing — and that field is WP3c's
  job, not WP2's. Fixing it now would mean guessing at WP3c's eventual
  column name/shape. Left as-is, still flagged as a known landmine for
  WP3c to actually close.
- **Real, pre-existing bug found while verifying, unrelated to this WP's
  scope:** running `python scripts/15_forensics.py` normally on Windows
  crashes with `UnicodeEncodeError` — the emoji in `print_summary()`
  can't encode to the default `cp1252` console codepage, and the crash
  happens *before* `save_to_db()` ever runs. Verified with
  `PYTHONIOENCODING=utf-8` as a workaround (not a fix) to actually get
  the DB-writing step to execute for verification. Not fixed here — it
  predates this WP and touches a different part of the script (output
  formatting, not persistence) — flagged as a separate follow-up task
  instead, since fixing it here would mix concerns per this plan's own
  ground rule #1.
- Verification performed: flag count matched the README's documented 62
  (26 high / 12 medium / 24 low, 10 companies) exactly on the first live
  run; re-ran a second time and got the identical count (idempotent, not
  additive); confirmed zero NULL `company_id` rows. 4 new tests in
  `tests/test_forensics.py`'s new `TestPersistence` class (DB-skip-guarded,
  same pattern as `test_baseline_regression.py`): table creation is
  idempotent, the documented flag count, delete-then-insert (not
  additive) on a second run, and an unresolvable company name is
  skipped rather than written with a NULL. Clicked through the live app's
  Forensics tab for Amplifon (HIGH_LEVERAGE flags) and EssilorLuxottica
  (confirmed the 2020 THIN_DENOMINATOR + downgraded-to-low 2021
  CASH_CONVERSION_DROP still render exactly as documented) — both now
  reading from the persisted table, zero console/server errors. Full
  suite: 155/155 passing.

---

# WP3 — Extend the `company` table (one migration, three additions) ✅ DONE

**Objective:** move the last hardcoded per-company constants into the database.

**Why now:** all three are needed by breadth, all three touch the same table,
and doing them as one migration means `04_create_schema.py` changes once.

### 3a. Sector taxonomy — this one is a hard blocker
Today `company.sector` is free text: **9 distinct strings across 11
companies** (Consumer / Beauty, Luxury Goods, Consumer / Eyewear, Consumer
Staples, Consumer / Beverages, Consumer / Hygiene, Luxury Apparel, Energy,
Consumer Health Retail — recounted directly from `data/companies.yaml`).
`19_valuation.py` groups peers on this column and requires ≥2 peers, which is
exactly why the live app prints *"Fewer than 2 sector peers"* — **the implied
valuation feature is already dead, today, at 11 companies.**

You chose a sector comparison view for the site, so this is a prerequisite,
not a nice-to-have: peer comparison grouped on free text is meaningless.

| | Source | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | yfinance `sector`/`industry` | Free, already a dependency, GICS-derived, consistent across companies, machine-assigned | Somewhat US-centric; a third-party opinion you must disclose |
| B | NACE (official EU) | Official, free, EU-native | Statistical classification, not investment-oriented — groups things investors would never compare |
| C | Hand-mapped custom 10-sector scheme | Full control, defensible | Manual at 300 companies; your judgement becomes an unstated assumption |

GICS and ICB proper are proprietary — excluded by the free-tools constraint.

**Changes:** add `sector_std TEXT`, `sector_source TEXT`, and rename the
existing free text to `sector_detail` (keep it — it is genuinely more precise
for the 11 you curated). `19_valuation.py` groups on `sector_std`.

**Verification:** after populating, assert **every** sector group used for peer
medians has ≥2 members, or is explicitly excluded. Re-run `19_valuation.py`
and confirm implied valuation now produces numbers for companies where it
previously printed the "median of one" guard. That flipping from n/a to a real
number *is* the acceptance test.

### 3b. Ticker + ISIN + LEI columns
Add `ticker TEXT`, `ticker_exchange TEXT`, `isin TEXT`, `ticker_source TEXT`.
`lei` already exists on `company` (confirmed at `sql/schema.sql:6`,
unpopulated for most rows). Populated by WP4.

### 3c. `fiscal_year_end`
**Correction: this already exists, just on the wrong table for this purpose.**
`filing.fiscal_year_end DATE` is already there (`sql/schema.sql:16`), set
per-filing. What's actually missing is a company-level default/current FYE
that forensics and the calendar-year-mapping rule can check without joining
to `filing` and picking a row — so add `company.fiscal_year_end_month_day`
(or similar) **derived from `filing.fiscal_year_end`** (e.g. the most recent
filing's month/day), not a second independent source of the same fact —
writing it as a fresh, separately-maintained value would violate this plan's
own rule #5 (database is the only source of truth; don't duplicate it).
Pernod Ricard's June 30 FYE is currently handled by ad-hoc notes in
`companies.yaml` and the hardcoded name list in forensics (see WP2). At 300
companies — UK retailers especially — this needs to be data plus an explicit
rule mapping a filing to a calendar year, not per-company notes.

**Verification:** a test asserting no analysis script contains a hardcoded
company name. Grep-based, crude, effective.

**Risk:** low (purely additive). **Effort:** 1 session.

**What actually happened, all three sub-parts, plus two real bugs found:**

**3a (sector_std) — verified live, not assumed:** queried yfinance's own
`sector` field for all 11 `TICKER_MAP` tickers before writing any code.
Real result: **Consumer Defensive (5: L'Oreal, Danone, Pernod Ricard,
Essity, Puig Brands), Consumer Cyclical (3: LVMH, Kering, Moncler),
Healthcare (2: EssilorLuxottica, Amplifon), Energy (1: Shell, correctly
alone)** — three real peer groups where the old 9-distinct-free-text-strings
scheme had zero. `company.sector` is untouched (still the detail text
shown in the company header/sidebar) — deliberately did NOT do the
literal "rename to `sector_detail`" this WP originally specified, per
ground rule #4 (additive over destructive): renaming would have broken
every existing reader of `company.sector` (the sidebar filter,
`09_batch_load.py`, `load_historical.py`) with no transition period.
Added `sector_std`/`sector_source` alongside instead, populated via a new
`populate_sector_std(engine, force=False)` in `19_valuation.py` (using
`TICKER_MAP` for the ticker — the same pre-WP4 source this script already
depends on). `19_valuation.py`'s `comps["sector"]` (and therefore
`valuation.sector`, `ev_ebitda_sector_median`, `n_peers_in_sector`,
`implied_ev_from_peers`, `premium_vs_peers_pct`) now derive from
`sector_std`, not the free text — verified live: L'Oreal's Trading Comps
tab now reads *"Sector peer comparison (5 peers in Consumer Defensive):
peer median EV/EBITDA 11.6x implies an EV of €95.5bn — this company trades
at a +113% premium to that"* where it used to say *"Fewer than 2 sector
peers."* 3 new tests in `tests/test_valuation.py` (new file — this script
had none before).

**3b (ticker/ISIN/LEI columns)** — added exactly as specified
(`ticker`, `ticker_exchange`, `isin`, `ticker_source`), empty, for WP4 to
populate. `lei` confirmed already present (`sql/schema.sql:6`).

**3c (fiscal_year_end) — two real bugs found running this, not assumed:**
1. Confirmed via a live query before writing any migration: `filing.
   fiscal_year_end` already exists (as this WP's own correction already
   noted) — but the backfill query
   (`sql/migration_002_company_metadata.sql`) that derives
   `company.fiscal_year_end_month/day` from it came back **NULL for
   Pernod Ricard specifically**, the one company this whole sub-part
   exists for. Traced to the real root cause: `load_historical.py`
   deliberately excludes Pernod Ricard (its own comment says so — the FYE
   mismatch this WP is trying to fix), and `09_batch_load.py`'s
   `get_or_create_filing()` never sets `filing.fiscal_year_end` or
   `filing_date` at all on the single-filing path — every OTHER company
   happens to have a `load_historical.py`-loaded row to draw a real value
   from, Pernod Ricard doesn't.
2. Fixed via the exact same "data existed in `companies.yaml`, never
   wired through" pattern as V2.6's sector/country fix — `companies.yaml`
   already had `fiscal_year_end: "June 30"` for Pernod Ricard since V0,
   documentation only, nothing ever read it. `09_batch_load.py`'s
   `get_or_create_company()` now accepts a `fiscal_year_end` string,
   parses it (`_parse_fiscal_year_end()`), and backfills
   `company.fiscal_year_end_month/day` via the same COALESCE-never-
   overwrite-a-real-value pattern already used for sector/country.
   Applied live (without needing to re-run the full, zip-dependent batch
   load): `company.fiscal_year_end_month/day` for Pernod Ricard is now
   `(6, 30)`, correctly derived from the human-verified source.
3. `15_forensics.py`'s `pernod_companies = ["Pernod Ricard"]` hardcode
   (deferred from WP2) is now closed: `compute_flags()` takes an
   `off_calendar_fye: dict` parameter instead, and a new
   `fetch_off_calendar_fye(engine)` builds it from
   `company.fiscal_year_end_month/day`. The function has zero company
   names baked in now — verified with tests that fire the flag for a
   company deliberately named something other than "Pernod Ricard", and
   confirm naming a company "Pernod Ricard" in test data alone (with no
   dict entry) triggers nothing. Live run still produces the identical
   62-flag count (26/12/24), with `PERNOD_FYE_WARNING` now correctly
   generated from data rather than a hardcoded name match.
4. **Deviated from "grep-based, crude, effective" verification** in favor
   of the functional genericity tests described above — a literal
   "no script contains the string 'Pernod Ricard'" grep would also flag
   `TICKER_MAP`/`download_historical.py`'s per-company config dicts
   (explicitly WP4's job to replace, not this WP's) and plain comments,
   producing false failures unrelated to the actual hardcode this WP
   closes.

**Baseline regression note:** re-running `19_valuation.py` legitimately
changed `valuation.market_cap_eur` (live market data moved since WP0's
snapshot) and every peer-comparison column (the intended effect of 3a) —
`tests/baseline/valuation.csv` was regenerated to reflect this; the other
four tables were untouched and still matched their original snapshot
unchanged.

Full suite: 167/167 passing — 155 after WP2, +5 in `test_batch_load.py`
(3 `_parse_fiscal_year_end` cases + 2 `get_or_create_company` FYE-backfill
cases), +4 in `test_forensics.py` (3 off-calendar-fye genericity tests +
1 live check that Pernod Ricard actually resolves), +3 in the new
`test_valuation.py` = 167.

---

# WP4 — Entity resolution: LEI → ISIN → ticker ✅ DONE (partial - see below)

**Objective:** new `scripts/26_entity_resolution.py`. Retire `TICKER_MAP`.

**Why now:** `19_valuation.py` holds `TICKER_MAP` (confirmed at
`19_valuation.py:104`), a hardcoded Python dict keyed by company name mapping
to (Yahoo ticker, quote currency). Every new company requires a **source code
edit**, and valuation, DCF *and* market risk all silently skip anything
unmapped (`"no ticker mapped - skipped"`). This is the single hardest wall
between you and 300 companies.

**The free path (all verified free):**
```
filings.xbrl.org  →  LEI  (already the entity identifier there)
GLEIF ISIN-to-LEI relationship file (free bulk download)  →  ISIN
OpenFIGI API (free, batched, rate-limited)  →  ticker + exchange code
exchange code  →  yfinance suffix (.PA .MI .MC .AS .ST .CO .HE .OL .L ...)
```

**Alternatives considered:** yfinance's own search endpoint (undocumented,
fuzzy, silently returns wrong companies — rejected for a project whose whole
claim is provenance); paid identifier vendors (violates the free constraint).

**Changes:**
1. `26_entity_resolution.py`: reads companies lacking a ticker, resolves,
   writes `isin`/`ticker`/`ticker_exchange`/`ticker_source` to `company`.
2. Cache the GLEIF file locally; it is large and immutable per release
   (rule #32).
3. **Never guess.** A company that does not resolve gets `ticker = NULL` and
   `ticker_source = 'UNRESOLVED'` — an explicit unknown, per rule #7. Do not
   fall back to a fuzzy name search.
4. `19_valuation.py`, `22_dcf.py`, `23_market_risk.py` read the ticker from the
   DB. Delete `TICKER_MAP` in the same PR — leaving both is how they drift.

**Verification — this is the important part:**
- Spot-check **all 11 current companies** by hand against the existing
  `TICKER_MAP`. The resolver must reproduce every one of them. If it disagrees
  on any, the resolver is wrong until proven otherwise — `TICKER_MAP` is
  hand-verified and currently correct.
- Then run it on ~30 unseen companies and **record the match rate as a
  number.** That number is a real result and belongs in the README.
- Confirm market cap comes back in the currency the ticker actually quotes in
  (this is what `TICKER_MAP`'s second element encodes today — do not lose it).

**Risk:** medium. External APIs, rate limits, and a real chance the match rate
is disappointing for smaller Nordic names. **Effort:** 1.5–2 sessions.

**Unblocks:** breadth entirely. Without this, company #12 onward has no market
data.

**What actually happened - built, verified live, and honestly incomplete:**

**The free path changed shape once actually built, verified live before
committing to it:**
- `filings.xbrl.org` was dropped in favor of **GLEIF's own LEI-search API**
  (`api.gleif.org/api/v1/lei-records?filter[entity.legalName]=...`) -
  more authoritative for name→LEI than filings.xbrl.org's sparse entity
  endpoint (confirmed: `filter[name]=` on that endpoint returned zero
  results for every company name tried).
- The ~1GB+ **bulk ISIN-to-LEI relationship file** this WP originally
  specified was dropped in favor of **GLEIF's own per-LEI ISIN endpoint**
  (`.../lei-records/{lei}/isins`) - found live while building this: it
  serves the identical mapping, queried on demand, which is far more
  practical at 11→low-hundreds scale than downloading and indexing a
  bulk file for a few hundred lookups.

**A real complication found building this, not assumed:** one LEI maps
to MANY ISINs, not one - L'Oreal's alone has 32 (equity + bond issuances
across currencies/tenors), and a single legal name can match SEVERAL
ACTIVE LEIs (LVMH returned 9: "ACTIONS LVMH", "LVMH Group Treasury",
"LVMH LUXURY VENTURES FUND I", ..., and the real parent, "LVMH MOET
HENNESSY LOUIS VUITTON" - no name-text heuristic reliably tells them
apart). Solved by **verifying candidates against real market data
instead of guessing from name text**: for an ambiguous LEI, each
candidate's ISINs are checked via OpenFIGI until one actually resolves
to a live, exchange-listed common-stock security - a subsidiary/
treasury/foundation LEI does not itself have separately listed common
stock, so this is a verification, not an inference.

**A second real bug found running this, not assumed, and actually
fixed:** the first live check flagged Essity as a "mismatch"
(`TICKER_MAP` says `ESSITY-B.ST`, resolver said `ESSITYB.ST`) - not a
wrong company, a wrong ticker FORMAT: OpenFIGI's raw `ticker` field
doesn't carry the hyphen yfinance requires for share-class tickers.
Confirmed live: `ESSITYB.ST` genuinely 404s on yfinance; `ESSITY-B.ST`
works. Fixed with `resolve_working_ticker()` - validates a candidate
ticker against yfinance itself before trusting it (never persist a
plausible-looking-but-broken value, per this project's own "never
guess" rule), trying one well-justified normalization (insert a hyphen
before a trailing single-letter share-class suffix) if the raw form
fails. This turned the one real mismatch into a correct resolve on
re-verification.

**A third real, structural finding: OpenFIGI's anonymous rate limit is
tighter and has a longer cooldown than a fixed per-request delay alone
can survive.** A company with many bond ISINs (Danone: 60+) can burn
through the anonymous budget mid-company and trigger a wall of 429s
that a flat delay doesn't recover from. Fixed with
`_post_openfigi_with_retry()` - exponential backoff (3 attempts) on a
429 specifically, not just a longer flat delay. This measurably
improved the real match rate across successive live runs as the fix
landed: 1/11 → 3/11 (partially rate-limited) → 6/11 (1 mismatch, since
fixed) → **7/11, 0 mismatched, 4 unresolved**, the number recorded below
and in the README per this WP's own instruction ("that number is a real
result and belongs in the README").

**Final live spot-check against all 11 current companies (this WP's own
required verification step):**

| Result | Companies |
|---|---|
| Matched (7) | L'Oreal, Kering, Pernod Ricard, Essity, Moncler, Amplifon, Puig Brands |
| Mismatched (0) | none |
| Unresolved (4) | LVMH, EssilorLuxottica, Danone, Shell |

All four unresolved cases are large multinationals where the real
equity ISIN sits behind more bond ISINs than
`MAX_ISINS_TO_CHECK_PER_LEI` (20) practically allows to check under
OpenFIGI's rate limit even with retry/backoff, or (Shell, EssilorLuxottica)
GLEIF's own candidate list didn't yield a working listing within the
LEI-candidate cap (10) tried. Not a logic bug - a real, disclosed
external-API constraint. **Not fixed further here**: an OpenFIGI API
key (free to obtain, materially higher rate limits) would likely close
most of this gap, but signing up for a third-party account is not
something to do on the user's behalf without asking first - flagged as
the clear next step rather than done here.

**Decision: `TICKER_MAP` is NOT retired.** `19_valuation.py`'s new
`resolve_ticker_currency()` prefers `TICKER_MAP` (hand-verified, still
correct for all 11 companies) and falls back to the DB-resolved
`company.ticker`/`ticker_currency` only for a company `TICKER_MAP`
doesn't have - the opposite priority from this WP's original "delete
TICKER_MAP in the same PR" instruction, and deliberately so: at 7/11
real coverage, deleting it would be a regression, not a cleanup.
`22_dcf.py`/`23_market_risk.py` updated to the same fallback pattern.
Revisit retiring `TICKER_MAP` only once the resolver's real coverage is
re-measured and materially better (e.g. with an API key, or once WP7's
company universe makes hand-maintaining `TICKER_MAP` itself impractical).

**Also added, not originally specified:** `company.ticker_currency`
(`sql/migration_003_ticker_currency.sql`) - `TICKER_MAP`'s second
element (quote currency) had no column to land in after WP3b's
original scope (`ticker`/`ticker_exchange`/`isin`/`ticker_source` only).
A small exchange→currency table (`EXCH_TO_CURRENCY`) mirrors
`EXCH_TO_YF_SUFFIX`, with the same "never guess an unmapped exchange"
rule - includes a note on the LSE's GBX-pence complication, matching
`19_valuation.py`'s existing documented Shell simplification.

**Verification performed:** 12 new tests in `tests/test_entity_resolution.py`
(the pure `pick_best_equity_hit`/`_ticker_variants` ranking and
ticker-normalization logic - the network-calling functions are
deliberately exercised live via `--verify`, per this WP's own spec,
not mocked). Migration 003 applied and confirmed idempotent (ran twice,
no error). `tests/test_baseline_regression.py` stayed green throughout -
this WP never touched the five WP1-migrated tables. Full suite passing
(see commit for the exact count).

---

# WP5 — Thin screener (validate the pattern before breadth, not after) ✅ DONE

**Objective:** a universe landing page in the existing Streamlit app.

**Why now and not after breadth:** this is the same "walking skeleton"
reasoning that justified Phase 2 — build the vertical slice while it is still
cheap to discover the data model does not support it. Building the screener
*after* 300 companies are loaded means discovering any schema gap at the
most expensive possible moment.

**Changes:**
1. A SQL view `company_latest_metrics`: one row per company with latest-year
   operating margin, ROIC, net debt/EBITDA, EV/EBITDA, and a forensics flag
   count. **One query for the whole universe** — not the current per-company
   N+1 pattern (9 separate cached queries per selected company).
2. `webapp/app.py`: landing = that table, sortable and filterable by country
   and `sector_std`. Click a row → the existing 9-tab company page,
   **completely unchanged**.
3. Change the country/sector filters to default to *nothing selected* rather
   than everything — at 300 companies the current default dumps the whole
   universe on load.
4. Fix the hardcoded `"11-company universe"` string in `render_comps()`; make
   it read the real count.

**Verification:** the landing table must render in one DB round trip. Time it.
If it is doing one query per company, it will not survive 300.

**Risk:** low. **Effort:** 1 session.

**What actually happened, including a real bug found in the same class as
an already-known one:**

- `sql/schema_screener.sql` defines `company_latest_metrics`, a VIEW
  (not a table - no module "owns" it the way other schema files' tables
  are owned, since it joins `ratio`/`valuation`/`credit_profile`/
  `forensics_flag` across modules) joining each source table's own
  latest year per company, plus all-time HIGH/total forensics flag
  counts. One query for the whole universe, verified live: **0.057s for
  all 11 companies**, `webapp/app.py`'s new `ensure_screener_view()` +
  `load_screener()` replace what used to be a 9-tab-per-company render.
- **Real bug found running this against live data, the same class as an
  already-known one:** Pernod Ricard's `operating_margin`/`roic` came
  back NULL on the first live query. Traced to the exact case
  `21_three_statement_model.py`'s `select_base_year_row()` already
  exists for: Pernod Ricard's June 30 fiscal year end leaves its
  numerically latest `ratio` year (2025) completely empty, and the
  view's original `DISTINCT ON (company_id, ratio_name) ORDER BY year
  DESC` picked that empty row over 2024's real value. Fixed by filtering
  `WHERE value IS NOT NULL` (ditto for `valuation.ev_ebitda` and
  `credit_profile.net_debt_ebitda`) before ranking by year - skip NULLs
  before picking "latest", the same fix in a new place. Locked in with a
  regression test that cross-checks the view's answer against the
  `ratio` table directly rather than hand-coding the expected number.
- **A second real bug, found by actually clicking through the app, not
  by reading the code:** the initial filter logic used a Python ternary
  that fell back to the literal `True` (not a pandas Series) when a
  multiselect was empty - `mask_a & mask_b` where BOTH masks are plain
  `True` collapses to a scalar `True`, and `df[True]` raises
  `KeyError(True)` (pandas tries to look up a column named `True`).
  Crashed the app on first load, before any filter was ever touched.
  Fixed with `pd.Series(True, index=...)` as the empty-filter fallback
  instead of a bare Python `True`.
- Click-to-select via `st.dataframe(..., on_select="rerun",
  selection_mode="single-row")` (native since Streamlit 1.35; this app
  runs 1.63) - clicking a row's checkbox loads that company's existing
  9-tab detail view below, unchanged, exactly as specified. Defaults to
  the first row selected so the detail view always shows something
  useful on first load, rather than an empty "click a row" placeholder.
- Country/sector filters now default to nothing pre-selected, but an
  empty selection is treated as "no filter" (shows everything) rather
  than "filter to nothing" - deliberately different from a literal
  reading of "default to nothing selected": a blank landing page on
  first load would be a worse default than today's, and the actual
  problem this WP names (300 pre-checked chips cluttering the sidebar)
  is solved either way.
- Sector filter now uses `sector_std` (WP3a), not the free-text
  `sector` - per this WP's own spec. The company header/caption above
  the 9 tabs still shows the free-text detail (`France · Consumer /
  Beauty`), untouched.
- `render_comps()`'s hardcoded `"11-company universe"` string now takes
  `n_companies` as a parameter, sourced from `len(companies)` at
  render time - verified live (still reads "11" today, correctly, but
  now from the query rather than a literal).
- 4 new tests in `tests/test_screener.py` (DB-guarded, same pattern as
  `test_baseline_regression.py`): one row per company, the Pernod Ricard
  NULL-skip case specifically, no unexpected NULLs across the current
  universe, and that a single query returns every company's full metric
  set. Full suite: 183/183 passing.

---

# WP6 — Phase 10: the one-pager ✅ DONE

**Objective:** one polished PDF or PPTX per company — comps table, DCF range,
key ratios, forensics flags — generated from data already in the DB.

**Why here:** everything it needs is already built (Phases 4–9 are done). It is,
by ROADMAP.md's own assessment, the highest-leverage-per-hour item **for
interviews specifically**, and it serves the Contrôleur de Gestion search —
your stated priority #1 — which breadth does not. Doing it before breadth
means you have a portfolio artifact in hand during the weeks that breadth
would otherwise consume.

**Changes:** `scripts/27_onepager.py`. Read the `pdf` or `pptx` skill *after*
the content is settled, not before. Cross-check that the DCF, comps and
precedent ranges shown together actually reconcile — three methods converging,
or an explicit sentence on why they do not, is the deliverable. A one-pager
that shows three numbers with no view is not an analyst's output.

**Verification:** generate for all 11, read all 11 yourself, and hand one to
someone who does not know the project. If they cannot say what the company is
worth and why, it is not finished.

**Risk:** low. **Effort:** 1.5–2 sessions.

**What actually happened, three real bugs found by actually reading the
rendered PDF, not by reading the code:**

- `scripts/27_onepager.py` fetches everything already persisted (`ratio`,
  `valuation`, `dcf_valuation`, `credit_profile`, `forensics_flag`,
  `precedent_transaction`) - no recomputation, matching this WP's own "nothing
  here is new analysis" framing. `reportlab` (Platypus) added to
  `requirements.txt`, chosen per the `pdf` skill's own guidance for
  building a structured document, read *before* writing any layout code
  per this WP's own instruction.
- **The actual deliverable is `build_reconciliation()`**: DCF Enterprise
  Value, peer-implied EV (from trading comps), and the company's actual
  market EV, always shown together with a one-sentence verdict on
  whether they converge - never one number with the other two hidden.
  Rendered as a real "football field" bar chart (reportlab shapes, no
  extra dependency), not just a table of three numbers, so the spread is
  seen, not just read.
- **Bug 1, found by looking at the first rendered PDF:** the page was
  mostly blank below ~40% down an A4 sheet - Platypus doesn't auto-size
  the page to content, and 8.5pt/tightly-spaced tables simply didn't
  fill it. Fixed with larger type (10-13pt), more generous section
  spacing, and the bar chart itself (real content, not padding).
- **Bug 2, found immediately after fixing bug 1:** the fix overcorrected
  - L'Oreal and EssilorLuxottica (the only 2 companies with a matching
    precedent transaction) now overflowed to a near-empty second page,
  and a naive first fix left the *section heading* orphaned alone at the
  page-1/page-2 boundary with its content on page 2. Fixed by trimming
  padding/spacing precisely (not guessing at a page-size change) and
  wrapping the precedent heading + its paragraph in `KeepTogether` so
  they move as one unit if they ever don't fit - verified by regenerating
  and reading the actual PDF again, not assumed fixed from the diff.
- **Bug 3, a real crash:** `decimal.Decimal` values from psycopg2/
  SQLAlchemy don't mix with the `float` math reportlab's own units (`mm`)
  and the bar chart use - `Decimal * float` raised `TypeError`. Fixed by
  coercing every numeric to `float()` once, in `build_reconciliation()`,
  rather than at each later use site.
- **Bug 4, a second real crash found generating all 11 (not just the one
  spot-checked first):** one company's `implied_ev_from_peers` came back
  as a genuine float `NaN` (not SQL `NULL`), which `is not None` alone
  doesn't catch - it passed straight through into the bar chart and
  crashed reportlab's own PDF renderer (`cannot convert float NaN to
  integer`), deep enough in a third-party library that the real cause
  wasn't obvious from the traceback alone. Fixed with a `_real()` helper
  (`v is not None and not pd.isna(v)`) used everywhere a DB value is
  checked for presence in this script.
- **Bug 5, a data-integrity bug, not a crash:** "D&A fallback" rendered
  as "D&A;" - reportlab's `Paragraph` parses its content as restricted
  XML, and an un-escaped `&` in dynamic text gets read as the start of a
  malformed entity, silently garbling real analytical content rather
  than raising an error. Fixed by escaping every dynamic string
  (`xml.sax.saxutils.escape`) at the point it enters a `Paragraph` -
  table cells via a shared `_cell()` helper, plus each forensics-flag/
  precedent field individually (not the whole markup string, which
  would have also escaped intentional `<b>` tags).
- A plain-string `Table` cell doesn't wrap in reportlab - only a
  `Paragraph` flowable does. Found live: "Sector peer median EV/EBITDA"
  ran straight into its value with no visible gap (the label overflowing
  the column, not wrapping). Fixed generally, not just for that one
  label, by wrapping every table cell in a `Paragraph`.
- Verified live for all 11 companies, not just spot-checked: every
  one-pager is exactly one page (locked in by a test that actually
  builds all 11 and checks `len(reader.pages) == 1` via `pypdf`), Shell/
  Amplifon correctly show "not available" rather than a fabricated DCF
  number, and both precedent-matched companies (L'Oreal, EssilorLuxottica)
  render correctly with the reconciliation, comps, credit, forensics, and
  precedent sections all present and readable.
- 9 new tests in `tests/test_onepager.py`: pure-function tests for the
  NaN-guard and reconciliation logic (synthetic data, run in every CI
  build), plus a live, DB-guarded end-to-end test that regenerates all 11
  one-pagers and asserts each is a single page with no leaked XML
  entities - exactly the two bugs above, locked in so a future change
  can't reintroduce them silently. Full suite: 192/192 passing.

**Not done here, noted as a real, disclosed limitation:** the layout was
tuned empirically against the current 11 companies' actual data, not by a
general "shrink to fit" algorithm - see README's Known limitations for what
that means for a future company with an unusually long flag list plus a
precedent match.

---

# ⛔ GATE — measure before committing to breadth ✅ MEASURED (2026-09-09/10)

Before WP7, run the ~40-company step and **record three numbers**. Done: a
real sample of 40 companies **not in the database** — 8 each from France,
Italy, Netherlands, Sweden, Belgium (the SCOPE.md-recommended "Nordics +
France + Italy + Benelux" core), deliberately large/index-type names
(Carrefour, Renault, Eni, Heineken, Sandvik, Umicore, BNP Paribas, ...)
matching WP7's own target-universe rule ("national-index member OR market
cap > €2bn"), downloaded live from `filings.xbrl.org` into `data/raw/gate40/`
(gitignored, ~1GB). All measurement ran with **zero writes to the live
database**.

**1. Extension auto-classification — first pass: 36.5% (475/1,302 tags),
FAILS the ~80% bar.** Only 5/40 companies (12.5%) needed zero manual
review. The worst review counts clustered on sectors the original
11-company mapping had never seen — banks (Banco BPM 84 review tags,
Mediobanca 70, KBC Groep 68) and a payment processor (Adyen).

**2. Ticker resolution on unseen companies — 24/40 (60%)**, consistent with
WP4's 63.6% on the known 11 (same root cause already documented there:
several ACTIVE LEI candidates share a similar name, and the real listed
parent isn't always among the first few tried). Not blocking.

**3. Parse throughput — 506 filings/hour** (7.1s/filing average, real Arelle
parse + full fact extraction, 40/40 succeeded). Far better than SCOPE.md's
earlier **unverified** 30-60s/filing guess — at this rate 2,700 filings is
~5-6 hours, not 30+. Not blocking.

**Verdict on the first pass: classification is the one real blocker.** See
WP4b below for what actually fixed it, and two rejected detours along the
way — kept as recorded history, not summarized away.

---

## WP4b — ESEF anchoring: the real, free fix for classification ✅ DONE

**Two rejected detours first, in the order they happened, because both are
useful to know about before anyone reaches for an LLM here again:**

1. **`scripts/28_claude_classify.py`** was built exactly as ROADMAP.md's
   long-deferred "Claude API auto-classifier" item specified — batches the
   827 review tags to Claude via a strict tool schema, writes proposals to
   a review file, never touches the trusted mapping directly. The user
   caught, correctly, that calling it needs a billed `ANTHROPIC_API_KEY`,
   which **directly contradicts this project's own free-tools principle**
   (the same one SCOPE.md already used to reject paid identifier vendors).
   Nobody caught the contradiction earlier — ROADMAP.md had been carrying
   this deferred item for a long time, and this plan's own GATE section,
   two paragraphs up, still said "that is what the Claude-API
   auto-classifier was deferred for" without flagging the conflict either.
   **Kept in the repo as optional infrastructure, not merged, not run.**
   Branch: `feature/claude-classifier`.
2. Instead, the actual classification was done directly inside the Claude
   Code session already building this feature — zero incremental cost,
   same underlying LLM judgment, written to
   `data/mappings/CLAUDE_REVIEW_extensions.yaml` with a
   `classification_source: claude-session-suggested` provenance tag
   (827/827 entries, 641 high confidence). **Also not merged** — the user's
   objection here wasn't cost, it was that a human reading and classifying
   827 tags inside a chat conversation is not reproducible or automatable;
   it doesn't belong in a pipeline that runs again at 150 and 300
   companies. Branch: `classify/gate40-session-review`.

**What actually fixed it — a free, deterministic, regulator-mandated
signal the pipeline had never read.** Under the ESEF RTS, an issuer using
an extension element **in the primary financial statements** must anchor
it to the closest standard IFRS element via the wider-narrower arcrole in
the **definition linkbase** (subtotals are exempt; everything else is
mandatory). That anchor is a machine-readable declaration, not a guess —
and `10_auto_classify.py`/`12_prep_company.py`/`13_batch_prep.py` all only
ever read the **presentation** linkbase (`XbrlConst.parentChild`); nothing
touched the definition linkbase or its anchoring arcrole.

**Verified live before writing a line of classification code** (this
project's own standing rule — see WP0/WP4's own verification sections):
`XbrlConst.widerNarrower` exists in Arelle and resolves to
`http://www.esma.europa.eu/xbrl/esef/arcrole/wider-narrower`; tested
against 5 real filings from the gate40 sample (banco_bpm, adyen, renault,
mediobanca, kbc_groep) and all 5 genuinely declare these relationships —
e.g. Banco BPM's `Acconti_su_dividendi` (one of this pass's own low-
confidence LLM guesses) anchors directly to `ifrs-full:DividendsPaid`, and
KBC's `ExceptionalInterimDividendPaidPerShare` (another low-confidence
guess) anchors to `ifrs-full:DividendsPaidOrdinarySharesPerShare`.

**Implementation, additive to `13_batch_prep.py`:**
- `build_anchor_map(model)` — reads `model.relationshipSet(XbrlConst.widerNarrower)`,
  returns `{extension_tag: standard_anchor_concept}`.
- `resolve_via_anchor(anchor_concept, pres_map, tag_to_statement)` — resolves
  the anchor's OWN statement via the same trust order already used for
  standard tags: (1) already in the trusted mapping (fastest, most direct),
  (2) the anchor's own presentation-linkbase role, (3) the anchor's own
  `periodType`. This is exactly this project's established layered-fallback
  pattern (CLAUDE.md: "XBRL concept-name variance is handled by layered
  fallback, not exceptions"), applied to statement classification instead
  of value extraction.
- `scan_zips()` tries anchoring **first**, for extension tags only, before
  the existing presentation-role/periodType/keyword tiers — a filer's own
  declared anchor outranks a role keyword match.
- 6 new pure-function tests in `tests/test_batch_prep_anchoring.py`
  (`resolve_via_anchor`'s three fallback tiers + the unresolvable case,
  no Arelle model needed) — `build_anchor_map` itself is deliberately not
  unit-tested (same reasoning as `test_entity_resolution.py`'s network
  calls: it needs a real filing, and is exercised live below, not mocked).

**Re-measured against the same 40-company sample, clean mapping state
(642 tags, no detour artifacts): 1,008/1,302 (77.4%) now auto-classify
with zero AI and zero cost** — up from 36.5%, and this time from a real,
reproducible, regulator-grounded signal instead of an LLM's self-reported
confidence. Coverage varies sharply by filer: FinecoBank 64/67 review tags
resolved by anchoring alone, Unilever 29/30, KBC 50/76 — but **Mediobanca
anchored 0 of its 78 unmatched tags**, a real, disclosed limit: not every
filer's extension taxonomy declares anchoring as faithfully as the RTS
requires, and this pipeline should not assume 100% coverage from any one
filer just because most do it well.

**Still short of the 80% bar after anchoring alone — closed by the
materiality screen below. ✅ DONE, both built on the same branch.**

## Materiality screen — the second free lever

A tag worth a fraction of a percent of revenue, that no ratio this
project computes would ever read, doesn't need classification at all —
per rule 7 ("prefer an explicit 'not available' state"), an honest,
deliberate `UNKNOWN` costs nothing, so it's not worth a human's time
either. Added to `13_batch_prep.py`: `build_value_map()` finds the
largest absolute value seen for every concept anywhere in a filing;
`assess_materiality()` compares a still-unclassified tag's own value
against the filing's own revenue (duration concepts) or total assets
(instant concepts) — the same revenue-tag fallback pair
`11_ratio_engine.py`'s `get_best()` already trusts, reused rather than
invented fresh — and flags anything under a 1% screen. Screened-immaterial
tags are **never** added to the mapping; they're written to a separate
`data/mappings/IMMATERIAL_extensions.yaml` audit trail (never applied
anywhere) so the exclusion stays inspectable, not silent.

**A real bug found running this live, not assumed:** the first pass
compared every numeric fact's raw value against revenue regardless of
unit — a share-count concept (`ALS:IncreaseDecreaseInNumberOfShares
OutstandingThroughOtherComprehensiveIncome`, unit=`shares`) was being
divided by a EUR revenue figure, a meaningless cross-unit ratio that
happened to read as "0.00% of revenue" and get silently waved through as
immaterial. Caught by inspecting the actual immaterial sample before
trusting the number (this project's own standing rule), confirmed live
via `concept.isMonetary` (`False`) and `fact.unit` (`shares`). Fixed by
gating the whole screen on `concept.isMonetary` — a share count, a
per-share ratio, or any other non-monetary disclosure now gets no
materiality opinion at all (stays in `all_review`, honestly unscreened)
rather than a spurious currency comparison. This dropped the first,
buggy immaterial count from 80 to the real 65 — smaller, but trustworthy.

**Final, corrected result on the same 40-company sample:**

| | Tags | % of 1,302 found |
|---|---:|---:|
| Auto-classified (anchoring + presentation/periodType tiers) | 1,008 | 77.4% |
| Immaterial — screened, left `UNKNOWN`, zero cost | 65 | 5.0% |
| Still genuinely needs a human | 229 | 17.6% |
| **Handled without a human** | **1,073** | **82.4%** |

**Clears the gate's own ~80% bar — for real this time, with the bug that
would have overstated it already caught and fixed before reporting.**
6 more tests in `tests/test_batch_prep_anchoring.py` (materiality math,
the monetary/instant/duration branches, and a locked-in regression for
the exact non-monetary bug above) — 13 total in that file. Full suite:
205/205 passing.

---

# WP7 — Breadth, staged

**Target universe (decided, per SCOPE.md §3):** programmatic from
filings.xbrl.org, filtered by "in the country's main national index OR market
cap > €2bn", stored in a versioned `universe_membership` table with an
`as_of` date. The versioning is what defends against a survivorship-bias
objection — a hardcoded list of today's winners cannot.

**Start where coverage is actually deep, not where the famous names are:**
Nordics + France + Italy + Benelux. Denmark (2,126 filings), Finland (1,168)
and Norway (958) are better covered than Spain or the Netherlands. Germany,
Ireland, Switzerland and Bulgaria return **zero** — see SCOPE.md §2.

**Steps:**
1. `11 → ~40`. Measure the three gate numbers. Stop and reassess.
2. `~40 → ~150`, only if the gate passes.
3. `~150 → 300+`.

**Step 1a — `universe_membership` table, built ✅.** `sql/schema_universe.sql`
+ `scripts/29_universe_membership.py` implement the market-cap half of the
rule ("member of national index OR market cap > €2bn") - the national-index
half needs verified free constituent data per country (CAC 40, FTSE MIB,
AEX, OMX Stockholm, BEL 20...), a real sourcing task not done yet, flagged
rather than guessed at. Because the rule is an OR, the market-cap half
alone already produces valid candidates. Pipeline reuses three already-
verified modules end to end, no new network client written:
`26_entity_resolution.py`'s `resolve_company()` → ticker,
`19_valuation.py`'s `fetch_market_data()` + `fetch_live_fx_rate()` →
market cap converted to EUR at today's live rate (the same live-vs-
historical split that script's own docstring already establishes for
market cap specifically). A candidate whose ticker doesn't resolve gets
**no row at all** - this table only ever asserts positive inclusion, so
absence means "not yet evaluated", never "excluded" (rule 7).

**Run live against the GATE's own 40-company candidate list
(`data/mappings/wp7_candidates.csv`, the same names used to measure the
classification gate): 20/40 qualify at ≥ €2bn, written to the live DB
with `as_of=2026-09-13`.** The other 20 split into two honestly different
buckets, not conflated:
- **4 genuinely below the threshold** (Piaggio €0.7bn, Dometic Group
  €0.6bn, JM AB €0.7bn, Arjo €0.7bn) - correctly excluded, no bug.
- **16 ticker-UNRESOLVED**, including large, obviously-qualifying names
  (BNP Paribas, Renault, Eni, Unilever, RELX, Assa Abloy, Svenska
  Handelsbanken, Safran, Schneider Electric, Mediobanca, Banco BPM,
  FinecoBank, KBC Group, Cofinimmo, Alstom, Ferrari) - the exact WP4
  LEI-disambiguation gap (several ACTIVE LEI candidates share a similar
  name; the real listed parent isn't always among the first few tried),
  now costing real universe coverage, not just a lower measured
  percentage. These are absent from `universe_membership`, not wrongly
  excluded - re-running this script once ticker resolution improves
  (WP4's own noted next step: an OpenFIGI API key) will pick them up
  without disturbing this `as_of` snapshot.

7 new tests in `tests/test_universe_membership.py` (the qualification
logic mocked at the resolution/market-data boundary - unit + EUR/SEK
currency-conversion cases + both failure paths - real network calls
exercised live above, not mocked, same pattern `test_entity_resolution.py`
already established). Full suite: 212/212 passing.

**Step 1a follow-up — OpenFIGI API key + three real retry bugs found
chasing the 16 unresolved names ✅.** The 16 ticker-UNRESOLVED gap above
was WP4's own noted next step: an OpenFIGI API key. Verified live against
OpenFIGI's own docs before trusting any of it (not assumed) - a free key
raises the anonymous 25-req/minute limit to 25-per-6-seconds (~10x) and
the per-request job batch size from 10 to 100. `26_entity_resolution.py`
now sends the `X-OPENFIGI-APIKEY` header when `OPENFIGI_API_KEY` is set,
with a faster delay and a higher per-LEI ISIN cap (60 vs. 20) when a key
is present - fully backward-compatible, identical behaviour with no key
set.

**Re-running then surfaced real, live non-determinism - not one bug,
three, found the same way each time (a company resolved in one run,
then didn't in the next, with no code change in between):**
1. OpenFIGI read/connect timeouts were silently treated as "no hit for
   that ISIN", no retry - fixed by retrying `Timeout`/`ConnectionError`
   the same way a 429 already was.
2. `resolve_working_ticker()`'s yfinance validation call had a bare
   `except Exception: continue` with **zero retry** - a single transient
   yfinance hiccup under batch load silently killed a candidate that was
   independently confirmed to be genuinely correct (Renault → RNO.PA,
   verified 3/3 in isolation). Fixed with the same retry-with-backoff
   shape.
3. A plain HTTP 5xx from OpenFIGI's own `raise_for_status()` was never
   caught by the retry function at all - it escaped the retry loop
   entirely and was silently absorbed one level up as "no hit". Found by
   independently confirming Thales's real equity ISIN
   (`FR0000121329`) sits well inside the range actually being checked
   (index 22 of 55, cap 60) - it should have been found, and wasn't.
   Fixed by treating 5xx the same as 429.

9 more tests in `tests/test_entity_resolution.py` covering all three
(23 total in that file). Full suite: 223/223 passing.

**Honest result after all three fixes, not overstated:** re-running the
full 40-candidate batch several times the same evening surfaced continued
run-to-run flakiness even after every fix - including, on one run,
**Carrefour** (resolved cleanly on every prior attempt) coming back
UNRESOLVED. This is very likely this project's own cumulative load on
free-tier OpenFIGI/GLEIF/yfinance from six-plus full batch runs plus
numerous one-off tests in a single evening, not a fourth undiscovered
bug - but that's a hypothesis, not confirmed, and is recorded as such.
**The real, DB-verified number: 21 distinct companies now qualify as of
2026-09-16** (a straight `SELECT` against `universe_membership`, not any
single run's own printed count) - net +2 over the original 20, gained
because `save_rows()`'s upsert-only design never deletes a company that
qualified in an earlier run but dropped out of a later one (Thales's
2026-09-13 success stays on record under that date's own snapshot,
untouched by tonight's churn). **Practical guidance for whoever runs
this next: don't trust one run's own count as final - query the table's
union across recent `as_of` dates, and don't re-run the full batch
repeatedly in one sitting once failures start recurring on previously-
reliable names - that's a signal to stop for the day, not to retry
harder.**

## Step 1b — national-index membership, the other half of the rule ✅ DONE

While the market-cap-rule discovery cools down for the night (see above),
built the other, genuinely independent half of SCOPE.md §3's rule:
"member of its country's main national index." `scripts/29_universe_membership.py`
only ever implemented the market-cap half; this was flagged as a real gap
in Step 1a, not guessed at.

**Source: Wikipedia's own index-constituent tables** - verified live
against all 5 target-country indices before writing any code (CAC 40,
FTSE MIB, AEX, OMX Stockholm 30, BEL 20), each carrying a real, dated "as
of" snapshot. Parses with plain `pandas.read_html()`, no LLM, no scraping
heuristics beyond matching on column names - genuinely reproducible by
re-running the script, unlike anything involving an LLM's own judgement.
Deliberately does **not** touch OpenFIGI, GLEIF or yfinance - 4 of 5
index pages' ticker columns are already yfinance-ready (e.g. `AC.PA`);
only BEL 20's `"Euronext Brussels:ABI"` format needs light, explicit
parsing (never a guessed exchange suffix - same rule `EXCH_TO_YF_SUFFIX`
already follows).

**A real gotcha found live, not assumed:** BEL 20's Wikipedia page has
TWO tables with a ticker-like column - the current 20 constituents, and
a 41-row historical/former-members table (`"Period in BEL 20"`). Picking
the first match blindly would have silently grabbed the wrong one.
`find_constituent_table()` fixes this by preferring the table whose row
count is closest to the index's known real size, not just "has a ticker
column."

**A company can qualify under both rules** (Carrefour: CAC 40 member
*and* > €2bn). `save_rows()` never creates a duplicate row for a company
already present today from the other script's run - a disclosed
simplification, not a silent loss: the company's *second* qualifying
reason isn't recorded, its existing row is just left alone rather than
duplicated or overwritten.

**Run live against all 5 indices, written to the same live DB:**

| Index | Country | Constituents found |
|---|---:|---:|
| CAC 40 | France | 40 |
| FTSE MIB | Italy | 40 |
| AEX | Netherlands | 25 |
| OMX Stockholm 30 | Sweden | 30 |
| BEL 20 | Belgium | 20 |

155 constituent rows found, 139 genuinely new (16 already present today
from the market-cap rule, correctly deduplicated - e.g. Carrefour,
Umicore, Heineken). **Universe total for 2026-09-16 jumped from 21 to
160 companies** - a direct `SELECT COUNT(*)` against `universe_membership`,
verified, not estimated. 21 via market cap, 139 via national index.

8 new tests in `tests/test_national_index_membership.py` (the BEL 20
two-tables gotcha, ticker normalization including the real
non-breaking-space artifact found in BEL 20's raw HTML, and the
never-guess-an-unmapped-exchange case). Full suite: 231/231 passing.

**Not yet done, disclosed rather than silently skipped:** the Nordics'
other three indices SCOPE.md recommends (OMX Copenhagen 25, OMX
Helsinki 25, Oslo Børs OBX) - the same Wikipedia-table pattern should
extend cleanly, just not verified live yet for those three pages
specifically.

## ⏭ Next session checklist (written 2026-09-16, night of the OpenFIGI/yfinance flakiness)

**1. Smoke-test before anything else - do not jump straight to the full
40-candidate batch.**
```
python scripts/29_universe_membership.py --candidates data/mappings/wp7_candidates.csv --no-db
```
Actually just run the 2-company version first (Carrefour, Umicore - the
exact pair used tonight) so a bad result costs seconds, not 15-30
minutes:
```
printf "name,country\nCarrefour,France\nUmicore,Belgium\n" > /tmp/smoketest.csv
python scripts/29_universe_membership.py --candidates /tmp/smoketest.csv --no-db
```
- **Clean signal:** both resolve in a few seconds each, zero
  `*** OpenFIGI ...`/`*** yfinance validation failed ...` lines in the
  output. Safe to run the full batch.
- **Still flaky:** Carrefour (or anything) fails, or it takes noticeably
  longer than a few seconds, or any retry-warning lines appear. Do not
  push forward - wait longer (hours, not minutes) and re-test. Tonight's
  actual failure mode was an 11-hour SILENT hang with zero output, not a
  clean error - if a run seems to be taking unusually long, treat that
  as the same signal, not bad luck.

**2. Always wrap the real batch run in a hard external timeout** - this
project has now hit one genuine multi-hour silent hang from an
unbounded network call somewhere in the yfinance path, cause not fully
diagnosed. Never run it bare:
```
timeout 1800 python scripts/29_universe_membership.py --candidates data/mappings/wp7_candidates.csv
```

**3. After it finishes, verify against the DB directly - never trust a
single run's own printed count** (`save_rows()` is upsert-only and
never deletes, so a company can be genuinely qualified today even if
the run that would have found it again failed):
```sql
SELECT name, country, ticker, inclusion_rule, as_of
FROM universe_membership
ORDER BY as_of DESC, country, name;
```

**4. The 16 still-unresolved large-caps as of tonight** (all obviously
> €2bn, all blocked purely on GLEIF LEI-candidate disambiguation, not
market cap): Alstom, Safran, Thales, BNP Paribas, Schneider Electric,
Mediobanca, Eni, Banco BPM, FinecoBank, Unilever, RELX, Svenska
Handelsbanken, KBC Group, Cofinimmo, plus whichever of Renault/Assa
Abloy/Carrefour didn't stick depending which run you're comparing
against. None of the three retry fixes shipped tonight (timeout, 5xx,
yfinance-validation) target this specific failure mode - it's
`resolve_lei_candidates()`'s own 10-candidate cap or GLEIF's own name
matching, not a network reliability problem. Worth its own investigation
pass, separate from tonight's fixes - don't assume it's already solved
just because the retry fixes are in.

**5. Real work that does NOT need OpenFIGI/GLEIF/yfinance at all, if
tonight's flakiness recurs and you want to keep moving instead of
waiting:**
- Extend `scripts/30_national_index_membership.py` to the three
  remaining Nordic indices (OMX Copenhagen 25, OMX Helsinki 25, Oslo
  Børs OBX) - verify each Wikipedia page live first (table shape can
  differ, as BEL 20 already proved), same pattern as the 5 already done.
- WP4's `TICKER_MAP` retirement - still incomplete, not blocking, but a
  real follow-up (`19_valuation.py`/`22_dcf.py`/`23_market_risk.py` all
  still prefer the hardcoded dict over the DB-resolved ticker).

**6. The actual next milestone after universe_membership is populated
(not started yet, the real point of all of this):** pick a batch of
qualifying companies, download their real filings
(`00_find_filing.py`/`14_scan_universe.py`, already built), run them
through the classification pipeline (`13_batch_prep.py`, now with ESEF
anchoring + materiality screening from WP4b), review/apply the
remaining genuinely-ambiguous tags by hand, then actually load them into
`company`/`fact_value` - this is the step that makes a new company
appear on the live public dashboard. Nothing done tonight does this yet
- discovery and classification are prerequisites, not the finish line.

### 2026-09-17 follow-up: the smoke test still failed, and a real mystery, not fully solved

Ran the checklist above exactly as written the next morning. **Carrefour
still failed, with the identical signature, after a full overnight
cooldown** - ruling out simple rate-limit exhaustion as the sole cause
(a real rate-limit would have reset by then).

**What was tried and found, in order:**
1. Confirmed live: Carrefour's real LEI candidate ("CARREFOUR",
   `549300B8P6MUJ1YWTS08`) is still found first, unchanged. Its real
   equity ISIN (`FR0000120172`) is still in its GLEIF ISIN list, at
   index 34 of 41 - well inside the checked range (cap 60).
2. Confirmed live: querying OpenFIGI directly for that exact ISIN,
   right now, correctly returns `CA FP Equity Common Stock` (the right
   answer) at the top of the result. The data and the classification
   are both fine.
3. Confirmed live: calling `resolve_company("Carrefour", "France")`
   standalone, in a fresh process, succeeded cleanly in ~38s - same
   inputs, same code, same correct result (`CA.PA`) every prior
   successful run has produced.
4. **Added a company-level retry** (`resolve_company_with_retry()` in
   `29_universe_membership.py`, 3 attempts) on the reasoning that if the
   whole pipeline can succeed on a fresh attempt, retrying it a few
   times should be cheap insurance against whatever's transient. 3 new
   tests, full suite 234/234.
5. **Re-ran live with the fix - Carrefour failed all 3 retry attempts,
   back-to-back, inside the SAME process, in ~3m46s.** This is the part
   that doesn't fit a simple "transient/intermittent" story: three
   fresh attempts, all identical, all failed, immediately after a
   standalone call with identical code had just succeeded. A connection-
   reuse theory (a load-balanced backend pinning to a stale replica) was
   considered and set aside - this codebase's bare `requests.get()`/
   `.post()` calls (no persistent `Session` object) create a fresh
   connection per call already, so that specific mechanism doesn't fit
   either. **Root cause not identified. Recorded honestly as unsolved,
   not swept under a retry that only sometimes works.**

**Why this isn't actually urgent, and shouldn't consume more time right
now:** `universe_membership` already has a valid Carrefour row from
2026-09-13/14/16 (`CA.PA`, `market_cap_gt_2bn`) - confirmed by direct
query. The table's own upsert-only, cross-`as_of`-date design means
today's failed re-verification doesn't remove or invalidate that -
Carrefour is not missing from the real data, it's just that today's
fresh confirmation attempt happens to be flaky. This is a re-
verification consistency puzzle, not a data-loss risk. The company-
level retry fix is kept - it's a real, tested, generally sound
improvement for other companies experiencing genuine transient
failures - but it should not be assumed to have "fixed Carrefour," and
whoever revisits this should not spend another multi-hour session on
this one company without new evidence pointing somewhere specific.

### 2026-09-17, later the same day: full batch re-run, retry count tuned 3→2, real numbers

First attempt at the real 40-candidate batch (with the 3-attempt company-
level retry from the morning's fix) was wrapped in `timeout 3600` (1h) and
**was killed without finishing** - not a crash, a genuine timeout. Root
cause found, not guessed: ~14-16 companies are *persistently* UNRESOLVED
(confirmed separately - Carrefour itself failed all 3/3 attempts, back-to-
back, in the morning's run), so every one of them paid the full 3x retry
cost for zero chance of succeeding. That's the dominant cost in the batch,
not the successful resolutions.

**Fix:** `max_attempts` dropped from 3 to 2 in `resolve_company_with_retry()`
(scripts/29_universe_membership.py) - still gives a genuinely transient
failure one real retry chance, at ~2/3 the time cost across every
persistently-unresolved company. Shipped as PR #19, merged into `main`.

**Re-ran with `timeout 5400` (90 min) - completed this time (`EXIT=0`).**
Verified against the live DB directly (never trust a single run's own
printed count, per this same section's own rule above):

```
Rows for as_of=2026-09-17 (today's run):        21/40 qualify
Distinct companies ever qualified (all dates):  161
  by rule: market_cap_gt_2bn  24
           national_index    139
```

161 is up from the 160 recorded the night before (2026-09-16) - one net
new distinct company found today via the market-cap rule. Carrefour failed
again this run (2/2 attempts) - fully consistent with the still-unsolved
mystery above, and still not a data-loss concern: its valid 2026-09-13/14/16
row stands untouched under the upsert-only, never-delete design.

**Status: WP7 Step 1a (market cap) and Step 1b (5 of 8 national indices)
are both done and DB-verified.** Remaining before Step 1 as a whole is
"done": the 3 Nordic indices (Copenhagen, Helsinki, Oslo) per item 5 of the
checklist above. The actual next milestone - loading real filings for these
161 companies into `company`/`fact_value` so they appear on the live
dashboard - has not been started.

### 2026-09-19 correction: "161" was 156 - dedup bug found and fixed

Looking at the full list showed the 161 figure double-counted companies.
`30_national_index_membership.py` deduped against the market-cap rows by
*name* and minted `NO_LEI:{name}` identities, so the same company under
two spellings (Wikipedia vs GLEIF) counted twice. Confirmed 5 pairs by
shared ticker (AB InBev/Anheuser-Busch InBev, Assa Abloy/Assa Abloy B,
D'Ieteren/D'Ieteren Group, Melexis/Melexis [nl], Hermès/Hermes
International) plus 2 NO_LEI rows (Eni, Thales) that shadowed a real-LEI
row on another `as_of`. Also scrape artifacts in names ("[nl]" suffix,
non-breaking spaces). ("Herm�s" seen in a console was cp1252 display
only; the DB value is correct.)

**Fix:** `save_rows()` now dedups by ticker and reuses a known real LEI
+ name instead of minting NO_LEI; `clean_company_name()` strips scrape
artifacts; `--repair dry-run|apply` repaired the 9 legacy rows (5 deleted,
2 re-pointed, 2 renamed). **Verified: distinct by ticker = by entity = by
name = 156, zero shared tickers.** Known remaining limit: different share
classes of one issuer with different tickers (SKF-A market-cap row vs
SKF-B index row) still count as two - issuer-level grouping is not done.

### 2026-09-19: WP7 Step 2 - first filing batch (5 companies), half-integrated, plus a cohesion audit

**What was done.** Heineken, Schneider Electric, Adyen, ASM International and
Recordati (filings already in `data/raw/gate40/`) went through
`13_batch_prep.py`: 139 extension tags, **121 (87.1%) handled without a human**
(110 auto-classified incl. anchoring, 11 screened immaterial - audit trail in
the new `data/mappings/IMMATERIAL_extensions.yaml`), 18 reviewed. Those 18 were
*proposed by the AI assistant and approved by the user* (not independently
derived): company-defined subtotals/KPIs and share-count movements -> `other`
(Schneider EBITA/AdjustedEBITA, Heineken FreeOperatingCashFlow, ASM/Schneider
share counts, IFRS impairment *note* tag - a note figure in the income statement
could double-count); four genuine face-statement lines -> their statement
(Heineken RevenueLessExciseTax + TotalOtherExpenses, ASM FinanceIncomeCost ->
income_statement; Schneider's two NetCashUsedByInvestment* -> cash_flow). Mapping
grew 715 -> 843 entries, additive only (0 lines removed). Loaded one at a time
via `09_batch_load.py --raw-dir data/raw/gate40 --only <stem>`, **never**
`--reset-facts`: 231 / 253 / 204 / 206 / 221 facts stored.

**Verified.** Re-loading Recordati added 0 rows (221 -> 221; the loader IS
idempotent - its own docstring saying otherwise is stale). "Loaded N" prints
insert *attempts*, not rows (Heineken: 435 parsed -> 283 attempted -> 231 stored;
the 43 collapsed groups all held identical values). `08_validate.py`: identity
checks pass 4/4 for Heineken, Schneider, ASM; 6/6 Recordati; **Adyen FAILS
Gross Profit = Revenue - CoS in both years** (2024: 1,996M stored vs 2,136M
expected) - a real mapping/interpretation problem, **not yet investigated**.
Dashboard: all 5 appear automatically ("16 of 16 companies"); the weekly
`--mode analyze` survives them (forecast skips "only 2 year(s), need >= 3").

**NOT integrated (the honest state).** No ticker/ISIN/LEI/`sector_std`/FYE for
any of the 5 -> 0 rows in valuation, dcf_valuation, market_risk, credit_profile,
three_statement_projection, forecast, backtest. Dashboard shows them with
Sector "None" (excluded by any sector filter) and blank valuation columns. Two
live-DB tests now fail *because they hard-code the 11-company universe*:
`test_screener::test_no_unexpected_nulls...` and
`test_forensics::...documented_flag_count` (62 -> 69 flags). CI is green only
because those 34 live-DB tests skip there (206 pass / 34 skip with no DB).
Note: running that forensics test **writes to the live `forensics_flag` table**.

**Audit findings, each verified by a command (the three HIGH items below were
fixed on 2026-09-19 - PRs #22, #23, #24 - the rest are open candidates; severity
is my judgement):**
- HIGH - **FIXED (PR #22).** `11_ratio_engine.py` pivoted with
  `aggfunc="first"` over a query with no `ORDER BY`; 223 (company, concept, period)
  keys held *different* values across filings, many exact sign flips (Essity D&A
  -7,671M vs +7,671M). Measured: shuffling the input rows moved up to **47** ratio
  cells on the old code, **0** on the new. `resolve_fact_conflicts()` now chooses:
  real value > NaN; the year's representative period (~365-day duration / latest
  instant); the latest filing (its year read from its own facts, not its filename);
  then filing_id. All 232 contested facts (96 restated, 73 other_period, 36
  rounding, 27 sign_flip) are reported, never silent. **Applied to the live DB on
  2026-09-19** (39 of 984 ratio values changed, verified by a before/after diff;
  the 19 frozen baseline rows now match live again): Recordati net debt 2021 -9.1% / 2022 -3.1% (its mis-dated opening cash had been
  picked over the year-end balance), EssilorLuxottica 2021 now on its restated
  basis (operating margin 11.74% -> 11.64%), Kering 2021 < 0.4%. The 19 affected
  frozen `tests/baseline/ratio.csv` rows were re-baselined explicitly. 13 new
  tests, every rule mutation-tested. Re-running 19/21/22/24 picks the same
  corrections up (their frozen baselines may need the same explicit re-baseline).
  Still open: D&A and CFO are not abs()'d, so a wrong sign in a company's ONLY
  filing would still flip EBITDA / cash conversion.
- HIGH - **FIXED (PR #23).** `00_find_filing.py`, `14_scan_universe.py` and
  `download_historical.py` picked "latest" by the archive's `period_end`, which
  has typos: Recordati lists a FY2022 package as `2032-12-31` (ranked first -> we
  downloaded FY2022, not FY2025 added 2026-04-07); Carrefour/Hermes carry
  2026-12-31, Melexis 2025-12-31 on a report published April 2025. A live survey
  of 23 entities found 4 (17%) with an impossible label. Now: a declared period is
  trusted only if it has ended and precedes the publication date; otherwise it is
  recovered ONLY from a possible date in the package filename, else reported as
  unknown (never guessed); an impossible-label filing published after the newest
  trustworthy one is chosen but flagged; same-period duplicates are ranked by the
  archive's own validation counts. 25 tests built from the real archive records,
  8 rules mutation-tested. **Recordati's DB data is still FY2022** - the fix picks
  the right file; re-downloading and loading FY2025 is a separate step.
- HIGH - **FIXED (PR #24).** No financial-sector gating: Adyen showed DIO 275d /
  DPO 1,018d / CCC -713d / ROIC -15.9%. `gate_financial_ratios()` blanks 8 ratios
  (gross margin, cash conversion, DSO/DIO/DPO/CCC, ROIC, net-debt/EBIT) for a
  company whose `sector_std` is 'Financial Services' OR that declares
  `reporting_model: financial` in `companies.yaml` (yfinance labels Adyen
  'Technology'); operating/net margin, tax rate and ROE are kept. The reason is
  stored in a new additive `ratio.note` column and shown on the dashboard.
  Applied live 2026-09-19: only Adyen changed (16 values blanked, 24 note rows).
  Limits: sector gating cannot fire for a company with NULL `sector_std`
  (`populate_sector_std` only fills the hard-coded `TICKER_MAP` companies);
  19/21/22/24 don't gate; ~20% of the universe by name (my rough read) are
  banks/insurers/holdings.
- HIGH - **FIXED (PR #25, merged 2026-09-20), found while applying the above.**
  `15_forensics.save_to_db` deleted old flags only for companies present in the NEW
  flag list, so a company whose flags all stop triggering keeps them (Adyen: 3
  stale flags incl. one HIGH). Also `test_rerunning_save_to_db_is_idempotent_not_
  additive` re-saved `compute_flags(wide)` alone into the LIVE table, leaving it 13
  flags short (revenue flags + Pernod warning) after every local test run - I
  degraded the live table this way during today's work and restored it with
  `15_forensics.py` (66 flags). Related, not fixed (measured tiny):
  `fetch_revenue_growth` uses the same un-ordered `groupby().first()`; shuffling
  ties moves only 2 Essity growth cells.
- MED - 73 XBRL tags are mapped to two concept keys (`_x` suffix, from
  `12_apply_review.py` not de-duping by tag); pre-existing (642 distinct tags in
  HEAD). Facts are stored consistently under the `_x` name (no split found), but
  DB `concept_mapping` disagrees with the YAML for those 73 and nothing reads it.
- MED - provenance: 0 of 53 filings have `source_url`; `source_file` is a
  Windows path; the planned "discard zip, keep URL+SHA256" is not yet possible.
- MED - adding a company touches `companies.yaml` + `TICKER_MAP` +
  `download_historical.COMPANIES` + `load_historical.COMPANY_MAP` (11/11/10
  entries); `04_create_schema.py` applies only `schema.sql` (migrations 001-003
  are not applied by any script); valuation/DCF/etc. (19-25) are not in
  `run_pipeline.py`; `universe_membership.company_id` is NULL for 217/217 rows.
- MED - `keep_dashboard_awake.yml` reports success but its curl gets `HTTP 303`
  (no `-L`); the public app was asleep when checked.
- LOW - doc drift: README (11 companies / 13,997 facts / 62 flags / "714 tags" /
  "192 tests" and "87 tests"), CLAUDE.md ("no test touches the live DB", "Two CI
  workflows" - there are three; ROADMAP "single source of truth" while PLAN.md
  holds all WP status), ROADMAP Phase 10 not marked done, "642-tag" label.
- Coverage of the universe in the free archive: **136/156 found by name/LEI
  search - an estimate with errors both ways** (false positives seen: Generali ->
  Banca Generali, SCA -> Scandi Standard; false negatives: L'Oreal, Ferrari);
  the 24 companies with a real LEI matched 24/24. 742 filing entries for the 136.

**Required infrastructure changes at this scale:**
- **Storage:** change `load_historical.py` to download → parse → **discard the
  zip**, storing the source URL + SHA256 for provenance instead. Otherwise
  ~2,700 filings × ~20MB ≈ 54GB locally. filings.xbrl.org is a stable public
  archive, so provenance survives without the file.
- **Incremental loading:** skip filings already in the DB. (The old "~45s/filing,
  30+ hours" figure here was a guess; measured since: GATE parse = 7.1s/filing,
  and on 2026-09-19 the DB *write* measured ~84 ms/fact = ~19s per 231-fact
  filing, so writes, not Arelle, dominate - one filing sample, see the
  2026-09-19 audit section above.)
- **Retire `data/companies.yaml`** as the universe definition. 300 hand-written
  entries is not viable; it becomes the `universe_membership` table.
- **Sector-relative forensics thresholds.** Current thresholds are fixed and
  sector-agnostic, sanity-checked against 11 companies. At 300 you can finally
  compute real sector medians — this becomes both possible and necessary.

**Risk:** high, which is exactly why the gate exists. **Effort:** 3+ sessions
for step 1 alone.

---

# WP8 — Sector comparison view

Only meaningful once WP3a (real sector taxonomy) and WP7 step 1 (enough
companies per sector) are both done. Peer distributions across ~40 companies
in 8–10 real sectors, benchmarked against sector medians.

This is the Asset Management deliverable. Do not build it on 11 companies in
9 free-text sectors — it would be a chart of noise.

---

# Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| WP1 backfill mismatches a name, silently NULLs a row | Medium | High | Explicit null-guard halts the migration; [`tests/test_baseline_regression.py`](tests/test_baseline_regression.py) snapshot-compares before/after |
| Ticker resolution rate is poor for Nordic small caps | Medium | High | Measured at the gate before committing; unresolved = explicit NULL, never a guess |
| Extension review cost makes 300 infeasible | Medium | High | That is precisely what the gate measures; fallback is a smaller, deeper universe (SCOPE.md option D) |
| yfinance rate-limits or breaks at 300 companies | Medium | Medium | Cache aggressively; it is already a known single point of failure for all market data |
| Scope creep into UX polish before the data model is right | High | Medium | UX is explicitly deferred; WP5 is information architecture only |
| Job search makes all of this moot for a while | Medium | Low | WP6 (one-pager) is deliberately placed before breadth for this reason |

---

# Sequence summary

```
WP0 safety net          0.5 session   ← DONE
WP1 company_id          1.5           ← DONE
WP2 forensics persist   1.0           ← DONE
WP3 company columns     1.0           ← DONE
WP4 entity resolution   2.0           ← DONE (7/11 real coverage, TICKER_MAP kept)
WP5 thin screener       1.0           ← DONE
WP6 Phase 10 one-pager  2.0           ← DONE - priority #1 payoff, portfolio artifact
--- GATE: measure 3 numbers ---            ← MEASURED, classification failed at 36.5%
WP4b ESEF anchoring +   1.5           ← DONE - 82.4% handled without a human, gate PASSES
  materiality screen
WP7 breadth staged      3.0+
WP8 sector comparison   2.0
```

WP0–WP5 are all prerequisites that serve **both** goals, so nothing in them is
wasted whichever way priority tips later. WP6 is the job-search payoff. Only
WP7–WP8 are the Asset Management bet, and they sit behind a measurement gate.

**Immediate next action: WP7 itself** (WP0-WP6 are done, the GATE is
measured, and WP4b — anchoring plus the materiality screen — closed the
classification gap for free: 82.4% of tags now handled without a human,
above the gate's own ~80% bar. The classification blocker that stopped
WP7 is resolved. WP4's `TICKER_MAP` retirement is still incomplete at
7/11 real coverage and should be revisited before/alongside WP7's staged
breadth, not blocking anything that came before it). WP6 (the one-pager) was the
last item that serves the Contrôleur de Gestion job search directly -
everything from here is the Asset Management / breadth bet, per §6's own
strategic-honesty note.

### 2026-09-20/21: fidelity rule adopted; what was applied, merged and found (supersedes the "NOT integrated" and "FIX OPEN" notes above)

**Rule of record (user):** every figure shown must MATCH the company's own published annual report; where a line's
meaning is in doubt, IFRS decides. Enforced by `scripts/reconcile_reports.py` (compares each printed statement line
of the ESEF report with what is stored; states OK / DIFFERENT / MISSING / BAD_PERIOD / NOT_LOADED / NO_STATEMENT).
Measured: loaded companies 6,052 lines ~99.9% match; the 5 companies given history 2,386 lines 94.1%. The stored
statement figures are faithful; the errors were in what the ENGINE reads.

**Done and live:**
- Depth: Heineken 2020-25, Schneider 2019-25, ASM 2019-25, Adyen 2019-25, Recordati 2020-25 (23 filings, sha256-verified
  downloads, additive loads, snapshot-compared: 0 rows removed/changed anywhere). Forecasts 9 -> 14 of 16 companies.
  Puig: the archive holds exactly one filing. Pernod Ricard: documented exclusion (June year end).
- Net debt = the financial-liability lines the company prints (IAS 1.54(m)) INCLUDING IFRS 16 leases, blank unless
  both the non-current and current side are stored; the "total non-current liabilities" fallback is gone
  (8 of 16 companies had used it). Recordati matches hand calculation from its printed balance sheets 6/6 years.
- Universe linked to companies (13 entities), tickers/LEI filled for 8, sector_std for the 5 newer companies.
- Financial companies (Adyen) are skipped in forensics, credit and valuation (same classification as the ratio gating).
- Dashboard: EBITDA multiples/band/trend shown as `n/a*` where D&A is not printed separately (L'Oreal, Schneider,
  Recordati, EssilorLuxottica, LVMH, Kering; Essity in part) because "EBITDA" there is EBIT. LVMH check: net financial
  debt as reported 9,228 (its definition, excludes leases) vs 31,143 here (+17,832 leases, +3,956 financial
  investments not netted, +127 derivatives); every stored input equals the printed figure.
- All earlier audit PRs (#25-#45) merged; `main` CI green.

**Known limits / still open:**
- HIGH-ish: no clean EBITDA for six companies (D&A only in notes as dimensional facts, or bundled with provisions).
- ASM: no debt line stored -> net debt blank; needs a reviewed zero-debt override with evidence, not an assumption.
- Statement hierarchy (parent/child) is not stored: whether a printed subtotal contains or sits beside its detail
  cannot be decided from numbers, so the debt rule only ever understates (never double counts). Storing the
  presentation tree per filing at load time is the proper fix.
- One-off concepts created by classification (`finanziamenti_*`, Danone's `dan:` lines under `_x` keys) are invisible
  to the engine; the general fix is to read each company's own ESEF anchors at load time. `12_apply_review.py` still
  does not de-duplicate by tag (73 duplicates).
- `19_valuation` still uses a hand-verified `TICKER_MAP` first; forecast save only upserts, so extending a history
  leaves stale rows (cleaned by hand for Recordati); Heineken has no 3-statement/DCF (costs by nature).
- (Audited 2026-09-21 - see the next section.)

### 2026-09-21: inventory / payables / D&A / capex audited against the reports (PRs #47-#59, applied to the live DB)

**How:** for every company-year, the concept the engine reads for these four inputs was matched to the value AND the row
label the company prints in its own ESEF report (`report_check` on the inline-XBRL, `reconcile_reports.py`, the anchors
in each definition linkbase). Stored figures were faithful (every chosen value equals a printed line); the errors were
in WHICH line the engine read, in labelling, and in mapping.

**Found and fixed (one PR each):**
- **Inventory:** correct for all 16 companies (42 of 85 company-years have a report that covers them; the rest are
  comparatives). Essity 2022 (28,888) looked odd and is exactly what is printed.
- **Puig payables (#53):** the filer swapped two tags - trade payables ("Proveedores y acreedores" 229.5M) sit under
  `CurrentTaxLiabilitiesCurrent`, the income-tax payable (47.6M) under `TradeAndOtherCurrentPayables`. DPO 14 -> 70 days
  (live), CCC 248 -> 192. New reviewed per-company override file `data/mappings/company_tag_overrides.yaml` (printed
  label + evidence mandatory; the loader refuses an entry without them; applies to that company only).
- **Capex (#49, #50):** the model read the first of five concepts, i.e. PP&E only where intangibles are printed on a
  separate line (Schneider 1,072 vs 1,543 printed; Amplifon, ASM, Recordati, Heineken), and fell back to a flat 3% of
  revenue where the combined line sits under an extension tag (EssilorLuxottica 795 vs 1,522; Kering 587 vs 2,611;
  L'Oreal, Pernod). `resolve_capex()` reads a combined line else adds every printed component; the extension tags now
  map to one canonical concept through the filers' own anchors. LVMH's "operating investments" (5,531M) was first left
  out because its anchors include disposal proceeds (a net line); after checking note 15.3 it is now read as capex through
  a reviewed LVMH-only override (#61): the note gives the gross IAS 7.16(a) payments for PP&E and intangibles (5,519 /
  7,536 / 4,948M for 2024 / 2023 / 2022) and the company line differs by +0.2% / -0.8% / +0.4% (disposals and deposits
  are tiny). Company-defined, within 1% of the gross measure; the note carries no XBRL tags so the gross figure itself
  cannot be read from the facts. LVMH's 3-statement capex 2,540M (3% fallback) -> 5,531M; its D&A is still the flagged
  fallback (= capex), so the DCF level barely moves (EV 477.1bn -> 476.4bn).
- **D&A (#47, #51, #52):** Essity 2019 was stored negative (EBITDA ~SEK 15bn too low, credit row 7.2x -> 2.25x live);
  the depreciation + amortisation pair of Recordati is now the D&A; Schneider, EssilorLuxottica and ASM print D&A under
  extension tags (anchored to the IFRS D&A / impairment elements) that are now mapped. Kering, LVMH, L'Oreal bundle D&A
  with provisions on the face of the statement - no clean D&A exists, they stay `n/a*` (Essity 2023-24 prints none).
  New `_da_basis` records which lines built each D&A. The perimeter differs by company: some lines include
  impairment (Danone, Heineken, Shell, EssilorLuxottica, ASM, Amplifon - whose own printed EBITDA 511.6 equals ours),
  Moncler / Puig / Pernod are D&A only.
- **Fiscal-year label (#48):** durations were labelled by START year, instants by END year; Pernod Ricard (FYE 30 June)
  paired FY2025 flows with FY2024 balances. One rule in `11_ratio_engine.fiscal_year_label` (year the period ends);
  only Pernod changes (43 ratio values), the other 15 companies are identical.
- **DPO / CCC basis (#54):** the IFRS "trade and other payables" element holds trade payables alone for LVMH / Kering /
  Moncler and a broader line for Heineken, Shell, Schneider (Adyen): recorded in `ratio.source_concepts` and captioned
  on the dashboard as "may not be comparable".
- **Keep-alive (#55):** the HTTP check could not see the sleep page (the app is drawn in an iframe by JavaScript) and was
  green while the app was asleep; a real headless browser now wakes and verifies it (`scripts/wake_dashboard.py`; the
  awake path ran green on the GitHub runner; the wake-button path is unit-tested only, it needs a sleeping app).
- **Tooling:** `scripts/32_remap_facts.py` re-points already-loaded facts to the concept the mapping now assigns
  (dry-run by default, clash-aware, swap-aware); forensics no longer crashes on a run with no flags (#56, #57); the live
  forensics test no longer asserts a hard-coded 62 flags (#59).

**Applied to the live DB** (snapshot before/after; 76 facts re-pointed and proven `concept_id`-only; 8 Recordati
duplicates left in place): ratios recomputed; forensics / valuation / 3-statement / DCF / credit refreshed for the
affected companies; Pernod's rows under the old year label removed. Untouched companies: identical in every table except
valuation (market data and peer medians). Golden files re-baselined (#58). Screener: EssilorLuxottica, Recordati and
Schneider now show EV/EBITDA and net debt/EBITDA (11.6x/1.7x, 14.6x/2.3x, 21.5x/1.6x); `n/a*` remains for Kering,
L'Oreal, LVMH and Essity (D&A not separable).

**Judgement calls a reviewer should look at:**
- EssilorLuxottica's DCF EV moved 120bn -> 211bn: capex and D&A are now the printed 1,522M / 3,098M instead of both
  being the 3% fallback, so D&A (incl. right-of-use depreciation and impairment) exceeds capex by ~1.6bn a year, growing
  at the 16.4% historical CAGR. The inputs are the printed ones; the growth assumption (CAGR including acquisitions)
  is what makes the level implausible against a 76bn market EV. The DCF stays illustrative.
- Recordati capex 2023/2024 (383M / 851M) come from its printed intangible-purchase line (product-rights deals);
  compared with the printed statements only for FY2022.

**Still open:** (ASM zero-debt: done, see the last section; D&A for Kering / LVMH / L'Oreal / Essity: done);
statement hierarchy not stored; one-off `_x` concepts (70 tags still mapped twice) and 6 stale `concept_mapping` rows;
`19_valuation --company` overwrites peer medians with the single company's own (run it for the whole universe);
Pernod forecast (2 years of history); Heineken / Amplifon / Shell have no 3-statement/DCF (costs by nature).

### 2026-09-21 (later): D&A for LVMH, L'Oreal, Essity and Kering (PR #63, applied to the live DB)

**Problem:** these four had no clean D&A in XBRL (LVMH / L'Oreal tag only lines bundled with provisions; Essity's FY2023-24
cash-flow line is untagged; Kering's line is anchored to "provisions"), so EBITDA was EBIT and EV/EBITDA, net debt/EBITDA,
the credit band and the DCF's D&A were `n/a*` or a flagged fallback. The figures are printed in the reports, in NOTES that an
ESEF report only block-tags, so they were never facts.

**Solution (deterministic, inspectable):** `scripts/33_load_note_facts.py` + the reviewed specification
`data/mappings/reviewed_note_figures.yaml`. The spec says where to read (report, row regex, how the years are identified,
concept, perimeter, evidence); the CODE reads the number from the report's own table and REFUSES it unless its checks pass
(cross-check against a tagged fact of the same report or the company's own EBITDA bridge; plausibility vs revenue). Stored
as `fact_value` with `raw_xbrl_tag = "note:<id>"`, `context_ref = "<report>#row<n>"`; never overwrites an XBRL fact.
- LVMH: segment note "Charges d'amortissement et de depreciation" 6,702 / 6,018 / 5,772 / 5,253 (2024-2021); its IFRS 16 row
  equals the tagged right-of-use line (3,228 / 3,031 / 3,007), which proves the year mapping (2021 by table order).
- L'Oreal: fixed-assets note 1,652.4 / 1,586.7 / 1,429.7 / 1,474.2 (2025-2022), D&A only (impairment is a separate line).
- Essity: cash-flow line 7,505 / 7,998 / 9,012 SEK (2024-2022), equal to EBITDA - operating profit in its own bridge
  (25,800 - 18,295). 2022 now follows the restated FY2024 report, consistent with the restated EBIT.
- Kering: its TAGGED line 1,823 / 1,666 is what its own EBITDA adds back (4,746 + 1,823 = 6,569): a reviewed override.
Result on the live DB: 15 note facts stored + 8 Kering facts re-pointed; no existing fact changed except `concept_id`; the 11
other companies identical in every table except valuation (market data). No company is on the D&A fallback any more (ASM and
Adyen have no net debt/EBITDA for other reasons). Screener: LVMH 9.0x / 1.2x, L'Oreal 20.6x / 0.2x, Kering 6.5x / 2.1x,
Essity 8.4x / 1.4x. Essity EBITDA 2023 / 2024 (23,146 / 25,800) equals the company's own; Kering 6,466 vs its 6,569 (ours starts
from operating profit after non-current items, its from recurring operating income).

**Perimeters (stated in the spec):** LVMH / Essity / Kering include impairment, L'Oreal does not; LVMH's line excludes the
brand and goodwill impairment booked in "autres produits et charges operationnels" (422M in 2024), so its EBITDA is slightly
conservative. Not covered by any report on this machine: L'Oreal 2019-2021 and LVMH 2020 (their D&A stays the flagged fallback).

**DCF effect (judgement call):** with real D&A the DCF moved: LVMH EV 476bn -> 529bn (D&A 6.7bn vs capex 5.5bn), Kering
127bn -> 93bn (2023 capex 2.6bn includes real-estate purchases, above D&A 1.8bn), L'Oreal 144 -> 148bn, Essity 42.8 -> 43.2bn.
The inputs are the printed ones; the levels are still driven by the growth assumption (historical CAGR incl. acquisitions:
LVMH 17.4%), so LVMH's DCF stays far above its ~232bn market EV. The DCF is illustrative.

### 2026-09-21 (later still): ASM International - reviewed zero-debt evidence and lease liabilities (PR #65, applied to the live DB)

**Problem:** ASM prints no borrowing line, so the net-debt rule (blank unless both sides are stored: "never a guess") left net
debt, net debt/EBITDA, the credit band, the valuation EV and the DCF blank, although the company says plainly that it has no debt.

**Evidence, read from ASM's FY2025 report:** "As per December 31, 2025, ASM was debt-free"; the EUR 150M revolving facility
"amount outstanding ... was nil" (and the EUR 15M overdraft line); note 18 "the company had no debt"; cash 1,026.9M. The balance
sheet, the five-year summary (2021-2025) and the financing cash flows (2025, 2024) have no borrowing line or flow. The only
financial-debt items are IFRS 16 lease liabilities: current 13.9 / 11.7M (note 15) and non-current 19.6 / 25.0M (note 18
maturity table), each proved by a neighbouring total that equals a TAGGED balance-sheet line. Contingent consideration for
acquisitions is a payable, not borrowing, and is not counted.

**Tool:** `33_load_note_facts.py` now handles balances at a year end (instants), a per-year column cross-check, `min_values`,
and a `stated_zero` kind (stored only while the report still contains the stating sentence and its balance sheet still has no
line of that nature). Result: net cash 993.4M (2025) / 890.3M (2024); net debt/EBITDA -0.84x / -0.89x; EV/EBITDA 33.1x; a DCF now
exists (EV 9.2bn vs a market EV of about 30bn+, driven by the 16.3% historical CAGR at a 9.8% all-equity WACC: illustrative).

**Limits:** 2024's zero rests on the statements' structure (the FY2025 report has no sentence about 31 Dec 2024); only
2024-2025 are covered (ASM's older reports are not on this machine - downloading them needs a go-ahead); the non-current lease
figure comes from a maturity table, so it can be slightly above the carrying amount (about 3% of cash, immaterial). ASM's ROIC
and ROE stay blank: it prints only total equity, not equity attributable to owners (the same tool can carry a reviewed
"no non-controlling interests" statement if wanted).

### 2026-09-22: LVMH's company-defined capex now labelled on the dashboard (PR #67, applied to the live DB)

**Problem:** LVMH's capex (5,531M, see the 2026-09-21 section above) is read from a per-company override
(`company_tag_overrides.yaml`) because it is the company's own net "Investissements d'exploitation" line, not the standard
combined capex tag every other filer uses. The distinction previously lived only in that YAML file's evidence and in this
document - a dashboard visitor had no way to know LVMH's 3-Statement Model tab was built on a different kind of figure than
everyone else's.

**Fix:** `resolve_capex()`'s `basis` (which concept(s) capex was read from) is now cross-checked, in `fetch_base_year()`,
against `company_tag_overrides.yaml` for a `statement: cash_flow` entry on that concept for that company - generic (any
future capex override gets the same treatment automatically), not an LVMH-only condition. When one matches, the company's
own `printed_label` from that file is carried through `three_statement_projection.capex_basis_label` (new nullable column,
one value per base-year run, same pattern as `growth_assumption`) into the webapp, which now shows a caption on the
3-Statement Model tab for that company only, in plain language: no filename or doc pointer, per
`tests/test_webapp_plain_language.py`'s guard against developer references in visitor-facing text.

**Live-DB result:** re-ran `21_three_statement_model.py --company "LVMH"`; snapshot-diffed `three_statement_projection`
before/after - 0 changes to any pre-existing column on any row (60 rows before and after), only LVMH's 5 rows gained
`capex_basis` / `capex_basis_label`; every other company's `capex_basis_label` is NULL (verified on Danone - no caption).
Confirmed live in the dashboard preview (LVMH's 3-Statement Model tab shows the caption; Danone's does not).

### 2026-09-22 (later): ASM's ROIC and ROE filled - proven-zero non-controlling interests (PR #68, applied to the live DB)

**Problem:** ROIC needs invested capital (`equity_attributable_to_owners_of_parent` + non-controlling interests + net
debt) and ROE needs that same equity-attributable-to-owners figure. ASM only tags total `Equity`
(`ifrs-full:Equity`) - it never prints the split into owners' equity and non-controlling interests - so both ratios
stayed blank even after the 2026-09-21 zero-debt fix filled net debt.

**Evidence, read from ASM's FY2025 report:** the consolidated balance sheet (note 12), the five-year summary
(2021-2025) and the statement of changes in equity all print ONE "Equity" line, with no non-controlling-interests row
or column anywhere across five years of statements. The consolidation accounting-policy note only says NCI "is
disclosed separately, where appropriate" - conditional boilerplate describing what ASM would do if a partly-owned
subsidiary existed, not a disclosure that one does. With no NCI line anywhere, ASM has none.

**Fix:** two reviewed `stated_zero` figures (`asm_no_noncontrolling_interests_2025`/`_2024` in
`reviewed_note_figures.yaml`) store a proven-zero `noncontrolling_interests` fact, checked against BOTH the
consolidated balance sheet and the five-year summary (no `report_states` sentence exists for this one, unlike the
debt fix - purely structural evidence for both years). `11_ratio_engine.py`'s ROIC/ROE now treat a PROVEN zero
(this note figure) - never a merely-missing one - as licence to use total equity as equity attributable to owners of
the parent, a plain accounting identity (total equity = owners' equity + NCI; NCI = 0 implies they're equal), not a
per-company guess or condition: any future company with the same kind of note figure gets the same treatment
automatically. The audit trail (`ratio.source_concepts`, same mechanism as DPO's `_payables_basis`) records when this
happened, and the dashboard's Ratios tab captions it in plain language.

**Live-DB result:** applied the two note figures, then re-ran `11_ratio_engine.py --company "ASM International"`.
Snapshot-diffed `ratio` and `company_latest_metrics` before/after: exactly 4 rows changed (ASM's `roic`/`roe` for
2024 and 2025, all filled from blank), 0 rows changed for any other company. ROIC 22.2% (2024) / 24.4% (2025), ROE
18.3% / 18.1%. `tests/test_screener.py`'s `EXPECTED_BLANKS` for ASM is now empty (every metric this view computes is
now real data) - Adyen remains the only deliberate-blank company (payment processor, "net cash" is merchants' money).

**Limits:** the same 2024-vs-2025 asymmetry as the debt fix - 2025 could in principle carry a stated sentence if one
existed (it doesn't for NCI either), so both years rest on structural evidence only, cross-checked against two
independent tables rather than one, which is the strongest evidence available without downloading ASM's older
reports. Any company whose non-controlling interests are simply untagged (not proven zero) stays exactly as blank as
before - this fix cannot silently fill in a real, undisclosed NCI split.

### 2026-09-22 (later still): 69 duplicate concepts removed from ifrs_concepts_v0.yaml

**Problem:** `09_batch_load.py`'s `load_mapping()` builds a plain `{xbrl_tag: concept}` dict from the mapping file -
when the SAME tag is listed under two different concept names, whichever is declared later in the file silently
overwrites the earlier one, so the earlier concept is permanently dead config: it looks live (it has a
`display_label`, it shows up in `ifrs_concept`/`concept_mapping`), but no fact has been routed there since the
duplicate was introduced. Found 69 such pairs - every one an `_x` or `_x_x`-suffixed near-duplicate of an existing
concept, all across Danone, EssilorLuxottica, Kering, L'Oreal, Pernod Ricard and Shell's extension-tag concepts,
apparently from an earlier concept-generation run that re-added tags already mapped instead of noticing they existed.

**Verification before touching anything:** for all 69 tags, confirmed live `fact_value` rows are already stored
under the concept that `load_mapping()` currently resolves to (the later-declared, "winning" one) - i.e. every load
since the duplicate was created has already been using the winner; the shadowed "loser" concept has zero live facts
under it. One exception (`kering:AdjustmentsForDepreciationAndAmortisationAndProvisionExpense`) is superseded
entirely by a reviewed company override (see the D&A section above), so neither duplicate is live for it either.

**Fix:** removed the 69 shadowed entries (never the one holding live data) from `ifrs_concepts_v0.yaml` by exact
key-block match - a line-level edit, not a full YAML re-dump, so nothing else in the file's formatting moved.
Verified `load_mapping()`'s output is byte-identical before and after (775 tags, same dict) - proof this is a pure
dead-config removal with zero effect on any future load. `scripts/32_remap_facts.py --all` still shows only the
pre-existing, unrelated Recordati borrowings drift (a real open item, not something this touched). Added a
regression test (`test_remap_facts.py::TestTheRealMappingFile::test_no_tag_is_mapped_under_two_concepts`),
mutation-tested by reintroducing a duplicate and confirming it fails.

**Not done in this pass:** the DB side. `concept_mapping` still has stale rows for the 69 now-removed concept names
(pointing tag -> the old, now-gone concept) - harmless (nothing reads `concept_mapping` for fact routing, only
`32_remap_facts.py`'s own drift-detection logic, which already keys off the current mapping file, not that table),
but a future reader could still be misled by them. [Corrected 2026-09-23: `32_remap_facts.py` does not read it for
drift detection either - it only UPDATEs the tag's row after moving facts. The only other writer is the loader, which
inserts a row for a tag that has none (one row per tag, first mapping wins). No analytics or dashboard code reads it.] Left alone pending a decision on whether to clean the live DB too
- a separate, DB-touching change from this YAML-only one.

### 2026-09-22 (later still): stale concept_mapping rows deleted from the live DB

**Problem:** the 69 duplicate concepts removed above left 69 stale `concept_mapping` rows behind (each still says a
tag maps to the now-deleted concept name). A fresh re-check of the WHOLE `concept_mapping` table (not just those 69
tags) found 75 stale rows total - the 69 from the dedup, plus 4 left over from an earlier session's extension-tag
consolidation (`kering:PurchaseOfPropertyPlantEquipmentAndIntangibleAssetsOtherThanGoodwill`,
`el:AmortissementsDepreciationsEtPertesDeValeur`, `el:AcquisitionsDimmobilisationsCorporellesEtIncorporelles`,
`pernod:AcquisitionDimmobilisationsCorporellesEtIncorporellesAutresQueLeGoodwill` - each remapped to the canonical
capex/D&A concept by `32_remap_facts.py` at the time, without updating its own `concept_mapping` row), and 2 that are
NOT stale metadata but a real, still-unresolved drift: Recordati's `Rec:FinanziamentiDovutiOltreUnAnno` /
`EntroUnAnno` still have 4 live facts each parked on the OLD concept (`finanziamenti_dovuti_oltre/entro_un_anno`)
because `32_remap_facts.py --apply` refuses to move them (`would_stay_clash`) - this is the same known open item as
PLAN.md's other Recordati borrowings mention, not something this pass created or should paper over.

**Verified before deleting anything:** for the 73 genuinely stale rows, zero `fact_value` rows use their
`concept_id` (checked across the WHOLE table, not just the one tag each row names), zero other `concept_mapping`
rows point at the same `concept_id`, and zero `ratio.source_concepts` entries mention their name - triple-checked
inside the same transaction as the delete, which asserts and aborts if any of the 73 turns out to have a live fact
after all.

**Fix:** deleted exactly those 73 `concept_mapping` rows (772 -> 699). Left the 2 Recordati rows untouched - they
have live facts, so deleting the row would just hide the drift, not fix it; the actual fix needs the `would_stay_clash`
investigated first. [Corrected 2026-09-23: deleting was the wrong fix. `concept_mapping` is the one-row-per-tag
registry, so these rows should have been re-pointed to the current concept, not removed - the delete left 72 loaded
tags with no row at all. Functionally harmless (nothing reads the table), but the registry was incomplete. Repaired
in the next section.] Left the now-mappingless `ifrs_concept` rows in place (an `ifrs_concept` row with no
`concept_mapping` row is normal for this schema - every reviewed-override and note-figure concept already works
that way).

**Verification:** snapshot-diffed `concept_mapping` before/after - removed set is byte-identical to the intended 73
mapping_ids, zero rows added, zero of the remaining 699 rows changed. `fact_value` (19,811) and `ifrs_concept` (840)
row counts unchanged. `32_remap_facts.py --all` (dry run) shows the exact same output before and after - only the
pre-existing Recordati clash, nothing new. Full test suite: 723 passed / 36 skipped (no DB), 759 passed (live DB).

### 2026-09-23: Recordati's "clash" was 8 stale duplicate facts; concept_mapping registry repaired

**What the clash really was:** `fact_value` is unique per (filing, period, CONCEPT), not per tag. When a filing is
re-loaded after a tag's mapping has changed, the loader adds a row under the new concept and never deletes the old
one. Recordati's `Rec:FinanziamentiDovutiOltreUnAnno` / `EntroUnAnno` had 8 such leftovers (filings 56 and 77, four
periods each) under the old concepts `finanziamenti_dovuti_oltre/entro_un_anno`. Each was an exact copy (same tag,
filing, period, value, currency) of a row already under `longterm_borrowings` / `shortterm_borrowings`. That copy is
what blocked `32_remap_facts.py` (`would_stay_clash`), so there was no real conflict to resolve. A scan of the whole
DB found no other case of one filing + period + tag stored under two concepts.

**Review of PR #70 (own error):** that cleanup deleted 73 stale `concept_mapping` rows instead of re-pointing them,
which left 72 loaded tags with no registry row (see the corrections above).

**Fix (one transaction, every precondition asserted again inside it, dry run shown first):** (a) deleted the 8 stale
facts; (b) re-pointed `concept_mapping` 798/800 to `longterm_borrowings` / `shortterm_borrowings`, which is what
`32_remap_facts.py` does after a move; (c) re-registered the 72 tags, each to the concept its facts are stored under
(asserted equal to the current mapping, and each tag asserted to have been in the registry before PR #70).

**Verification:** `fact_value` 19,811 -> 19,803, and the rows removed are exactly the 8 intended `value_id`s. No other
company's facts changed. `concept_mapping` 699 -> 771: +72 rows, 798/800 changed, 0 removed, no tag registered twice.
`ratio` and `company_latest_metrics` are unchanged. Recordati's ratios recomputed from the post-fix facts (read-only)
match the stored values exactly, so the deleted rows never fed any figure. Whole-DB end state: 0 drift
(`32_remap_facts.py --all`: "No drift"), 0 duplicated filing + period + tag, 0 loaded tags without a registry row, 0
registry rows disagreeing with the mapping file. New live-DB guard `tests/test_mapping_consistency_live.py` (read-only,
skipped without `DATABASE_URL`) checks those three invariants. It was mutation-checked without writing to the DB:
with an in-memory mapping where one tag is re-pointed, both mapping checks fail; the duplicate check's own SQL finds
the 8 groups in the pre-fix Recordati snapshot and 0 in the post-fix one. Tests: 723 passed / 39 skipped (no DB),
762 passed (live DB).

**Root cause still in the loader (deliberately not changed here):** re-loading after a remap will leave a stale row
again. The documented remedy is `--reset-facts`, which is off-limits on the live DB. The new live guard now catches
it; the fix is `32_remap_facts.py` BEFORE any reload, which moves the facts instead of duplicating them.
