# Archive

One-off exploration and diagnostic scripts, not part of the active pipeline
(`run_pipeline.py` never calls any of these). Kept for history, not
deleted, because each one is real evidence of how a mapping or a bug was
actually found — several are referenced by name in `ROADMAP.md`'s build
log. None of these are covered by `tests/`, and none should be — they were
throwaway investigation tools, not production code.

**Early exploration** (V0, before the pipeline had a real shape):
- `01_explore_filing.py` — first look at one filing's raw facts via Arelle, unfiltered. Superseded by `09_batch_load.py`.
- `02_explore_concepts.py` — counted distinct concepts across a parsed filing.
- `03_build_mapping_draft.py` — generated the first-draft YAML concept mapping from concepts that appeared a clean 3 times.
- `04b_batch_scan_concepts.py` — pooled unmapped concepts across every downloaded filing in one pass. Superseded by `10_auto_classify.py` / `13_batch_prep.py`.
- `06_check_revenue.py` — ad hoc check of one company's loaded revenue figure.
- `_extend_mapping_batch.py` — one-time batch classification of new concepts across LVMH/Essity/Shell/Kering/Danone when the universe first expanded past L'Oréal.

**Coverage/duplicate diagnostics** (V0-V1 data-quality investigations):
- `_check_coverage.py` — which concepts from a filing are loaded vs. skipped as unmapped.
- `_diag_conflicts.py`, `_diag_duplicates.py` — found the duplicate-fact issue later fixed by the `UNIQUE(filing_id, period_id, concept_id)` constraint (see `ROADMAP.md`).
- `_fix_duplicates.py` — one-time cleanup once `_diag_duplicates.py` confirmed duplicates were safe to collapse (identical values).

**Ratio-engine tag investigations** (found which company used which XBRL tag for a concept, before the fix was written into `11_ratio_engine.py`'s `get_best()` fallback chains):
- `_diag_debt.py`, `_diag_pbt.py`, `_diag_roic.py` — net debt, profit-before-tax, and ROIC input coverage per company.
- `_diag_shell_ebitda.py` — the investigation behind the Shell D&A/EBITDA fix (see `ROADMAP.md` Phase 4).
- `_diag_shell_pernod.py` — Shell and Pernod Ricard-specific concept checks.
