-- Credit profile classification per company/year, built on Net Debt /
-- TRUE EBITDA trajectory. Written by 24_credit.py.
--
-- WHY "TRUE EBITDA" AND NOT THE EXISTING net_debt_ebitda_proxy RATIO:
-- 11_ratio_engine.py's "net_debt_ebitda_proxy" column (display label
-- "Net Debt vs Op. Profit") is actually Net Debt / EBIT, not Net Debt /
-- EBITDA, despite its name - found while building this script, not
-- assumed. Real-world credit thresholds are calibrated to EBITDA, and
-- EBIT understates EBITDA by the D&A add-back, so reusing that column
-- here would systematically overstate leverage. This table computes
-- Net Debt / _ebitda (= EBIT + D&A, already computed in
-- 11_ratio_engine.py for exactly this purpose) directly instead.
--
-- Design decisions:
--   - company + year (a TRAJECTORY, not a single snapshot) - unlike the
--     other valuation-layer tables (dcf_valuation, valuation), this is
--     explicitly meant to show a multi-year path, per ROADMAP.md Phase 8.
--   - is_da_fallback flags any year where _da_total was 0 (no D&A tag
--     matched for that company - see 11_ratio_engine.py) - the resulting
--     ratio for that year uses EBITDA=EBIT and OVERSTATES true leverage.
--     Never hidden, always shown alongside the number it affects.
--   - band/trend are a SIMPLE, fixed, sector-agnostic classification -
--     explicitly not a real credit rating (no industry/coverage/
--     qualitative factors). Same "sanity-checked, not statistically
--     derived, revisit once sector-relative" caveat already applied to
--     15_forensics.py's thresholds.

CREATE TABLE IF NOT EXISTS credit_profile (
    credit_profile_id  SERIAL PRIMARY KEY,
    company            TEXT NOT NULL,
    year               INTEGER NOT NULL,
    net_debt           NUMERIC,
    ebitda             NUMERIC,
    net_debt_ebitda    NUMERIC,
    is_da_fallback     BOOLEAN DEFAULT FALSE,
    band               TEXT,
    yoy_change         NUMERIC,
    trend              TEXT,
    computed_at        TIMESTAMP DEFAULT now(),
    UNIQUE(company, year)
);

CREATE INDEX IF NOT EXISTS idx_credit_profile_company ON credit_profile(company);
