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

### Phase 3 — Real automation (GitHub Actions cron) ✅ DONE
`.github/workflows/pipeline.yml` runs `--mode analyze` every Monday
06:00 UTC, verified by a real successful manual trigger (not just
enabled and assumed working). Deliberately forces `analyze` mode
specifically for schedule/CI runs regardless of what `workflow_dispatch`
requests, since `data/raw/*` is gitignored - a fresh CI checkout has no
local `.zip` files, so `full`/`load`/`historical` would find nothing to
parse. `analyze` only needs database connectivity, which CI has.

One real setup gotcha hit and fixed: the GitHub Actions `DATABASE_URL`
secret is a SEPARATE value from the Streamlit Cloud secret of the same
name - and unlike Streamlit's TOML format (which requires quotes), a
GitHub Actions secret must be the raw connection string with NO
surrounding quotes. Pasting the TOML-quoted version caused
"Could not parse SQLAlchemy URL from given URL string".

One thing explicitly NOT automated (per NOTES.md's original reasoning,
correct then and still correct now): classification of new extension
tags. The workflow should surface unmapped concepts and stop, never
guess - this is the one step needing human judgment.

**A real gap found the hard way, not in this phase's own review but
during a later sanity check:** `--mode analyze` never actually called
`step_ratios()` - it went straight to forensics/forecast/backtest,
silently assuming the `ratio` table was already fresh. `fact_value`
rarely changes without a new filing, so this went unnoticed for a long
time - but a CODE fix to `11_ratio_engine.py`'s ratio logic (like the
D&A/revenue/gross-profit fixes earlier in this same phase's history)
would never reach the live `ratio` table via the automated weekly run
either, only via a manual `--mode ratios`/`--mode full`. Confirmed
concretely: after fixing 4 companies' ratio computation, the live
dashboard still showed the OLD values until `11_ratio_engine.py` was
run by hand - `--mode analyze` would have run right past the fix every
Monday, forever, without ever picking it up. Fixed by adding
`step_ratios()` as `--mode analyze`'s first step - it only reads
`fact_value` and writes the DB (+ a local Excel side-artifact CI doesn't
need), the same "DB connectivity only" constraint the three steps
already there satisfy.
**Serves:** both (a live "last updated: [date]" on the public site is a
concrete, checkable automation claim - not an assertion to take on faith).

