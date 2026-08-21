-- Ratio table: computed metrics stored alongside raw facts.
-- Computed at query time from fact_value, stored here so downstream
-- tools (dashboard, DCF) can read them directly without recomputing.
--
-- Design decisions:
--   - company_id + year + ratio_name is the natural key
--   - value is NULL when inputs were missing (not zero - there's a difference)
--   - is_currency_neutral flags ratios where FX doesn't matter (margins,
--     coverage) vs ones where it does (absolute EBITDA) - important because
--     cross-company comparison is only valid for currency-neutral ratios
--     until FX conversion is built
--   - source_concepts records which normalized_names fed this ratio,
--     so a bad mapping can be traced back to its source

CREATE TABLE IF NOT EXISTS ratio (
    ratio_id            SERIAL PRIMARY KEY,
    company_id          INTEGER REFERENCES company(company_id),
    year                INTEGER NOT NULL,
    ratio_name          TEXT NOT NULL,          -- e.g. 'gross_margin'
    display_label       TEXT NOT NULL,          -- e.g. 'Gross Margin'
    value               NUMERIC,               -- NULL means inputs were absent
    is_currency_neutral BOOLEAN DEFAULT TRUE,   -- TRUE for margins/ratios
    currency            TEXT,                   -- NULL for currency-neutral
    source_concepts     TEXT[],                 -- which concepts fed this
    computed_at         TIMESTAMP DEFAULT now(),
    UNIQUE(company_id, year, ratio_name)
);

CREATE INDEX IF NOT EXISTS idx_ratio_company ON ratio(company_id);
CREATE INDEX IF NOT EXISTS idx_ratio_name ON ratio(ratio_name);
