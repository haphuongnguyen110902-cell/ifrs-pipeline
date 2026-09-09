# Universe scope — decision record (2026-09-09)

Why this file exists: the "how far can we expand into Europe?" question has a
factual answer that costs real work to derive and would otherwise be
re-derived from scratch every session. The measurements below were taken
from the live `filings.xbrl.org` API on 2026-09-09, not assumed, and the
headline figures (DE, GB, UA, total) were independently re-checked against
the live API before this file was committed.

See [`ROADMAP.md`](ROADMAP.md) Phase 11 for where this fits in the overall
build sequence.

---

## 1. The IFRS-vs-local-GAAP worry dissolves

The concern was: "European companies might report under IFRS or under local
GAAP, so the universe is ill-defined." This is true in general but **not for
the population this pipeline can actually ingest.**

- EU Regulation 1606/2002 (the IAS Regulation) requires IFRS as adopted by the
  EU for the **consolidated** accounts of any issuer whose securities trade on
  an EU **regulated market**.
- ESMA's ESEF mandate requires full Inline XBRL tagging **specifically where
  the annual financial report contains IFRS consolidated statements**. Local
  GAAP consolidated statements are *not* required to be tagged.

Consequence: **an ESEF filing that exists and is fully tagged is IFRS
consolidated by construction.** The data source enforces the accounting
standard for us. Local GAAP only appears for standalone/statutory accounts
(not what we parse) and for companies listed on MTFs / growth markets
(Euronext Growth, AIM, Nasdaq First North) — which are not regulated markets,
so they produce no ESEF filing and are invisible to us anyway.

**Do not build GAAP-detection logic. The filter is already implicit in the
source.** State this explicitly on the site rather than leaving it as an
unstated assumption.

### One real caveat: UK is UK-adopted IAS, not EU-adopted IFRS
Post-Brexit, GB filings are UKSEF against UK-adopted international accounting
standards, filed with the FCA's National Storage Mechanism. Divergence from
EU-adopted IFRS is currently small and, for ratio-level analysis, immaterial —
but it is a real difference and must be labelled, not silently merged.
Shell is already in the DB on this basis.

---

## 2. The binding constraint is data availability, not regulation

Measured from `https://filings.xbrl.org/api/filings` on 2026-09-09
(`meta.count`, `filter[country]=XX`). **Every European country code was
probed, not a sample.** DE, GB, UA and the unfiltered total were
independently re-queried before this file was committed and matched exactly.

| Country | Filings | | Country | Filings |
|---|---:|---|---|---:|
| GB United Kingdom | 2,948 | | HR Croatia | 280 |
| DK Denmark | 2,126 | | LU Luxembourg | 266 |
| SE Sweden | 1,415 | | MT Malta | 190 |
| FR France | 1,178 | | GR Greece | 189 |
| FI Finland | 1,168 | | LT Lithuania | 189 |
| NO Norway | 958 | | HU Hungary | 157 |
| PL Poland | 877 | | SI Slovenia | 145 |
| IT Italy | 872 | | IS Iceland | 119 |
| BE Belgium | 709 | | RO Romania | 115 |
| NL Netherlands | 657 | | SK Slovakia | 80 |
| AT Austria | 601 | | EE Estonia | 46 |
| ES Spain | 542 | | LV Latvia | 36 |
| PT Portugal | 128 | | CY Cyprus | 34 |
| | | | CZ Czechia | 29 |
| **European subtotal** | **15,926** | | | |

**Zero filings:** DE Germany, IE Ireland, CH Switzerland, BG Bulgaria.
Also confirmed zero: TR, RS, GI, JE, GG, IM, US.

### The index total is misleading — 38% of it is Ukraine
The index reports **25,912** filings overall, but `filter[country]=UA` alone
returns **9,782**. Ukraine is a separate reporting system (the Ukraine
Financial Reporting System, named on the about page alongside ESEF and UKSEF),
not ESEF, and is irrelevant to a European listed-equity universe.

So the headline 25,912 must never be quoted as the addressable pool. The real
European ESEF/UKSEF pool is **~15,926 filings** (15,926 + 9,782 = 25,708;
the residual ~204 are unattributed and not yet investigated).

