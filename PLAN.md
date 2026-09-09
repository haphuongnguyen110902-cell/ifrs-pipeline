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

# WP1 — Migrate the five TEXT-keyed tables to `company_id`

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

---

# WP2 — Persist forensics flags

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

---

# WP3 — Extend the `company` table (one migration, three additions)

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

---

# WP4 — Entity resolution: LEI → ISIN → ticker

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

---

# WP5 — Thin screener (validate the pattern before breadth, not after)

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

---

# WP6 — Phase 10: the one-pager

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

---

# ⛔ GATE — measure before committing to breadth

Before WP7, run the ~40-company step and **record three numbers**:

1. **Extension auto-classification rate** — what % of new companies need zero
   manual review? Currently ~10 min/company manual; at 300 that is ~50 hours.
   This number decides whether 300 is reachable at all.
2. **Ticker resolution rate** from WP4 on unseen companies.
3. **Parse throughput** — filings/hour on your machine, measured.

If (1) is below ~80%, **fix classification before scaling**, do not grind
through manual review. That is what the Claude-API auto-classifier was
deferred for, and this is the moment it finally has real unmapped tags to earn
its keep.

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
WP1 company_id          1.5           ← riskiest, do it while the DB is small
WP2 forensics persist   1.0
WP3 company columns     1.0
WP4 entity resolution   2.0           ← the real wall before breadth
WP5 thin screener       1.0
WP6 Phase 10 one-pager  2.0           ← priority #1 payoff, portfolio artifact
--- GATE: measure 3 numbers ---
WP7 breadth staged      3.0+
WP8 sector comparison   2.0
```

WP0–WP5 are all prerequisites that serve **both** goals, so nothing in them is
wasted whichever way priority tips later. WP6 is the job-search payoff. Only
WP7–WP8 are the Asset Management bet, and they sit behind a measurement gate.

**Immediate next action: WP1** (WP0 is done — see above).
