-- Migration 001: add company_id (integer FK) to the five TEXT-keyed
-- analysis tables written by 19_valuation.py, 22_dcf.py, 23_market_risk.py,
-- 24_credit.py, 21_three_statement_model.py. See PLAN.md WP1.
--
-- APPROACH: additive, not destructive (project rule #5 / PLAN.md ground
-- rule #4). company_id is added ALONGSIDE the existing `company` TEXT
-- column, backfilled by name, and given a real FK + its own UNIQUE
-- constraint. The existing `company` TEXT column and its existing
-- UNIQUE(company, ...) constraint are DELIBERATELY NOT dropped or removed
-- here - PLAN.md's own wording said "replace", but keeping both is what
-- makes this migration actually reversible (PLAN.md's own rollback step
-- for this WP says "drop the added columns/constraints; company TEXT
-- still carries everything" - that only works if the old constraint is
-- still there). Dropping the TEXT column/constraint is a separate,
-- later cleanup once every reader/writer is confirmed migrated - not
-- part of this file.
--
-- IDEMPOTENT: IF NOT EXISTS / guarded UPDATE / a DO block that aborts the
-- whole transaction if the backfill guard fails, so it is safe to run
-- more than once and safe to run against a database where it's already
-- partially applied.
--
-- HOW TO RUN AND VERIFY:
--   1. Confirm tests/test_baseline_regression.py passes BEFORE running
--      this file (regenerate tests/baseline/*.csv first if it's been a
--      while since WP0's snapshot was taken).
--   2. Run this file once against the live DB.
--   3. Run tests/test_baseline_regression.py again - must still pass.
--      It snapshot-compares each table's existing rows, so it will catch
--      a mismatched backfill.
--   4. Run tests/test_migration_company_id.py - asserts every row in all
--      five tables has a non-null company_id and that the FK exists.

BEGIN;

-- valuation ------------------------------------------------------------
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS company_id INTEGER;

UPDATE valuation v
SET company_id = c.company_id
FROM company c
WHERE c.name = v.company
  AND v.company_id IS NULL;

-- dcf_valuation ----------------------------------------------------------
ALTER TABLE dcf_valuation ADD COLUMN IF NOT EXISTS company_id INTEGER;

UPDATE dcf_valuation v
SET company_id = c.company_id
FROM company c
WHERE c.name = v.company
  AND v.company_id IS NULL;

-- market_risk --------------------------------------------------------------
ALTER TABLE market_risk ADD COLUMN IF NOT EXISTS company_id INTEGER;

UPDATE market_risk v
SET company_id = c.company_id
FROM company c
WHERE c.name = v.company
  AND v.company_id IS NULL;

-- credit_profile -----------------------------------------------------------
ALTER TABLE credit_profile ADD COLUMN IF NOT EXISTS company_id INTEGER;

UPDATE credit_profile v
SET company_id = c.company_id
FROM company c
WHERE c.name = v.company
  AND v.company_id IS NULL;

-- three_statement_projection -------------------------------------------------
ALTER TABLE three_statement_projection ADD COLUMN IF NOT EXISTS company_id INTEGER;

UPDATE three_statement_projection v
SET company_id = c.company_id
FROM company c
WHERE c.name = v.company
  AND v.company_id IS NULL;

-- GUARD: per PLAN.md WP1 - "must return zero rows. If it doesn't, a name
-- in an analysis table has no match in company - investigate before
-- proceeding, do not force it." Enforced here as a hard abort, not just
-- a manual check, so this file stays safe to re-run in a future session
-- that doesn't remember to check by hand first.
DO $$
DECLARE
    missing_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO missing_count FROM (
        SELECT company FROM valuation WHERE company_id IS NULL
        UNION ALL
        SELECT company FROM dcf_valuation WHERE company_id IS NULL
        UNION ALL
        SELECT company FROM market_risk WHERE company_id IS NULL
        UNION ALL
        SELECT company FROM credit_profile WHERE company_id IS NULL
        UNION ALL
        SELECT company FROM three_statement_projection WHERE company_id IS NULL
    ) AS unmatched;

    IF missing_count > 0 THEN
        RAISE EXCEPTION
            'company_id backfill guard failed: % row(s) across the five tables have no matching company.name - investigate before proceeding, do not force it.',
            missing_count;
    END IF;
END $$;

-- Guard passed for all five tables - safe to enforce NOT NULL and add
-- the FK + a dedicated UNIQUE constraint on the new key.
--
-- NOTE ON IDEMPOTENCY: unlike ADD COLUMN, PostgreSQL's ADD CONSTRAINT has
-- no IF NOT EXISTS form - running a bare ADD CONSTRAINT twice raises
-- DuplicateObject (confirmed by actually running this file twice while
-- testing it). Each constraint is added inside a DO block that checks
-- pg_constraint first, so the whole file is genuinely safe to re-run.

ALTER TABLE valuation ALTER COLUMN company_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_valuation_company') THEN
        ALTER TABLE valuation ADD CONSTRAINT fk_valuation_company
            FOREIGN KEY (company_id) REFERENCES company(company_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'valuation_company_id_year_key') THEN
        ALTER TABLE valuation ADD CONSTRAINT valuation_company_id_year_key
            UNIQUE (company_id, year);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_valuation_company_id ON valuation(company_id);

ALTER TABLE dcf_valuation ALTER COLUMN company_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_dcf_valuation_company') THEN
        ALTER TABLE dcf_valuation ADD CONSTRAINT fk_dcf_valuation_company
            FOREIGN KEY (company_id) REFERENCES company(company_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'dcf_valuation_company_id_base_year_key') THEN
        ALTER TABLE dcf_valuation ADD CONSTRAINT dcf_valuation_company_id_base_year_key
            UNIQUE (company_id, base_year);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_dcf_valuation_company_id ON dcf_valuation(company_id);

ALTER TABLE market_risk ALTER COLUMN company_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_market_risk_company') THEN
        ALTER TABLE market_risk ADD CONSTRAINT fk_market_risk_company
            FOREIGN KEY (company_id) REFERENCES company(company_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'market_risk_company_id_benchmark_key') THEN
        ALTER TABLE market_risk ADD CONSTRAINT market_risk_company_id_benchmark_key
            UNIQUE (company_id, benchmark);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_market_risk_company_id ON market_risk(company_id);

ALTER TABLE credit_profile ALTER COLUMN company_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_credit_profile_company') THEN
        ALTER TABLE credit_profile ADD CONSTRAINT fk_credit_profile_company
            FOREIGN KEY (company_id) REFERENCES company(company_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_profile_company_id_year_key') THEN
        ALTER TABLE credit_profile ADD CONSTRAINT credit_profile_company_id_year_key
            UNIQUE (company_id, year);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_credit_profile_company_id ON credit_profile(company_id);

ALTER TABLE three_statement_projection ALTER COLUMN company_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_three_statement_projection_company') THEN
        ALTER TABLE three_statement_projection ADD CONSTRAINT fk_three_statement_projection_company
            FOREIGN KEY (company_id) REFERENCES company(company_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'three_statement_projection_company_id_base_year_forecast_year_key') THEN
        ALTER TABLE three_statement_projection ADD CONSTRAINT three_statement_projection_company_id_base_year_forecast_year_key
            UNIQUE (company_id, base_year, forecast_year);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_three_statement_projection_company_id ON three_statement_projection(company_id);

COMMIT;

-- ROLLBACK (manual, run separately if ever needed - not part of the
-- forward migration and not idempotent-guarded, since it's meant to be
-- read and run deliberately, not re-run blindly):
--
-- ALTER TABLE valuation DROP CONSTRAINT IF EXISTS fk_valuation_company, DROP CONSTRAINT IF EXISTS valuation_company_id_year_key, DROP COLUMN IF EXISTS company_id;
-- ALTER TABLE dcf_valuation DROP CONSTRAINT IF EXISTS fk_dcf_valuation_company, DROP CONSTRAINT IF EXISTS dcf_valuation_company_id_base_year_key, DROP COLUMN IF EXISTS company_id;
-- ALTER TABLE market_risk DROP CONSTRAINT IF EXISTS fk_market_risk_company, DROP CONSTRAINT IF EXISTS market_risk_company_id_benchmark_key, DROP COLUMN IF EXISTS company_id;
-- ALTER TABLE credit_profile DROP CONSTRAINT IF EXISTS fk_credit_profile_company, DROP CONSTRAINT IF EXISTS credit_profile_company_id_year_key, DROP COLUMN IF EXISTS company_id;
-- ALTER TABLE three_statement_projection DROP CONSTRAINT IF EXISTS fk_three_statement_projection_company, DROP CONSTRAINT IF EXISTS three_statement_projection_company_id_base_year_forecast_year_key, DROP COLUMN IF EXISTS company_id;