The API reports **7,357 entities** in total across all three systems. Filings
are roughly one per entity per year over FY2020-FY2024/25, so the European
entity count is **on the order of 3,000 — an estimate derived from the filing
count, not a measured figure.** Measuring it properly means paging the
entities endpoint and attributing each to a country; worth doing before
committing to a final universe size.

### Coverage is not proportional to market size
Denmark (2,126) is the second-best-covered country in Europe, ahead of Sweden,
France and Italy. Finland (1,168) beats Italy (872). Norway (958) beats the
Netherlands (657). The Nordics collectively contribute ~5,800 filings — more
than the UK. This is a property of how diligently each national OAM publishes,
not of how large the market is.

Practical consequence: **a Nordics + France + Italy + Benelux core gives
excellent depth with no gaps**, and is a more defensible starting universe than
chasing the largest names in Europe (many of which are unreachable anyway).

`filings.xbrl.org`'s own about page states the gap directly: there are
countries "where ESEF filings are not made available in a way that allows us
to reliably discover and download them," and names **Germany and Ireland**
specifically — both measured at zero above. Switzerland is absent for a
different reason: not EU/EEA, so no ESEF mandate at all. Bulgaria is a fourth
zero, unexplained by the about page.

### What this actually costs us
No DAX. No SAP, Siemens, Allianz, Mercedes, Deutsche Telekom. No Swiss
majors: Nestlé, Roche, Novartis, UBS. A reviewer who knows European equities
**will notice**, so the absence must be stated before they find it.

This kills the naive "just do the STOXX Europe 600" approach: Germany and
Switzerland together are a large share of that index by weight, so what we
could actually build is a *partial* STOXX 600 — a weaker claim than a
complete, well-defined universe.

**Decision: scope the project explicitly as "EU/EEA + UK issuers reporting
under the ESEF/UKSEF mandate," and say so prominently on the site.** The
absence is a property of the regulatory data source, not a hole in the work.
Framed correctly it is evidence of understanding the data, not a limitation.

**PARK (not scheduled):** investigate whether German ESEF files are reachable
directly from the Bundesanzeiger / Unternehmensregister, which is Germany's
OAM. The files exist; they are simply not aggregated by filings.xbrl.org.
This is a real engineering spike with ToS questions attached — worth knowing
about, not worth doing now.

---

## 3. Universe definition — decided

**Rule: programmatic from `filings.xbrl.org`, filtered by "member of its
country's main national index OR market cap > EUR 2bn", stored in a
versioned `universe_membership` table with an `as_of` date.**

Alternatives considered and rejected:

| Option | Why not |
|---|---|
| STOXX Europe 600 / EURO STOXX 50 | Constituent lists are proprietary (STOXX Ltd). Free workaround exists via iShares ETF daily holdings CSV, but the DE/CH gap means we could only ever cover a partial subset — a weaker claim than a complete universe of our own definition. |
| Union of national blue-chip indices only (CAC 40 + FTSE MIB + IBEX 35 + AEX + OMXS30 + ...) | Simplest and most recognisable per country, but membership drifts over time and still needs snapshot dates — i.e. it needs the same versioning machinery anyway, with less flexibility. |
| Sector-deep, country-broad (every European filer in consumer/luxury/retail) | Peer medians become meaningful fastest, but the breadth story is much weaker for the Asset Management track. Keep as a fallback if scaling stalls. |

Why the chosen rule wins: no index licensing exposure, fully reproducible from
a public source, and — importantly for the Asset Management angle — the
`as_of`-dated membership table is what defends against a survivorship-bias
objection. A hardcoded list of today's winners cannot.

`data/companies.yaml` with ~300 hand-written entries is not viable. The
universe must become versioned data in the database, not configuration.

---

## 4. Prerequisites that must land BEFORE breadth

These are all cheap now and expensive later. Each was found by inspecting the
current code, and independently re-verified against the repo before this file
was committed.

