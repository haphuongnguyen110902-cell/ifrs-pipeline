-- Trading comps table: EV/EBITDA, EV/Sales, P/E per company, all
-- converted to EUR. Written by 19_valuation.py.
--
-- Design decisions:
--   - company + year is the natural key (year = the fiscal year the
--     fundamentals came from, NOT the year market cap was fetched -
--     market cap is always "today", so re-running this script updates
--     the same row's market_cap_eur/ev_eur/multiples without creating a
--     new "year")
--   - Every monetary column is already in EUR - no raw local-currency
--     columns are stored here, unlike fact_value which deliberately
--     keeps the original currency. This table is a DERIVED, point-in-time
--     comps snapshot, not a source-of-truth filing record.
--   - pe is NULL when net income is negative or missing (never a
--     meaningless negative P/E)

CREATE TABLE IF NOT EXISTS valuation (
    valuation_id    SERIAL PRIMARY KEY,
    company         TEXT NOT NULL,
    year            INTEGER NOT NULL,
    ticker          TEXT,
    market_cap_eur  NUMERIC,
    net_debt_eur    NUMERIC,
    ev_eur          NUMERIC,
    revenue_eur     NUMERIC,
    ebitda_eur      NUMERIC,
    net_income_eur  NUMERIC,
    ev_ebitda       NUMERIC,
    ev_sales        NUMERIC,
    pe              NUMERIC,
    computed_at     TIMESTAMP DEFAULT now(),
    UNIQUE(company, year)
);

CREATE INDEX IF NOT EXISTS idx_valuation_company ON valuation(company);
