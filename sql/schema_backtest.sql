-- Backtest table: rolling-origin MAE/RMSE/BIAS/MAPE for each forecasting
-- method (CAGR, linreg), per company/ratio. Written by 17_backtest.py.
--
-- Design decisions:
--   - company_id + ratio_name + method is the natural key (one row per
--     method per ratio per company, aggregated across ALL rolling folds -
--     individual fold-level detail is not persisted, only the summary)
--   - is_winner is TRUE for whichever method had the lower MAE for that
--     company/ratio, computed at write time by 17_backtest.py - re-running
--     the backtest with more historical years can flip this
--   - mape can be NULL: excluded when too few folds had a large-enough
--     actual value to divide by (see THIN_ACTUAL_THRESHOLD in the script)
--   - n_thin_excluded records HOW MANY folds were skipped for MAPE so a
--     near-empty MAPE isn't silently trusted

CREATE TABLE IF NOT EXISTS backtest (
    backtest_id      SERIAL PRIMARY KEY,
    company_id       INTEGER REFERENCES company(company_id),
    ratio_name       TEXT NOT NULL,
    method           TEXT NOT NULL CHECK (method IN ('cagr', 'linreg')),
    n_folds          INTEGER NOT NULL,
    mae              NUMERIC,
    rmse             NUMERIC,
    bias             NUMERIC,
    mape             NUMERIC,
    n_thin_excluded  INTEGER DEFAULT 0,
    is_winner        BOOLEAN DEFAULT FALSE,
    confidence       TEXT,
    computed_at      TIMESTAMP DEFAULT now(),
    UNIQUE(company_id, ratio_name, method)
);

CREATE INDEX IF NOT EXISTS idx_backtest_company ON backtest(company_id);
CREATE INDEX IF NOT EXISTS idx_backtest_ratio ON backtest(ratio_name);

-- Added after the first version of this table was already created and run
-- against - IF NOT EXISTS makes this safe to re-run on an existing table.
ALTER TABLE backtest ADD COLUMN IF NOT EXISTS confidence TEXT;