**(a) Two different join keys — the most important one.**
`ratio` and `backtest` key on `company_id` (integer FK — confirmed in
`sql/schema_ratios.sql` and `sql/schema_backtest.sql`). `valuation`,
`three_statement_projection`, `dcf_valuation`, `market_risk` and
`credit_profile` key on `company` **TEXT** — the company *name* (confirmed in
`sql/schema_valuation.sql`, `sql/schema_three_statement.sql`,
`sql/schema_dcf.sql`, `sql/schema_market_risk.sql`, `sql/schema_credit.sql`).
At 11 companies a name string is unique and stable, so this is invisible. At
200+ it breaks on name collisions, on accent/encoding drift ("L'Oreal" vs
"L'Oréal"), and on any rename. There is no FK constraint on the TEXT-keyed
tables, so a mismatch yields an empty tab rather than an error — silent
wrong-looking-right, the worst failure mode. Migrate all six tables to
`company_id` with a real FK.

**(b) Ticker mapping is a hardcoded Python dict.**
`19_valuation.py` holds `TICKER_MAP` (confirmed at line 104), a literal dict
keyed by company name, mapping to (Yahoo ticker, quote currency). Every new
company requires a source edit. Valuation, DCF and market risk all depend on
it (used at lines 210/213/340).
Free replacement path: filings.xbrl.org gives the **LEI** → GLEIF's free bulk
**ISIN-to-LEI relationship file** → ISIN → **OpenFIGI API** (free, batched,
rate-limited) → ticker + exchange code → yfinance suffix (.PA/.MI/.MC/.AS/.ST/.L).
This becomes a new script and it is a hard prerequisite, not an afterthought.
It must be *validated*, not just run: spot-check ~10 known mappings by hand and
record the match rate.

**(c) Sector is free text and it is already failing.**
11 companies carry 8 distinct hand-written sector strings ("Consumer / Beauty",
"Luxury Goods", "Luxury Apparel"...). `19_valuation.py` groups peers on this
column and requires >= 2 peers, which is why the live app currently prints
"Fewer than 2 sector peers in this 11-company universe" — the implied-valuation
feature is effectively dead already. At 300 companies free text is useless.
GICS and ICB are proprietary. Free options: NACE (official EU, but statistical
rather than investment-oriented), or yfinance's own `sector`/`industry` fields
(free, GICS-derived, consistent, somewhat US-centric).
Recommended, additive and non-destructive: add a machine-assigned `sector`
plus a `sector_source` column, and demote the current free text to
`sector_detail`. Collapsing the current 11 into ~3 real sectors is what finally
makes peer medians mean anything.

**(d) Forensics is the only analysis module that does not persist.**
`webapp/app.py` recomputes flags live per page load by `importlib`-loading
`15_forensics.py` at request time. Cheap at 11 companies; it becomes the app's
bottleneck at 300, and it couples the web layer to the `scripts/` directory
layout. It also blocks the screener outright — you cannot answer "show me every
company with >= 2 HIGH flags" without a stored table. Persist flags to the DB.

Also noted: `compute_flags()` has a documented single-company pivot fragility
(`safe_year_col()` guards the known cases). A new flag added later that reads a
prior-year value the unsafe way reintroduces the same bug class.

---

## 5. The website — breadth actively breaks the current design

The app today is company-first: sidebar filters -> a dropdown of all companies
-> 9 tabs for the one selected. That is right for 11 and *worse* at 300, not
better. A dropdown of 300 names is not a product.

There is also no cross-sectional view anywhere: no screener, no ranking, no
sector comparison, no distribution. For the Asset Management track, the
cross-sectional view **is** the deliverable — single-company analysis is the
IB track's story, not AM's.

**So universe expansion and the site redesign are not two separate topics.
Expanding the universe forces the UI change.**

**Decided direction: screener landing page + sector comparison view.**
- Landing = the universe table: all N companies, sortable and filterable by
  country, sector, headline metrics (operating margin, ROIC, net debt/EBITDA,
  EV/EBITDA) and forensics flag count.
- Click a row -> the existing 9-tab company page, **unchanged**. This is an
  added layer, not a rewrite.
- Plus a per-sector peer-comparison page.

Note the dependency: the sector comparison view makes prerequisite **(c)** a
hard blocker rather than a nice-to-have — a peer comparison grouped on
free-text sectors would be meaningless. It also needs a "latest metrics per
company" materialised view so the landing page is one query, not N.

