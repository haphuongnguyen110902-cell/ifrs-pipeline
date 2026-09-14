-- WP7's own first requirement (SCOPE.md §3, PLAN.md WP7): a versioned,
-- rule-based universe of candidate companies - "member of its country's
-- main national index OR market cap > EUR 2bn" - not a hardcoded list.
-- The `as_of` date is what defends against a survivorship-bias objection:
-- a snapshot taken today can be re-taken later and compared, a static
-- list of today's winners cannot. Written by scripts/29_universe_membership.py.
--
-- Design decisions:
--   - This table only ever asserts POSITIVE inclusion. A company that
--     doesn't appear here is "not yet evaluated" (ticker didn't resolve
--     yet, hasn't cleared the threshold, or hasn't been scanned yet),
--     never "excluded" - there is deliberately no row saying "considered
--     and rejected", per this project's own "never guess / explicit
--     unknown" rule. Re-running the discovery script after ticker
--     resolution coverage improves picks up anyone missed the first
--     time without losing the earlier as_of snapshot.
--   - company_id is nullable, unlike forensics_flag/company_latest_metrics'
--     NOT NULL FKs - a universe candidate is discovered and qualified
--     BEFORE it is ever loaded into `company`/`fact_value`. It gets
--     backfilled once the pipeline actually loads that company.
--   - UNIQUE(entity_identifier, as_of) - the same company can appear in
--     multiple as_of snapshots (its qualification can change over time),
--     but only once per snapshot date.

CREATE TABLE IF NOT EXISTS universe_membership (
    universe_membership_id SERIAL PRIMARY KEY,
    entity_identifier TEXT NOT NULL,   -- LEI, from filings.xbrl.org / GLEIF
    name              TEXT NOT NULL,
    country           TEXT NOT NULL,
    inclusion_rule    TEXT NOT NULL CHECK (inclusion_rule IN ('market_cap_gt_2bn', 'national_index')),
    inclusion_detail  TEXT,             -- e.g. "market cap EUR 45.2bn (yfinance CA.PA, 2026-09-13)"
    ticker            TEXT,
    ticker_currency   TEXT,
    as_of             DATE NOT NULL,
    company_id        INTEGER REFERENCES company(company_id),
    discovered_at     TIMESTAMP DEFAULT now(),
    UNIQUE (entity_identifier, as_of)
);

CREATE INDEX IF NOT EXISTS idx_universe_membership_country ON universe_membership(country);
CREATE INDEX IF NOT EXISTS idx_universe_membership_company ON universe_membership(company_id);
CREATE INDEX IF NOT EXISTS idx_universe_membership_as_of ON universe_membership(as_of);
