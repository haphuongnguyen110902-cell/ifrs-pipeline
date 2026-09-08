# Roadmap

This file is the single source of truth for what's built, what's in
progress, and what's next. Earlier plans (the original `Detailed_roadmap`
doc, and various revisions discussed in chat) are superseded by this file
- if something here conflicts with an older doc or an old chat message,
this file wins.

**Two goals, equal priority, always** - every phase below states which
goal(s) it serves. Neither goal is worth more than the other:
- **Controlling** — job search for Contrôleur de Gestion / Financial Analyst roles
- **Dauphine** — Master Finance application, considering BOTH the
  "Banque d'investissement et de marché" track (DCF, trading comps,
  single-company valuation) AND "Gestion d'Actifs" / Asset Management
  (portfolio construction, cross-sectional screening, benchmark-relative
  performance) - phases below are tagged with which track(s) they serve
  when it's not both.

## Vision (the end state this is building toward)

A website, similar in spirit to yfinance: an end user picks a European
listed company (filterable by **country** and **sector**), sees its
financials, ratios, forensics flags, forecasts, and valuation - all
computed by a backend that runs **automatically**, not by hand per
company. This was always the intent but had never been written down in
one place until now - every phase below should be read against this end
state, not just against the two goals above.

**Why the phase order below deliberately front-loads a thin
"walking skeleton" of this website (Phase 2) instead of building all the
analytical depth first and bolting the site on at the end:** building
the full vertical slice early - even a bare-bones filterable list of the
current 11 companies - proves the underlying data model (country, sector,
company) actually supports the filtering vision, while it's still cheap
to fix if it doesn't. Discovering a schema problem after Phases 4-10 are
built would be far more expensive to fix than discovering it now.

---

## Done

### V0 — Foundations
Parse one company's ESEF/XBRL filing with Arelle, build the semantic
mapping layer, store in PostgreSQL with full provenance.
**Serves:** Controlling (direct proof point) + Dauphine (IFRS coursework overlap).

### V1 — Universe + ratio engine
11 companies, standard tag auto-classification via the IFRS presentation
linkbase, core ratio engine (margins, ROIC, ROE, cash conversion).
**Serves:** both.