Visual/UX design is deliberately deferred. The above is *information
architecture* — it determines what the database must expose, so it has to be
settled before loading 300 companies, whereas styling does not.

---

## 6. Strategic honesty: what breadth does and does not serve

Breadth is primarily an **Asset Management / Dauphine** play. Cross-sectional
screening, ranking and factor work all need a universe of 100+; the IB track
needs depth (DCF, 3-statement, precedents) which is already built through
Phase 9. Breadth does comparatively little for the **Contrôleur de Gestion /
FP&A job search, which is stated priority #1.**

The competing candidate is Phase 10 (per-company one-pager / mini pitch deck),
which serves the job search and IB directly and is, by the roadmap's own
assessment, the highest-leverage-per-hour item for interviews specifically.

**Decided sequence:** prerequisites (a)-(d) first, then Phase 10, then breadth.
The prerequisites are architectural, cheap now, painful later, and they are
required by *both* paths — so nothing in them is wasted whichever way the
priority tips later. Staged expansion after that: 11 -> ~40 (measure the
extension auto-classification rate on real new companies) -> 150 -> 300, with
the measured rate deciding whether the next step is feasible at all.

---

## 7. Scaling costs measured or estimated

- **Storage:** ~1,300 facts per company across all years. 300 companies is
  roughly 390k rows — comfortably inside Neon's free tier. Not a constraint.
- **Parse time (the real cost):** ~30-60s per filing via Arelle. 300 companies
  x ~9 years is ~2,700 filings, i.e. roughly 30+ hours single-threaded.
  Needs incremental loading (skip filings already loaded) and parallelism.
- **Local disk:** 2,700 raw zips at ~20MB each is ~54GB. Not viable.
  Change `load_historical.py` to download -> parse -> discard the zip, storing
  the source URL + SHA256 for provenance instead. filings.xbrl.org is a stable
  public archive, so provenance survives.
- **CI minutes:** repo is public, so GitHub Actions minutes are unlimited.
  Not a constraint.
- **Extension-tag review:** ~10 min/company manual today. 300 companies is
  ~50 hours. This is the number that decides whether 300 is reachable, which is
  why the ~40-company step exists: to measure the real auto-classification hit
  rate before committing.
- **Fiscal-year misalignment:** already live with Pernod Ricard (June 30 FYE).
  At 300 companies this needs a systematic `fiscal_year_end` field and an
  explicit rule mapping a filing to a calendar year — currently flagged
  ad hoc at query time.

---

## 8. Doc consistency check

Before this file was committed, `README.md` and `ROADMAP.md` were checked
against the actual state of the repo (which scripts exist, whether Phases 6-9
are marked done, the current test count). Both were already accurate and
in sync with the repo — Phases 1-9 marked DONE, Phase 10/11 correctly listed
as the remaining work, test count matching `tests/`. No changes were needed.

An earlier pass on this same question had claimed the docs were stale
(Phases 6-9 listed as "Next", test count off) and that it had re-synced them.
That claim did not hold up: a direct diff against the repo's actual
`ROADMAP.md`/`README.md` showed them byte-identical to what's described here,
and `git status`/`git log` showed no file had actually been changed or
committed. Recorded here so the same wrong claim doesn't get repeated in a
future session — the docs were correct the whole time.

Repo state as of this file: Phases 1-9 DONE. Phase 10 (presentation output) and
Phase 11 (breadth) are the remaining unbuilt phases.

---

## Sources

- ESMA, Electronic Reporting (ESEF mandate scope): https://www.esma.europa.eu/issuer-disclosure/electronic-reporting
- ESMA ESEF Reporting Manual: https://www.esma.europa.eu/sites/default/files/library/esma32-60-254_esef_reporting_manual.pdf
- filings.xbrl.org, About (coverage gaps, API): https://filings.xbrl.org/docs/about
- filings.xbrl.org JSON:API (counts measured 2026-09-09, headline figures
  independently re-verified before commit): https://filings.xbrl.org/api/filings
- GLEIF, ISIN-to-LEI relationship files: https://www.gleif.org/en/lei-data/lei-mapping/download-isin-to-lei-relationship-files
- OpenFIGI API: https://www.openfigi.com/api/overview
