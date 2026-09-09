-- Market risk / return metrics per company, computed from daily price
-- history vs. a pan-European benchmark (STOXX Europe 600). Written by
-- 23_market_risk.py.
--
-- Design decisions:
--   - company + benchmark is the natural key, NOT company + a date/year -
--     this is a TRAILING-WINDOW measure ("what does this company's risk
--     profile look like as of today"), not a fiscal-year fact. Re-running
--     with a fresher price history updates the same row in place, same
--     "always want the latest" reasoning as dcf_valuation's UNIQUE
--     (company, base_year) - except there's no base_year equivalent here,
--     so it's just company + benchmark.
--   - beta/correlation are computed from raw price history against
--     `benchmark`, NOT read from yfinance's own opaque per-ticker beta
--     (which 22_dcf.py's WACC still uses, deliberately - see
--     23_market_risk.py's module docstring for why the two scripts make
--     different choices here).
--   - rolling_corr_mean/min/max summarize a rolling-window correlation
--     series rather than persisting every window's value - same
--     "aggregate, not fold-level detail" choice 17_backtest.py already
--     made for its rolling folds.

CREATE TABLE IF NOT EXISTS market_risk (
    market_risk_id       SERIAL PRIMARY KEY,
    company               TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    benchmark             TEXT NOT NULL,
    period_start          DATE,
    period_end            DATE,
    n_observations        INTEGER,
    annualized_volatility NUMERIC,
    benchmark_volatility  NUMERIC,
    sharpe_ratio          NUMERIC,
    beta                  NUMERIC,
    correlation           NUMERIC,
    rolling_corr_mean     NUMERIC,
    rolling_corr_min      NUMERIC,
    rolling_corr_max      NUMERIC,
    max_drawdown          NUMERIC,
    computed_at           TIMESTAMP DEFAULT now(),
    UNIQUE(company, benchmark)
);

CREATE INDEX IF NOT EXISTS idx_market_risk_company ON market_risk(company);
