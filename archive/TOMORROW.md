# Monday Session Plan — Full Day (8 hours)
_Work top to bottom. Each block has a clear goal and expected output._

---

## STARTUP CHECKLIST (10 min)

```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
python scripts\08_validate.py
git pull
```

Must show `53/53` and `5/5` before starting anything else.

---

## THE REAL PRIORITY: unmapped concepts

**Context:** 33% of all facts across 11 companies are not loading.
The biggest gaps:

| Company | Skip % | Why it matters |
|---|---|---|
| Amplifon | 48% | Nearly half the filing is invisible |
| Moncler | 47% | Same |
| Puig Brands | 45% | Same |
| Danone | 39% | Key consumer staples comp |
| Kering | 32% | Key luxury comp |

These numbers mean your comps table is missing real data. Gross margins and
operating margins may be correct, but balance sheet ratios (ROIC, ROE, net debt)
are built on incomplete data for these companies.

**The tool to fix this already exists:** `12_prep_company.py` was built
specifically for this. We've only run it on LVMH so far.

---

## BLOCK 1 — Close the unmapped gap (~3 hours)

Run `12_prep_company.py` on every under-mapped company. Do them in order
of severity (worst first). For each one:

### Step A: run the prep script
```
python scripts\12_prep_company.py --zip data\raw\Amplifon_2025.zip
```

This will:
- Auto-classify all standard `ifrs-full:` tags (zero manual work)
- Write extension tags to `data\mappings\REVIEW_extensions.yaml`

### Step B: review the extension tags
```
notepad data\mappings\REVIEW_extensions.yaml
```

For each entry, change `statement: REVIEW` to one of:
`income_statement` / `balance_sheet` / `cash_flow` / `other`

**How to decide:** read the `display_label` field — it's the official
English name from the taxonomy. The statement is usually obvious from
the label. When in doubt, ask me.

### Step C: apply the review
```
python scripts\12_apply_review.py
```

### Step D: reload the company
```
python scripts\09_batch_load.py --only Amplifon_2025 --reset-facts
```

### Step E: validate
```
python scripts\08_validate.py --company "Amplifon"
```

**Repeat for each company, in this order:**
1. `Amplifon_2025.zip`
2. `Moncler_2025.zip`
3. `puig.zip` (stem is `puig`, not `Puig_Brands`)
4. `danone.zip`
5. `Kering_2023.zip`
6. `essilorluxottica_new.zip`
7. `pernod_ricard.zip`
8. `essity.zip`
9. `Shell_2025.zip`
10. `loreal_2025.zip` (yes, even L'Oreal — it has 11 unmapped)
11. `LVMH_2024.zip` (already mostly done, but rerun to catch the 4 new standard tags)

**IMPORTANT:** run `12_apply_review.py` BEFORE moving to the next company.
The REVIEW file gets overwritten each time. If you run two companies without
applying, you lose the first one's review.

After all companies done, run full validation:
```
python scripts\08_validate.py
python scripts\11_ratio_engine.py
```

The ROIC and coverage numbers should improve significantly.

---

## BLOCK 2 — README rewrite (~1.5 hours)

**This is the highest-value portfolio task.**
Your GitHub README still says "V0 (foundations)." A recruiter opening
it sees the wrong thing.

Open it:
```
notepad README.md
```

Rewrite it to cover:

**1. What it is (1 sentence)**
"Automated pipeline for parsing, normalising, and analysing IFRS/XBRL
financial filings from European listed companies."

**2. What it does (5-6 bullets)**
- Parses ESEF/XBRL packages directly from ESMA-regulated filings using Arelle
- Normalises raw XBRL tags to standardised IFRS concepts via semantic mapping
- Stores facts in PostgreSQL (Neon) with full provenance
- Computes financial ratios across 11 companies, 2021-2025
- Validates data against accounting identities AND published annual reports
- Auto-classifies new companies using taxonomy presentation linkbase

**3. Current status**
V1 complete: 11 companies, 311+ concepts mapped, ratio engine live

**4. The non-obvious technical bits (interview gold)**
- Taxonomy package resolution: why `PackageManager.addPackage()` matters
- IAS 21 FX design: closing rate for balance sheet, average for P&L
- Human-in-the-loop: why extension tags need review but standard tags don't
- Database-as-source-of-truth: why nothing is stored as a snapshot

**5. Known limitations (intellectual honesty)**
- Dimensional/segment facts not yet loaded
- FX conversion not yet built (ratios are currency-neutral in the meantime)
- Sign conventions partially handled

Save, then:
```
git add README.md
git commit -m "Rewrite README: accurate V1 status, architecture, technical decisions"
git push
```

---

## BLOCK 3 — Fix STATEMENT_ORDER (~1 hour)

Run these and screenshot the income statement section:
```
python scripts\07_generate_statements.py --company "LVMH"
python scripts\07_generate_statements.py --company "Danone"
```

Send me the screenshots. The line ordering will be wrong for non-L'Oreal
companies because `STATEMENT_ORDER` is hardcoded from L'Oreal's structure.
I'll fix it to use the presentation linkbase order instead.

---

## BLOCK 4 — Company universe scanner (~1 hour)

Ask me to build `13_scan_universe.py` at the start of this block.

This lets you discover companies you didn't know existed:
```
python scripts\13_scan_universe.py --country FR
```

Output: all French ESEF filers not yet in your database, with most
recent filing date. You pick which to add — no more manual searching.

---

## BLOCK 5 — Rerun ratios + commit everything (~30 min)

After Block 1 closes the unmapped gap, rerun the ratio engine so the
Excel output reflects the improved data:
```
python scripts\11_ratio_engine.py
```

Then commit:
```
git add .
git commit -m "Monday: close unmapped gaps, update ratios, README, STATEMENT_ORDER"
git push
```

---

## REALISTIC EXPECTATIONS

If you do only Block 1 and Block 2, that's already a genuinely good day:
- Unmapped gap closed = more complete data = better ratios
- README updated = project presents correctly to the world

Blocks 3-5 are improvements. Block 1 and 2 are corrections.

---

## KEY RULE FOR BLOCK 1

**Do not skip the `--reset-facts` flag when reloading.**
Without it, you'll get duplicate rows again (the UNIQUE constraint
will reject them with an error, which is better than silent duplication,
but still means the reload fails halfway).

---

## IF YOU GET STUCK

Say which block and step you're on, send a screenshot.
Say **"let's revise"** if you want to test your understanding of
what we've built before continuing.
