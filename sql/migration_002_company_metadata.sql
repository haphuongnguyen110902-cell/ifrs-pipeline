-- Migration 002: extend `company` with the three additions PLAN.md WP3
-- specifies - a standardized sector for peer grouping, ticker/ISIN/LEI
-- columns for WP4 to populate, and a company-level fiscal-year-end.
-- Purely additive - no existing column is touched, renamed, or dropped.

BEGIN;

-- 3a. Standardized sector, alongside (not replacing) the existing free-text
-- `sector` column. `sector` stays exactly as-is - still the detail-level
-- text shown in the company header/sidebar filter. `sector_std` is a
-- machine-assigned, coarser grouping (yfinance's sector field, see
-- 19_valuation.py's populate_sector_std()) used specifically for peer
-- comparison, where the existing 9-distinct-strings-across-11-companies
-- free text was too granular for any group to reach 2 members.
ALTER TABLE company ADD COLUMN IF NOT EXISTS sector_std TEXT;
ALTER TABLE company ADD COLUMN IF NOT EXISTS sector_source TEXT;

-- 3b. Ticker + ISIN + LEI columns. Empty until WP4's entity-resolution
-- script populates them - `lei` already existed (schema.sql) and is left
-- alone here.
ALTER TABLE company ADD COLUMN IF NOT EXISTS ticker TEXT;
ALTER TABLE company ADD COLUMN IF NOT EXISTS ticker_exchange TEXT;
ALTER TABLE company ADD COLUMN IF NOT EXISTS isin TEXT;
ALTER TABLE company ADD COLUMN IF NOT EXISTS ticker_source TEXT;

-- 3c. Company-level fiscal-year-end, DERIVED from filing.fiscal_year_end
-- (which already exists, per-filing) rather than a second independent
-- source of the same fact - see PLAN.md WP3c's correction. Backfilled
-- below from each company's most recent filing with a non-null
-- fiscal_year_end; re-runnable (always recomputes from the same source).
ALTER TABLE company ADD COLUMN IF NOT EXISTS fiscal_year_end_month SMALLINT;
ALTER TABLE company ADD COLUMN IF NOT EXISTS fiscal_year_end_day SMALLINT;

UPDATE company c
SET fiscal_year_end_month = EXTRACT(MONTH FROM latest.fiscal_year_end)::SMALLINT,
    fiscal_year_end_day = EXTRACT(DAY FROM latest.fiscal_year_end)::SMALLINT
FROM (
    SELECT DISTINCT ON (company_id) company_id, fiscal_year_end
    FROM filing
    WHERE fiscal_year_end IS NOT NULL
    ORDER BY company_id, fiscal_year_end DESC
) latest
WHERE c.company_id = latest.company_id;

COMMIT;
