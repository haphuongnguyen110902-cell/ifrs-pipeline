-- Migration 003: adds company.ticker_currency - the currency the
-- resolved ticker's market data actually quotes in (what TICKER_MAP's
-- second element encoded in scripts/19_valuation.py/22_dcf.py/
-- 23_market_risk.py). Found missing while wiring 26_entity_resolution.py
-- (PLAN.md WP4) into those three scripts - WP3b's original column list
-- (ticker/ticker_exchange/isin/ticker_source) didn't include it. Purely
-- additive, same pattern as migration_001/002.

BEGIN;

ALTER TABLE company ADD COLUMN IF NOT EXISTS ticker_currency TEXT;

COMMIT;
