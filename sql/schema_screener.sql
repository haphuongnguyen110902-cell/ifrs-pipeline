-- Universe screener view. One row per company, computed from tables
-- already owned by other modules (ratio, valuation, credit_profile,
-- forensics_flag) - this file defines no table of its own, only a VIEW
-- joining them, so the screener's landing page is a single query, not
-- one query per company. See webapp/app.py's ensure_screener_view() and
-- PLAN.md WP5.
--
-- Design decisions:
--   - "latest" per source table means the most recent YEAR that table
--     has for that company - each table's own latest year can differ
--     (e.g. valuation's latest fiscal year vs. credit_profile's), which
--     is expected and not a bug - DISTINCT ON ... ORDER BY year DESC
--     picks each table's own latest independently, same as every
--     load_*() function elsewhere in this app already does per table.
--   - Every "latest" CTE below filters to non-NULL values BEFORE
--     ranking by year - a real bug found running this against live
--     data, not assumed: Pernod Ricard has a 2025 `ratio` row for
--     operating_margin with value = NULL (its June 30 fiscal year end
--     leaves the numerically latest year empty - the exact case
--     21_three_statement_model.py's select_base_year_row() was already
--     built to handle, reintroduced here in a new place). Without the
--     filter, DISTINCT ON ... ORDER BY year DESC picks that empty 2025
--     row over 2024's real value - same bug class, same fix: skip NULLs
--     before picking "latest", don't just take the newest year blindly.
--   - forensics_flag counts are ALL-TIME (every year that company has a
--     flag for), not latest-year-only - a screener asking "which
--     companies have >= 2 HIGH flags" (the motivating example since
--     Phase 11/PLAN.md's Explicitly Deferred list) means "ever flagged
--     this badly", not just this year.
--   - LEFT JOINs throughout: a company missing one source (e.g. no DCF/
--     comps yet - see README's Known limitations) still gets a row here,
--     with NULLs for what's missing, not silently dropped from the
--     universe table entirely.

CREATE OR REPLACE VIEW company_latest_metrics AS
WITH latest_ratio AS (
    SELECT DISTINCT ON (company_id, ratio_name)
        company_id, ratio_name, value, year
    FROM ratio
    WHERE value IS NOT NULL
    ORDER BY company_id, ratio_name, year DESC
),
latest_valuation AS (
    SELECT DISTINCT ON (company_id)
        company_id, year, ev_ebitda
    FROM valuation
    WHERE ev_ebitda IS NOT NULL
    ORDER BY company_id, year DESC
),
latest_credit AS (
    SELECT DISTINCT ON (company_id)
        company_id, year, net_debt_ebitda, band, is_da_fallback
    FROM credit_profile
    WHERE net_debt_ebitda IS NOT NULL
    ORDER BY company_id, year DESC
),
flag_counts AS (
    SELECT
        company_id,
        COUNT(*) FILTER (WHERE severity = 'high') AS high_flag_count,
        COUNT(*) AS total_flag_count
    FROM forensics_flag
    GROUP BY company_id
)
SELECT
    c.company_id,
    c.name,
    c.country,
    c.sector AS sector_detail,
    c.sector_std,
    om.value AS operating_margin,
    roic.value AS roic,
    lv.ev_ebitda,
    lc.net_debt_ebitda,
    lc.band AS credit_band,
    lc.is_da_fallback AS credit_is_da_fallback,
    COALESCE(fc.high_flag_count, 0) AS high_flag_count,
    COALESCE(fc.total_flag_count, 0) AS total_flag_count
FROM company c
LEFT JOIN latest_ratio om   ON om.company_id = c.company_id AND om.ratio_name = 'operating_margin'
LEFT JOIN latest_ratio roic ON roic.company_id = c.company_id AND roic.ratio_name = 'roic'
LEFT JOIN latest_valuation lv ON lv.company_id = c.company_id
LEFT JOIN latest_credit lc     ON lc.company_id = c.company_id
LEFT JOIN flag_counts fc       ON fc.company_id = c.company_id;
