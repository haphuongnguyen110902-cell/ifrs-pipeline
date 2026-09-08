-- Linked 3-statement projection results. Written by
-- 21_three_statement_model.py. One row per (company, base_year,
-- forecast_year) - base_year identifies WHICH run's starting point this
-- row belongs to, since re-running with a later base_year (as more
-- historical data is loaded) shouldn't silently overwrite an older run's
-- projection from a different starting point.

CREATE TABLE IF NOT EXISTS three_statement_projection (
    projection_id            SERIAL PRIMARY KEY,
    company                  TEXT NOT NULL,
    base_year                INTEGER NOT NULL,
    forecast_year             INTEGER NOT NULL,
    growth_assumption        NUMERIC,
    interest_rate_assumption NUMERIC,
    revenue                  NUMERIC,
    ebit                     NUMERIC,
    interest_expense         NUMERIC,
    net_income               NUMERIC,
    dividends                NUMERIC,
    payout_ratio_assumption  NUMERIC,
    fcf                      NUMERIC,
    net_debt_end              NUMERIC,
    computed_at               TIMESTAMP DEFAULT now(),
    UNIQUE(company, base_year, forecast_year)
);

CREATE INDEX IF NOT EXISTS idx_three_statement_company ON three_statement_projection(company);

-- Added after the table already existed from an earlier run - IF NOT
-- EXISTS makes this safe to re-run on an existing table.
ALTER TABLE three_statement_projection ADD COLUMN IF NOT EXISTS dividends NUMERIC;
ALTER TABLE three_statement_projection ADD COLUMN IF NOT EXISTS payout_ratio_assumption NUMERIC;
