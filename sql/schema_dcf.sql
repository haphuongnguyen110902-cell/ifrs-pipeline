-- DCF valuation results. Written by 22_dcf.py. One row per (company,
-- base_year) - re-running with the same base_year updates in place.

CREATE TABLE IF NOT EXISTS dcf_valuation (
    dcf_id                  SERIAL PRIMARY KEY,
    company                 TEXT NOT NULL,
    base_year               INTEGER NOT NULL,
    wacc                    NUMERIC,
    cost_of_equity          NUMERIC,
    after_tax_cost_of_debt  NUMERIC,
    enterprise_value        NUMERIC,
    equity_value            NUMERIC,
    pct_ev_from_terminal    NUMERIC,
    computed_at             TIMESTAMP DEFAULT now(),
    UNIQUE(company, base_year)
);
