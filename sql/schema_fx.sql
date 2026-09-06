-- FX rates table: EUR-based reference rates from the ECB, aggregated to
-- average (for P&L conversion) and closing (for balance-sheet conversion)
-- per currency per calendar year. Written by 18_fx_convert.py, read by
-- to_eur() / load_fx_lookup() for any script needing EUR conversion
-- (e.g. 19_valuation.py for trading comps).
--
-- Design decisions:
--   - currency + year is the natural key - EUR itself is never stored here
--     (to_eur() short-circuits EUR without a lookup)
--   - avg_rate and closing_rate both stored (not just one) because they
--     serve DIFFERENT line items (P&L vs balance sheet) per IAS 21's
--     current rate method - see 18_fx_convert.py's module docstring
--   - rates are CCY-per-EUR (ECB's native quotation convention), so
--     converting TO eur means DIVIDING by the stored rate, not multiplying
--   - n_observations records how many daily rates went into avg_rate, so
--     a year with suspiciously few observations (partial year, ECB outage)
--     is visible rather than silently trusted

CREATE TABLE IF NOT EXISTS fx_rates (
    fx_id           SERIAL PRIMARY KEY,
    currency        TEXT NOT NULL,
    year            INTEGER NOT NULL,
    avg_rate        NUMERIC NOT NULL,
    closing_rate    NUMERIC NOT NULL,
    n_observations  INTEGER,
    source          TEXT DEFAULT 'ECB',
    computed_at     TIMESTAMP DEFAULT now(),
    UNIQUE(currency, year)
);

CREATE INDEX IF NOT EXISTS idx_fx_currency_year ON fx_rates(currency, year);