### Phase 4 — Finish trading comps + precedent transactions ✅ DONE
`19_valuation.py` extended: sector-grouped peer stats (min/median/max),
implied valuation (peer median multiple × own EBITDA → implied EV →
premium/discount vs. actual market cap, requiring ≥2 peers to avoid a
meaningless "median of one"), and forward multiples (CAGR-projected NTM
revenue/EBITDA, reusing `16_forecasting.py`'s `cagr_forecast`).
`20_precedents.py` added: 2 real, cited M&A deals (L'Oreal/Aesop,
EssilorLuxottica/GrandVision) compared against current trading comps -
deliberately only 2 well-verified deals rather than padding to 5 with
uncertain figures.

**Two real bugs found and fixed while actually running this on live data:**
- EBITDA reconstruction only summed 3 granular D&A concepts
  (PP&E/right-of-use/intangibles) - Shell (and likely other capital-
  intensive/extractive companies) discloses ONLY a single combined
  cash-flow-statement D&A line, never the granular breakdown, so its
  `_da_total` silently computed to 0 and its EV/EBITDA came out ~2x too
  high (10.2x vs. the real ~4.3-5.2x, verified against 4 independent
  sources). Fixed by preferring the combined concept when present,
  falling back to the granular sum only when no combined line exists.
- A genuine DATA-LOSS INCIDENT: `09_batch_load.py --reset-facts` (run to
  backfill sector/country metadata) deleted ALL of a company's
  `fact_value` rows regardless of which filing loaded them, silently
  wiping the 2017-2020 historical data `load_historical.py` had loaded -
  caught only because the ratio engine's year coverage collapsed from
  2017-2025 back to 2021-2025 and that was noticed before moving on.
  Fixed by scoping `clear_company_facts()` to exclude any filing whose
  `source_file` contains "historical", with a regression test locking
  the exclusion in place. Data recovered by re-running
  `load_historical.py` (idempotent, source zips still present locally).
**Serves:** Dauphine (Banque d'investissement et de marché).

### Phase 5 — Linked 3-statement projection model ✅ DONE
`21_three_statement_model.py`: Revenue growth → EBIT (margin-driven) →
Interest Expense ↔ Debt balance (circularity) → EBT → Tax → Net Income
→ CFO (using DSO/DIO/DPO-driven ΔWorking Capital) → FCF → dividend
payout → debt paydown → back to Interest Expense. The circularity
solver (Excel's iterative-calculation problem, solved here with a
fixed-point loop) was verified against an INDEPENDENTLY DERIVED
closed-form algebraic solution, not just checked for "did it converge
to something" - see the script's module docstring for the derivation.

**Two real issues found and fixed while running this on live data:**
- `numpy.float64` values (from pandas Series iteration) aren't reliably
  adapted by psycopg2 as SQL parameters - produced a confusing
  `InvalidSchemaName: schema "np" does not exist` error (it tried to
  inline `repr(np.float64(...))` instead of parameterizing the value).
  Fixed by casting every numeric to native `float()` before binding.
- A genuine ECONOMIC implausibility (not a math bug - the circularity
  math was already verified correct): with no dividend assumption,
  100% of FCF silently piled up as debt paydown/cash forever - L'Oreal's
  projected net debt reached -29bn EUR net cash after 5 years, which no
  real dividend-paying company would do. Fixed by computing a
  `payout_ratio` from the company's own disclosed dividends/net income
  (63.9% for L'Oreal, not invented), reducing L'Oreal's 2030 net cash
  projection from -29bn to a much more plausible -3.8bn.
**Serves:** Dauphine (Banque d'investissement et de marché) primarily -
this is the concrete deliverable that proves "I can build what they'll
test me on in an interview or in the program itself."

**Known remaining simplification:** share buybacks aren't modeled,
only dividends - a real "cash returned to shareholders" figure would be
higher than the payout_ratio alone captures, meaning even the current
-3.8bn/2030 net-cash trajectory somewhat understates how close to zero
L'Oreal's real net debt would likely stay.

### Phase 6 — `22_dcf.py` ✅ DONE
WACC build-up (CAPM: risk-free rate + beta from yfinance + equity risk
premium), unlevered FCFF computed directly from EBIT (deliberately NOT
Phase 5's "fcf" column - that's levered, discounting it at WACC would
double-count the cost of debt; see the script's module docstring),
terminal value (Gordon growth), sensitivity table (WACC × terminal
growth grid). Cross-check DCF output against Phase 4's trading comps and
precedent transaction ranges - three methods converging (or explaining
why they don't) is the actual valuation deliverable, not any one method
alone.

**A real bug found while running this on live data, bigger than it
first looked:** the first live run (L'Oreal) showed D&A silently
computing to exactly 0.0 for every projected year. Tracing it back to
`11_ratio_engine.py`'s `_da_total` (shared by the EBITDA reconstruction
in `19_valuation.py`/Phase 4, the D&A projection in
`21_three_statement_model.py`/Phase 5, and this script) showed
`_da_total=0` for 8 of the 11 companies, not just the one Shell case
already fixed - Pernod Ricard, Moncler and Puig Brands use a THIRD D&A
tag variant ("adjustments_for_depreciation_and_amortisation_expense",
no "_and_etc" suffix) that nothing matched. Fixed by adding it as a
third, lowest-priority candidate - verified sane (3.9-14.0% of revenue,
consistent with each company's known capital intensity) before trusting
it. That means Phase 4's trading comps EV/EBITDA multiples for those 3
companies have been understated (EBITDA collapsed to EBIT) since Phase
4 shipped - worth a follow-up run to confirm how much they move.

L'Oreal, LVMH, Kering, EssilorLuxottica and Essity still show
`_da_total=0` and are DELIBERATELY left unfixed: each only has AMBIGUOUS
tags available (bundled with provisions or impairment, split across
several overlapping concepts, or - L'Oreal's case - no D&A tag at all,
folded into one lump "non-cash charges elimination" extension concept).
Guessing which tag is the clean total risks quietly corrupting
EBITDA/FCFF for those companies. Per this roadmap's own Phase 3 rule
("surface unmapped concepts and stop, never guess"), the honest fix
instead is a `da_total_is_fallback` flag (same pattern as the existing
`capex_is_fallback`) - when no real D&A tag matches, `da_total` falls
back to `capex` (the standard steady-state assumption that a mature
company's reinvestment roughly offsets depreciation) and both
`21_three_statement_model.py` and `22_dcf.py` print an explicit
"illustrative only" warning rather than silently treating the 0 as real.
A real fix for those 5 companies needs a human to read each filing's
cash-flow statement and confirm the right tag - not a pattern-matched
guess.

**Follow-up: the 6-of-11 "missing required inputs" gap flagged above is
now fixed for 4 of them.** Root causes traced to `11_ratio_engine.py`,
not `22_dcf.py` itself:
- `get_col(wide, "revenue")` checked ONLY the bare "revenue" tag, no
  fallback - unlike almost every other concept in that file. Kering,
  Pernod Ricard and Amplifon never use it, in ANY year - they exclusively
  tag "revenue_from_contracts_with_customers" (IFRS 15's contract-revenue
  concept, the same top-line figure under a different taxonomy element,
  not an ambiguous case like the D&A tags). Added as a fallback.
- Gross Profit = Revenue - Cost of Sales is a textbook accounting
  identity, not a judgment call - safe to derive whichever of the two a
  company doesn't explicitly tag, as long as the other is present. Danone
  tags cost_of_sales but never a distinct gross_profit subtotal; Essity
  does the reverse. Both now derive the missing one instead of going NaN.
- Fixed a real bug this surfaced, not just a gap: `fetch_base_year()`
  took `idxmax()` on `year` unconditionally - Pernod Ricard's numerically
  latest year (2025) was completely empty (every ratio NaN, likely tied
  to its June 30 fiscal year end), while 2024 had a full, real set of
  ratios. Extracted `select_base_year_row()` to pick the latest year that
  actually has data, falling back to the old behavior only when no year
  does.
- **A second real bug found immediately after, by not trusting a
  plausible-looking number:** the first live DCF run for Essity (SEK
  reporter, the first non-EUR company to ever reach this stage) printed
  a "EUR 884bn" Enterprise Value - actually its real figure IN SEK,
  silently mislabeled as EUR. `22_dcf.py` combined `fetch_base_year()`'s
  native-currency financials with an already-EUR-converted live market
  cap for the WACC weights, without ever converting the financials
  themselves - both the capital-structure weights AND the discounted
  cash flows were wrong. Fixed with `convert_base_to_eur()`, using
  `18_fx_convert.py`'s stored historical rates (average for flow items,
  closing for balance-sheet items - same IAS 21 split `19_valuation.py`
  already established, not a new convention). Re-running Essity's DCF
  after the fix: EUR 884bn -> EUR 43bn, the right order of magnitude.
  Trading comps now cover all 11 companies too (was 8), not just the 4
  fixed here - Kering and Amplifon's revenue fix was enough on its own.

Amplifon and Shell still correctly fail - both tag NEITHER gross_profit
nor cost_of_sales at all ("by nature" P&L presentation, no COGS/gross-
profit split exists in their statements), so nothing can be derived.
Same "explicit not available, never guess" principle as the D&A gap
above, not an oversight.
**Serves:** Dauphine.

### Phase 7 — Market risk & return module (`23_market_risk.py`) ✅ DONE
Before this phase the ENTIRE pipeline was fundamentals-only - zero
analysis of actual stock price behavior. Using yfinance price history
(same source already used for market cap): volatility, Sharpe ratio,
max drawdown, beta vs. a benchmark, rolling correlation. This was not
optional polish for the Asset Management track - it's the most basic
vocabulary of that discipline, and the project had none of it despite
Asset Management being an explicit goal. 11/11 companies covered (this
layer only needs price history, not XBRL tag completeness, so it
doesn't inherit the fundamentals side's per-company data gaps). Wired
into the dashboard as a new "Market Risk" tab; 14 new regression tests.

**Renumbered from the originally-planned `22_market_risk.py`** - `22`
was already taken by `22_dcf.py` (Phase 6) by the time this phase was
written up; `23_credit.py`/`24_scenario.py` below are bumped to
`24`/`25` for the same reason, found while actually starting this phase
rather than left to collide later.

**Benchmark: STOXX Europe 600 (`^STOXX` on Yahoo Finance), not CAC 40** -
this universe spans France, Italy, Spain, Sweden and the UK; a
France-only index would be the wrong comparison for Essity, Shell,
Amplifon, Puig Brands and Moncler. One consistent pan-European benchmark
for all 11 companies, not a per-country one - same "one universe-wide
comparison" principle as the single EUR conversion used everywhere else
in this project.

**Beta computed from raw price history, not read from yfinance's own
`info.get("beta")`** (which `22_dcf.py` already uses for the DCF WACC) -
deliberately different from that script, and for a real reason: Yahoo's
own beta is an opaque black box (unstated benchmark, unstated lookback,
unstated frequency), fine as a quick WACC input but not something this
project would claim as its own analysis. Regressing the company's own
daily returns against `^STOXX`'s is the actual "market risk" contribution
Phase 7 exists to add - computed transparently, with the exact window
and frequency stated, instead of trusted from an opaque number.
**Serves:** Dauphine (Gestion d'Actifs) specifically - directly maps to
"Investissements et marchés financiers" and "Introduction à
l'économétrie de la finance" coursework.

### Phase 8 — `24_credit.py` ✅ DONE
Net Debt/EBITDA trajectory → simple credit-profile classification (fixed
sector-agnostic bands: net cash / very low / low / moderate / elevated /
high / very high leverage) plus a YoY trend label (improving/stable/
deteriorating).

**A real bug found while building this, not the "mostly reuse
net_debt_ebitda_proxy" shortcut originally planned:** `11_ratio_engine.py`
already has a column literally named `net_debt_ebitda_proxy` - reusing
it would have been the obvious low-cost path this phase was scoped
around. It's actually Net Debt / EBIT, not Net Debt / EBITDA - its own
display label ("Net Debt vs Op. Profit") says so honestly, only the
COLUMN NAME is misleading. Real-world credit thresholds are calibrated
to EBITDA; EBIT understates EBITDA by the D&A add-back, so reusing that
column would have systematically OVERSTATED every company's leverage.
Computes Net Debt / `_ebitda` directly instead. Every row where
`_da_total == 0` (no D&A tag matched - see Phase 6's D&A investigation)
is flagged `is_da_fallback` and shown with an explicit "EBITDA=EBIT,
overstated" warning, both in the CLI output and the dashboard - the
same "never silently treat a fallback as real" principle as
`da_total_is_fallback` elsewhere, applied here because THIS script is
exactly where that upstream gap would have caused a second, compounding
wrong number if left unflagged.

19 new regression tests on the pure classification logic (`classify_band`,
`classify_trend`, `build_credit_profile` - factored out from the DB-
fetching wrapper the same way `select_base_year_row()` was, specifically
so it's unit-testable without a database connection). Wired into the
dashboard as a new "Credit Profile" tab.
**Serves:** Dauphine (credit/markets angle) + Controlling (counterparty
risk is a real controlling concern).

### Phase 9 — `25_scenario.py` ✅ DONE
Perturb one or more base-year assumptions (revenue, operating margin,
revenue growth, cost of debt, capex - any combination in one run) and
recompute the downstream 3-statement projection, unlevered FCFF,
discounted Enterprise Value, and Net Debt/EBITDA leverage band, side by
side against the unshocked base case. Genuinely shares its core with
Phase 6's DCF and Phase 5's 3-statement model, not just in spirit:
imports `21_three_statement_model.py`'s `project()`, `22_dcf.py`'s
`compute_fcff()`/`discount_cash_flows()`, and `24_credit.py`'s
`classify_band()` directly, so a shock's effect is computed the exact
same way those scripts compute the unshocked case - not a parallel
reimplementation that could quietly drift from them.

**WACC held constant across scenarios, deliberately:** an operating
shock (revenue/margin/growth/capex) and a financing-conditions shock
(WACC) are different questions - re-deriving WACC under an operating
shock would conflate them. WACC is fetched once from live market data
and fixed for both the base and shocked run, so the entire Enterprise
Value delta shown is attributable to the operating shock alone.

**A real, subtle bug found via a live run, not assumed:** the first run
(L'Oreal, a net-cash company, margin -2pp) showed "Net Debt (end):
-12.09bn -> -10.65bn (-11.9%)" - net debt became LESS negative (leverage
moved the wrong way) but the percentage read as an *improvement*, because
dividing a positive delta by a negative base flips the sign. A first fix
(only guarding against a sign FLIP) still let this through, since the
base stayed negative throughout rather than crossing zero - dividing by
ANY negative base is misleading, not just a sign-flipping one. Fixed by
`format_delta_pct()` requiring BOTH the base and shocked value to be
strictly positive before showing a percentage at all; the absolute
delta is always shown regardless.

**Deliberately no database table or dashboard tab:** every other
analysis phase computes ONE canonical fact per company worth persisting
and showing by default - a scenario is parameterized by whatever shock
was just typed on the command line, so there's no single "the" scenario
to show. CLI + Excel output only, consistent with `20_precedents.py`
also not needing deep dashboard interactivity.

11 new regression tests (131 total) on `apply_shocks()` and
`format_delta_pct()` - both pure functions, no database or market-data
calls in the test suite. Verified live across companies with negative
net debt (L'Oreal, Moncler), positive net debt (Danone), and a non-EUR
reporter requiring the Phase 6 currency-conversion path (Essity).
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
- ~~Kering DSO/CCC gap~~ — **RESOLVED.** Was actually the `_revenue`/
  gross-profit fallback gap fixed in Phase 6's follow-up (Kering never
  tagged bare "revenue", only "revenue_from_contracts_with_customers") -
  not a single missing receivables tag as originally guessed here. DSO,
  DIO, DPO, CCC all work for Kering now.
- **Streamlit app URL is auto-generated and long** (haphuongnguyen110902-cell-ifrs-pipeline-webappapp-iwnn9s.streamlit.app) -
  cosmetic, not urgent, but genuinely unblocked now: "once the app has
  content worth a polished URL" (Phase 4+) has been true since this
  phase shipped. Streamlit Cloud allows setting a custom subdomain under
  app Settings → General → App URL - a 5-minute task whenever it's
  worth doing, not gated on anything else anymore.
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
- **Canonical concept layer** — company-specific "adjusted operating
  profit" tags (`loreal:ResultatDexploitation`,
  `LVM:ProfitLossFromOperatingActivitiesRecurring...`,
  `essi:OperatingProfitExclIAC`) are each their own concept today, three
  separate definitions treated as comparable by `get_best()`'s fallback
  chains rather than genuinely unified. The schema already supports a
  proper many-to-one canonical layer (`concept_mapping`) - this is
  design debt carried since V0 (see the now-archived `archive/NOTES.md`),
  not a new finding, and still unaddressed. Worth a dedicated pass once
  the universe grows past 11 companies and the "different tag, same
  economic line item" pattern gets more frequent, not urgent at this size.
- **Dimensional facts** — segment/geographic breakdowns are dropped
  entirely at parse time; the `dimensions` JSONB column exists in the
  schema but has never been populated. Blocks any geographic-exposure or
  segment-margin analysis (relevant to the Asset Management track
  specifically). Same V0-era design debt as the canonical concept layer
  above - real, not forgotten, just correctly sequenced behind breadth
  (Phase 11) rather than done now for an 11-company universe where it
  wouldn't yet pay for itself.
