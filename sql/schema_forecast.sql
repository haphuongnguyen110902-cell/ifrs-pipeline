-- Forecast table: CAGR and linear-regression projections computed from
-- the `ratio` table (itself computed from fact_value by 11_ratio_engine.py).
--
-- Design decisions (same spirit as schema_ratios.sql):
--   - company_id + ratio_name + method + forecast_year is the natural key
--   - method is 'cagr' or 'linreg' - kept side by side rather than picking
--     a winner, because V2's "forecasting model tournament" (roadmap) is
--     exactly about comparing methods, not committing to one early
--   - r_squared is NULL for CAGR (not a regression) and for linreg it's a
--     quick fit-quality signal, not a guarantee of forecast accuracy
--   - cagr is NULL for linreg rows, and vice versa - only one method's
--     parameter is populated per row

CREATE TABLE IF NOT EXISTS forecast (
    forecast_id     SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES company(company_id),
    ratio_name      TEXT NOT NULL,
    method          TEXT NOT NULL CHECK (method IN ('cagr', 'linreg')),
    base_year_start INTEGER NOT NULL,
    base_year_end   INTEGER NOT NULL,
    n_years         INTEGER NOT NULL,
    forecast_year   INTEGER NOT NULL,
    forecast_value  NUMERIC,
    cagr            NUMERIC,
    r_squared       NUMERIC,
    computed_at     TIMESTAMP DEFAULT now(),
    UNIQUE(company_id, ratio_name, method, forecast_year)
);

CREATE INDEX IF NOT EXISTS idx_forecast_company ON forecast(company_id);
CREATE INDEX IF NOT EXISTS idx_forecast_ratio ON forecast(ratio_name);
