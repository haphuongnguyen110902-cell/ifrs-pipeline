-- Precedent transactions: curated, real, publicly-sourced M&A deals in
-- the same sectors as this project's universe. Written by
-- 20_precedents.py. See that script's module docstring for why these
-- are hand-curated rather than pulled from an automated feed.

CREATE TABLE IF NOT EXISTS precedent_transaction (
    precedent_id    SERIAL PRIMARY KEY,
    deal            TEXT NOT NULL,
    announced       TEXT NOT NULL,  -- stored as text (YYYY-MM-DD) since some deals only have an approximate/agreement date, not a clean DATE
    sector          TEXT,
    acquirer        TEXT,
    target          TEXT,
    ev_eur_m        NUMERIC,
    revenue_eur_m   NUMERIC,
    ebitda_eur_m    NUMERIC,
    ev_sales        NUMERIC,
    ev_ebitda       NUMERIC,
    ev_ebitda_is_estimate BOOLEAN DEFAULT FALSE,
    current_trading_comps_ev_sales_median NUMERIC,
    implied_control_premium_pct NUMERIC,
    source          TEXT,
    computed_at     TIMESTAMP DEFAULT now(),
    UNIQUE(deal, announced)
);
