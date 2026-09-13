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

**Required infrastructure changes at this scale:**
- **Storage:** change `load_historical.py` to download → parse → **discard the
  zip**, storing the source URL + SHA256 for provenance instead. Otherwise
  ~2,700 filings × ~20MB ≈ 54GB locally. filings.xbrl.org is a stable public
  archive, so provenance survives without the file.
- **Incremental loading:** skip filings already in the DB. At ~45s/filing,
  2,700 filings is 30+ hours single-threaded — you cannot afford to re-parse.
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