### V2 — Historical depth + earnings quality + forecasting
- `load_historical.py` — 37 historical filings, 2017–2025, separate filing
  row per company/year (not overwriting V1's single filing)
- `11_ratio_engine.py` — DSO/DIO/DPO/CCC added (were computed but never
  registered for output - found and fixed), Excel sheet-name crash fixed
- `15_forensics.py` — 10 rule-based flags, including `THIN_DENOMINATOR`
  (catches ratios distorted by a near-zero Operating Profit denominator,
  found via the EssilorLuxottica 2020 case) and its knock-on fix to
  `CASH_CONVERSION_DROP`/`HIGH_LEVERAGE` severity when the prior or
  current year's baseline is itself distorted
- `16_forecasting.py` — CAGR + linear regression per company/ratio
- `17_backtest.py` — rolling-origin backtest (MAE/RMSE/BIAS/MAPE), with a
  `confidence` label so a 1-fold "winner" isn't presented the same as a
  4-fold one
- `run_pipeline.py` — `--mode analyze` / `--mode historical` orchestration
- `18_fx_convert.py` — ECB average (P&L) + closing (balance sheet) rates
  per IAS 21, auto-detects currencies from the DB instead of a hardcoded list
- Verified against independent sources where possible (EssilorLuxottica
  2020 operating margin vs published filing; L'Oréal DSO/DIO/DPO vs GuruFocus)

**Serves:** Controlling primarily (DSO/DIO/DPO/CCC, forensics = daily
controller tools) + Dauphine — and specifically, the rolling-backtest
methodology in `17_backtest.py` (comparing forecasting methods
objectively across companies/ratios rather than picking one on
intuition) is a legitimate talking point for the Asset Management track,
not just background infrastructure - it's the same "which method
actually predicts better, proven not asserted" thinking that track cares
about.

### V2.5 — Trading comps (in progress)
`19_valuation.py` — EV/EBITDA, EV/Sales, P/E in EUR, combining historical
ECB rates (for Net Debt/EBITDA/Revenue) with a live rate (for market cap)
per IAS 21. **Currently a multiples CALCULATOR, not real comps yet** - no
peer benchmarking, no sector grouping, no implied valuation. Phase 4
below closes this gap.
**Serves:** Dauphine primarily (Business valuation coursework).

### V2.6 — Sector/country metadata wired through (small, done while planning the website vision)
`sql/schema.sql` already had `country`/`sector` columns on `company` since
V0, and `data/companies.yaml` already had `sector` per company - but
`get_or_create_company()` only ever wrote `name`, so both columns had been
NULL for every company since the very first load. Fixed: `companies.yaml`
now also has `country`, `get_or_create_company()` writes both and
backfills existing rows via `COALESCE` (never overwrites a real value with
NULL). Exactly the same "data existed, was never wired through" pattern as
the DSO/DIO/DPO gap in V2 - found while reviewing the website vision's
filtering requirement, not by accident.
**Serves:** both (this is the concrete data foundation the "filter by
country/sector" vision needs).

---

## Next — in dependency order

### Phase 1 — Initial test suite (`pytest` + GitHub Actions CI)
**Do this first, before any new feature.** Every fix made so far
(THIN_DENOMINATOR, the HIGH_LEVERAGE consistency fix, the DPO Excel-crash
fix, the CASH_CONVERSION_DROP baseline-distortion downgrade...) was
verified by hand, in chat, and lives nowhere as a standing check. Turn
each into a fixed `assert` in `tests/`, run automatically on every push.
Cheapest to build now, covering V0–V2.5; gets more expensive the more
modules pile up without tests under them.

**Standing rule for every phase after this one: write that phase's tests
as part of the phase, not deferred to a batch at the end.** Batching all
testing into one late phase was considered and rejected - it would mean
Phases 2 through 10 all ship and get used for weeks with zero regression
protection, which is exactly the risk Phase 1 exists to close. Each new
script gets its test file in the same work session it's built in.
**Serves:** both equally (CI badge = credibility signal either direction).

### Phase 2 — Walking-skeleton website, deployed publicly
A minimal but REAL version of the end-state website: a simple Streamlit
app showing the current 11 companies, filterable by the `country`/`sector`
fields just wired through in V2.6, displaying whatever is already in the
DB today (ratios, forensics flags). Deliberately thin - no new analysis,
just prove the filter-and-browse experience works end to end on real
data before investing further.

**Not "runs on my machine" - deployed to Streamlit Community Cloud
(free) with a real public URL from day one.** A link a recruiter or
Dauphine admissions reader can click is categorically different from a
GitHub repo they'd have to clone and run - this is the single highest
leverage-per-hour change to how the whole project is perceived, and it
costs almost nothing once the app itself exists. Every phase after this
one (4-11) adds its output to this running app rather than staying
isolated in Excel/DB until Phase 11.
**Serves:** both - this is the product shell the whole project has been
building toward, made concrete and shareable early.

### Phase 2 — Walking-skeleton website, deployed publicly ✅ DONE
Live at: https://haphuongnguyen110902-cell-ifrs-pipeline-webappapp-iwnn9s.streamlit.app/
Deployed from `webapp/app.py` (moved from repo root so Streamlit
Community Cloud's directory-based auto-detection picks up
`webapp/requirements.txt` - a light dependency set - instead of the
repo root's `requirements.txt`, which includes the heavy `arelle`
dependency the dashboard doesn't need). Filters by country/sector,
shows ratios + forensics flags per company, recomputing forensics live
rather than reading a stored table (15_forensics.py doesn't persist to
DB - see its own docstring).

**Bugs found and fixed by actually running this in Streamlit Cloud/locally, not by reading code:**
- Assigning formatted strings into a float64-dtype DataFrame via `.loc`
  raised `LossySetitemError` - fixed by building the display frame with
  `dtype=object` from the start.
- `15_forensics.py`'s `compute_flags()` crashed with `KeyError:
  'operating_margin'` when filtered to a single company that has zero
  rows for that ratio_name anywhere in its history - pivot_table only
  creates a column when SOME row in the filtered input has that
  ratio_name, which is always true across the full 11-company universe
  but not guaranteed for one company alone. Fixed with a `safe_year_col()`
  helper used everywhere `compute_flags()` looks up a prior year's value.

### Phase 3 — Real automation (GitHub Actions cron)
NOTES.md already documents this plan in detail - execute it, don't just
plan it. `run_pipeline.py --mode full` runs on a weekly GitHub Actions
schedule against the Neon database (already cloud-hosted), with
`DATABASE_URL` as a repo secret. Moved up early, right after the
website exists, because "automated" should stop being a claim and start
being a fact as early as possible - everything built from Phase 4 onward
then inherits a genuinely self-refreshing pipeline underneath it, rather
than automation being a checkbox ticked at the very end after everything
else is already built by hand.

One thing explicitly NOT automated (per NOTES.md's own reasoning,
correct then and still correct now): classification of new extension
tags. The workflow should surface unmapped concepts and stop, never
guess - this is the one step needing human judgment.
**Serves:** both (a live "last updated: [date]" on the public site is a
concrete, checkable automation claim - not an assertion to take on faith).

### Phase 4 — Finish trading comps + precedent transactions
Sector grouping (at minimum: energy vs. consumer/luxury - Shell should
never sit in the same peer stats as Moncler), peer min/median/max per
multiple, implied valuation (peer median multiple × target's own metric),
and forward multiples (wire in `16_forecasting.py`'s revenue/margin
forecasts instead of only trailing figures).

**Also add precedent transactions** (`20_precedents.py`) - the third leg
of the DCF/comps/precedents "valuation triangle" every real valuation
presentation includes, and almost certainly part of Dauphine's Business
Valuation coursework. No paid M&A database needed: curate 3-5 real,
publicly-announced deals in the same sectors (luxury/consumer - these
get press coverage, deal multiples are usually disclosed), compute
implied EV/EBITDA and EV/Sales at announcement, compare against the
trading comps range. Manual/curated by design - this is a legitimate
professional approach when a paid database isn't available, not a
shortcut to hide.
**Serves:** Dauphine (Banque d'investissement et de marché - this IS the
standard valuation methodology, all three legs expected together).

### Phase 5 — Linked 3-statement projection model
**Before DCF, not after** - a DCF needs projected Free Cash Flow, and
"project FCF" done properly means a real linked model: Revenue growth
assumption → COGS/Opex → EBIT → Tax → Net Income → reinvestment (Capex,
ΔWorking Capital using the DSO/DIO/DPO already built) → FCF, with a debt
schedule (revolver draw/paydown, interest expense that feeds BACK into
the income statement - the "circularity" every IB technical test asks
about). This is the single most-tested IB technical skill ("build a
3-statement model") and the current pipeline doesn't have it -
`07_generate_statements.py` only reformats HISTORICAL data, it doesn't
project forward with everything linked.
**Serves:** Dauphine (Banque d'investissement et de marché) primarily -
this is the concrete deliverable that proves "I can build what they'll
test me on in an interview or in the program itself."

### Phase 6 — `21_dcf.py`
WACC build-up (CAPM: risk-free rate + beta from yfinance + equity risk
premium), FCF pulled from Phase 5's linked model (not a shortcut ratio
projection), terminal value (Gordon growth), sensitivity table (WACC ×
terminal growth grid). Cross-check DCF output against Phase 4's trading
comps and precedent transaction ranges - three methods converging (or
explaining why they don't) is the actual valuation deliverable, not any
one method alone.
**Serves:** Dauphine.

### Phase 7 — Market risk & return module (`22_market_risk.py`)
Currently the ENTIRE pipeline is fundamentals-only - zero analysis of
actual stock price behavior. Using yfinance price history (same source
already used for market cap): volatility, Sharpe ratio, max drawdown,
beta vs. a benchmark (CAC 40 / STOXX 600), rolling correlation. This is
not optional polish for the Asset Management track - it's the most
basic vocabulary of that discipline, and right now the project has none
of it despite Asset Management being an explicit goal.
**Serves:** Dauphine (Gestion d'Actifs) specifically - directly maps to
"Investissements et marchés financiers" and "Introduction à
l'économétrie de la finance" coursework.

### Phase 8 — `23_credit.py`
Net Debt/EBITDA trajectory → simple credit-profile classification.
Most of the underlying data already exists (`net_debt_ebitda_proxy` from
V2) - this is mostly a classification/trend layer on top of what's
already computed, so cost is low relative to value.
**Serves:** Dauphine (credit/markets angle) + Controlling (counterparty
risk is a real controlling concern).

### Phase 9 — `24_scenario.py`
Perturb an input (margin -2pp, SEK -10%, etc.) and recompute downstream
ratios/leverage/valuation. This is the actual day-to-day tool of FP&A
(budget variance, sensitivity analysis) - the single most
Controlling-relevant piece of the whole forward roadmap. Shares its
"perturb and recompute" core with Phase 6's DCF sensitivity table, so
build once, use in both places.
**Serves:** Controlling primarily.

### Phase 10 — Presentation output (one-pager / mini pitch deck)
Everything above lives in Excel/DB/terminal - a real IB/PE deliverable
is a document. Generate one polished PDF or PPTX per company ("Company X
- Trading Comps & DCF Summary") pulling numbers already computed by
Phases 4-9: comps table, DCF range, forensics flags, key ratios. This is
the single highest-leverage-per-hour item for INTERVIEWS specifically -
handing someone a one-pager lands very differently than "let me show you
some Excel files."
**Serves:** both (a polished deliverable is a strong artifact for either
job interviews or the Dauphine application/interview).

### Phase 11 — Breadth + dashboard
Only once Phases 4–10 give the universe real analytical depth: scale to
more companies/countries (this is where the Claude API auto-classifier,
deliberately deferred until now, finally has real unmapped tags to earn
its keep) and enrich the Phase 2 dashboard with everything built since.
**Serves:** both.

### Phase 12 — Open-source the mapping layer + write up the journey
The 642-tag XBRL→IFRS-concept mapping is a genuinely reusable artifact -
anyone doing ESEF/XBRL analysis hits the exact same "which tag means
Revenue" problem this project already solved. Publish the mapping file
with documentation as a standalone, usable-on-its-own resource (separate
from the rest of the pipeline, which is this-project-specific).

Pair it with a short written case study of the actual engineering
journey - the THIN_DENOMINATOR discovery, the DPO Excel-crash fix, the
HIGH_LEVERAGE consistency gap, the sector/country metadata that existed
in the schema but was never wired through - all of it real, all of it
already happened, all of it currently only visible buried in chat
history. `LEARNING.md` already exists as the right place to grow this
into something publishable (a blog post, a LinkedIn article, or simply a
polished doc linked from the README).

This is the "helpful for others" and "showcase skills" phase made
concrete: a recruiter or Dauphine reader who sees a real open-source
contribution plus a genuine account of debugging real problems reads
very differently from someone who only shows finished, polished output
with the messy process hidden.
**Serves:** both - initiative and communication are exactly what neither
a CV bullet nor a GitHub repo link communicates on their own.

---

## Explicitly deferred, not forgotten

- **Claude API auto-classifier** — wait for the next new company, so it
  can be built AND tested against real unmapped tags, not synthetic ones.
- **Kering DSO/CCC gap** — one XBRL tag for receivables not yet mapped,
  low priority, note only.
- **Streamlit app URL is auto-generated and long** (haphuongnguyen110902-cell-ifrs-pipeline-webappapp-iwnn9s.streamlit.app) -
  cosmetic, not urgent. Streamlit Cloud allows setting a custom subdomain
  under app Settings → General → App URL, once a short/memorable name is
  decided on. Revisit once the app has more content (Phase 4+) worth
  giving a polished URL to.
- **Sector-relative forensics thresholds** — current thresholds are fixed
  and were sanity-checked against the current 11-company dataset, but
  will need to become per-sector once the universe is large/diverse
  enough for sector medians to mean anything (bundle with Phase 11).
- **Cross-sectional screening/ranking** — a composite score across
  companies (margin trend + leverage + forensics flag count, etc.) to
  rank the universe, closer to how Asset Management actually screens a
  coverage list than single-company analysis is. Natural fit once
  Phase 9's scenario engine exists (same "recompute across
  perturbations" machinery extends to "recompute across companies") and
  before Phase 11's breadth expansion. Noted now because of the added
  Asset Management consideration, not scheduled yet.
