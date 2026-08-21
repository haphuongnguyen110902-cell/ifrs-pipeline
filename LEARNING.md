# What to Learn While Building This

Ordered roughly by when you'll hit it. Marked **[NOW]** if it directly unblocks
current work, **[SOON]** for the next phase, **[LATER]** for reference when you
get there. Everything listed is free unless noted.

---

## 1. XBRL & ESEF mechanics **[NOW]**

You're already using this daily. Understanding it properly is a genuine
differentiator - almost nobody applying for controlling or IB roles can explain
how structured financial reporting actually works.

**What to understand:**
- The four things that define a fact: concept, value, context (entity + period), unit
- Why `instant` vs `duration` periods exist and why the dates look "off by one"
- What a taxonomy is, and why companies create *extension* concepts (`loreal:`, `LVM:`, `essi:`)
- Why extensions are the central problem for comparability - this is the crux of your project
- Inline XBRL (iXBRL): the report is a human-readable web page with machine-readable tags embedded

**Sources:**
- XBRL International "What is XBRL": https://www.xbrl.org/the-standard/what/
- ESMA ESEF Reporting Manual (the actual EU rulebook - search "ESMA ESEF Reporting Manual", it's a free PDF, updated periodically)
- Arelle documentation: https://arelle.readthedocs.io/
- Arelle Python API specifically: https://arelle.readthedocs.io/en/latest/python_api/python_api.html
- filings.xbrl.org (your data source) about + API docs: https://filings.xbrl.org/docs/about
- Arelle users Google Group - genuinely useful for specific errors, active maintainers

**Concrete exercise:** open one filing's `.xhtml` in a browser and find the same
number in your `fact_value` table. Seeing the human view and the machine view of
the same fact makes the whole model click.

---

## 2. IFRS - the standards behind the numbers **[NOW]**

You're classifying ~300 concepts. Knowing what they mean is the difference
between mapping correctly and guessing.

**Priority standards for this project:**
| Standard | Why it matters here |
|---|---|
| **IAS 1** | Presentation of financial statements - defines what goes on which statement, current vs non-current |
| **IAS 7** | Statement of cash flows - the operating/investing/financing split you're mapping |
| **IAS 21** | FX translation - closing rate vs average rate. Directly drives your FX design |
| **IFRS 15** | Revenue recognition - what "Revenue" actually contains |
| **IFRS 16** | Leases - explains why `RightofuseAssets` and `LeaseLiabilities` exist at all |
| **IAS 36** | Impairment - relevant to goodwill and the "non-recurring items" you keep seeing |
| **IFRS 8** | Operating segments - matters when you get to dimensional/segment data |

**Sources:**
- IFRS Foundation, free registered access to the standards: https://www.ifrs.org/
- IFRS Foundation "Who we are / Around the world" pages for context on adoption
- Big-4 IFRS summaries (PwC "Manual of accounting", Deloitte IAS Plus, EY, KPMG all publish free summary pages) - IAS Plus is the most accessible: https://www.iasplus.com/
- Your Dauphine coursework - this is the one area where formal study beats self-teaching

**Concrete exercise:** for five concepts you classified as `other`, look up which
standard governs them and decide if `other` was really right.

---

## 3. Financial statement analysis **[SOON]**

This is what turns a data pipeline into an analysis tool. It's also the part an
interviewer will actually probe.

**What to understand before building the ratio engine:**
- Margin structure: gross, EBITDA, EBIT, net - and why companies report "adjusted" versions
- ROIC vs ROE vs ROA: what capital base each uses and when each is the right lens
- Cash conversion cycle: DSO, DIO, DPO - and why the working capital line in the cash flow statement matters
- Free cash flow: the several definitions in use, and why you must state which you mean
- Leverage: Net Debt/EBITDA, interest coverage - and the IFRS 16 complication (lease liabilities: debt or not?)
- Earnings quality: divergence between reported profit and cash generation

**Sources:**
- Aswath Damodaran (NYU Stern) - the single best free resource in finance. Full course videos, spreadsheets, and lecture notes: https://pages.stern.nyu.edu/~adamodar/
- Damodaran's "Valuation" and "Corporate Finance" playlists on YouTube
- CFA Institute program curriculum outlines (free) - good checklist of what a professional is expected to know
- Corporate Finance Institute (CFI) free articles - decent quick reference, less rigorous
- Company annual reports themselves: read L'Oreal's actual management discussion, then check whether your pipeline's numbers tell the same story

**Concrete exercise:** compute gross margin for L'Oreal by hand from the filing,
then from your database. If they differ, you've found a mapping bug.

---

## 4. Python for data work **[NOW]**

You're using this constantly. Depth here compounds.

**What to get comfortable with:**
- pandas: `groupby`, `pivot_table`, `merge`, `reindex` - you've used all four already
- Understanding *why* pandas defaults surprised you (alphabetical sort, NaN handling)
- Virtual environments and dependency management (you've done this - understand what it actually isolates)
- `argparse` for CLI scripts, `pathlib` over string paths
- Error handling: try/except around anything touching a network or a file
- Type hints - not required, but they make code readable and are standard professionally

**Sources:**
- pandas official user guide: https://pandas.pydata.org/docs/user_guide/
- "Python for Data Analysis" by Wes McKinney (pandas' creator) - free online: https://wesmckinney.com/book/
- Real Python (https://realpython.com/) - reliable tutorials, some free some paid
- Python official tutorial: https://docs.python.org/3/tutorial/

---

## 5. SQL & data modeling **[NOW]**

Your database is the spine of the whole project. Controllers who can write SQL
are meaningfully more employable than those who can't.

**What to understand:**
- JOINs - you're already doing 4-table joins in `07_generate_statements.py`, make sure you can explain each one
- Primary/foreign keys and referential integrity
- Why the schema is normalized the way it is (company -> filing -> period -> fact)
- Indexes: why `idx_fact_value_concept` exists and what it speeds up
- Idempotency: why `get_or_create` patterns matter, what happens without them
- Aggregation: `GROUP BY`, window functions (these will matter for growth rates)

**Sources:**
- PostgreSQL official tutorial: https://www.postgresql.org/docs/current/tutorial.html
- Mode Analytics SQL tutorial (free, interactive, well-paced): https://mode.com/sql-tutorial/
- SQLBolt (free, browser-based exercises): https://sqlbolt.com/
- Neon's own docs for the hosted specifics: https://neon.tech/docs
- "Designing Data-Intensive Applications" by Martin Kleppmann - the serious book, worth it later

**Concrete exercise:** write a SQL query that returns revenue growth year-over-year
for one company, using a window function. This is directly reusable.

---

## 6. Data engineering concepts **[SOON]**

The vocabulary that makes your project sound professional rather than academic.

**What to understand:**
- ETL / ELT: extract, transform, load - name what you've already built
- Idempotency: running twice produces the same result (your loader does this)
- Data provenance / lineage: tracing a number back to its source (your schema does this deliberately)
- Single source of truth: why you store local currency and convert at query time
- Schema-on-write vs schema-on-read
- Data validation as a pipeline stage, not an afterthought

**Sources:**
- "Fundamentals of Data Engineering" (Reis & Housley) - the standard modern text
- dbt's documentation on testing and data quality - even if you never use dbt, the concepts transfer: https://docs.getdbt.com/
- Great Expectations docs (data validation framework) for how validation is done professionally

---

## 7. Valuation **[LATER - V2]**

Only needed when you build the DCF and comps engine.

**Sources:**
- Damodaran again - he has entire free courses on valuation, plus downloadable spreadsheets that are worth studying as models of clear structure
- Damodaran's annual data page (industry betas, margins, cost of capital by sector) - directly usable as benchmarks in your project: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html
- Mergers & Inquisitions / Breaking Into Wall Street - practical IB modelling conventions (some free content)

---

## 8. Git, GitHub, and CI/CD **[SOON]**

Your roadmap's V4, but start using git properly *now* - it costs nothing and
recruiters look at commit history.

**What to understand:**
- Commit, branch, merge - the basics, done regularly
- `.gitignore` and why `.env` must never be committed (you have this right already)
- README vs ROADMAP vs NOTES - what each is for
- GitHub Actions: workflows, cron schedules, secrets

**Sources:**
- Pro Git book, free online: https://git-scm.com/book/en/v2
- GitHub Skills interactive courses: https://skills.github.com/
- GitHub Actions docs: https://docs.github.com/en/actions

---

## 9. FX and constant-currency reporting **[SOON]**

Specific to the problem you just identified.

**What to understand:**
- IAS 21 translation mechanics (closing vs average rate)
- Translation exposure vs transaction exposure
- Constant-currency / organic growth: how companies strip FX out of growth figures
- Why cumulative translation adjustment sits in OCI (you've mapped several of these)

**Sources:**
- ECB reference rates and methodology: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html
- ECB Data Portal API docs: https://data.ecb.europa.eu/help/api/data
- Any large multinational's annual report - look for their "constant currency" or "comparable growth" reconciliation. L'Oreal and Essity both publish one.

---

## Meta-advice

**Learn by breaking your own project.** Every bug you've hit so far (the 0-facts
parse, the year offset, alphabetical ordering) taught more than reading would
have. Keep that pattern: when something looks wrong, chase the root cause rather
than patching around it.

**Write down what you learn.** The `NOTES.md` bugs section is already interview
material - "I found that Arelle stores instant dates one day ahead because XBRL
treats a calendar day as a span" is a much better answer than "I used a library."

**Don't try to learn all of this before continuing.** Build, hit a wall, then
read the specific thing that unblocks you. The list is a map, not a syllabus.
