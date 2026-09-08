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
    sector          TEXT,
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
    ev_ebitda_sector_median NUMERIC,
    n_peers_in_sector       INTEGER,
    implied_ev_from_peers   NUMERIC,
    premium_vs_peers_pct    NUMERIC,
    fwd_ev_ebitda           NUMERIC,
    fwd_ev_sales            NUMERIC,
    computed_at     TIMESTAMP DEFAULT now(),
    UNIQUE(company, year)
);

CREATE INDEX IF NOT EXISTS idx_valuation_company ON valuation(company);

-- Added after the table already existed and had been written to once -
-- IF NOT EXISTS makes this safe to re-run on an existing table.
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS sector TEXT;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS ev_ebitda_sector_median NUMERIC;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS n_peers_in_sector INTEGER;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS implied_ev_from_peers NUMERIC;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS premium_vs_peers_pct NUMERIC;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS fwd_ev_ebitda NUMERIC;
ALTER TABLE valuation ADD COLUMN IF NOT EXISTS fwd_ev_sales NUMERIC;
