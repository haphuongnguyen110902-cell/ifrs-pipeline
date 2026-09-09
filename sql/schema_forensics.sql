-- Earnings-quality/leverage forensics flags, persisted per (company, year,
-- flag). Written by 15_forensics.py's save_to_db(). See PLAN.md WP2.
--
-- WHY THIS TABLE DIDN'T EXIST UNTIL NOW: 15_forensics.py's own docstring
-- used to say flags are "recomputed from the ratio table each run, cheap
-- enough at this dataset size that persisting a copy isn't needed" - true
-- at 11 companies, but webapp/app.py was importlib-loading the script and
-- recomputing on every page render (coupling the web layer to scripts/'s
-- directory layout), and with nothing stored, a query like "every company
-- with >= 2 HIGH flags" (the screener PLAN.md's WP5 wants) was impossible.
--
-- Design decisions (same pattern as sql/schema_credit.sql):
--   - company_id is a real FK, born this way from the start - unlike the
--     five tables migration_001_company_id.sql had to retrofit, this
--     table postdates that migration so there's no legacy TEXT column to
--     carry forward.
--   - UNIQUE(company_id, year, flag_id) - one row per flag actually
--     triggered for that company/year, not one row per company/year
--     (a single year can trigger zero, one, or several flags).
--   - No `company` TEXT column at all, deliberately - every other
--     analysis table added since WP1 should follow this table's pattern,
--     not the old one.

CREATE TABLE IF NOT EXISTS forensics_flag (
    forensics_flag_id SERIAL PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(company_id),
    year              INTEGER NOT NULL,
    flag_id           TEXT NOT NULL,
    label             TEXT,
    severity          TEXT CHECK (severity IN ('high', 'medium', 'low')),
    value             NUMERIC,
    detail            TEXT,
    what_to_check     TEXT,
    computed_at       TIMESTAMP DEFAULT now(),
    UNIQUE (company_id, year, flag_id)
);

CREATE INDEX IF NOT EXISTS idx_forensics_flag_company ON forensics_flag(company_id);
CREATE INDEX IF NOT EXISTS idx_forensics_flag_severity ON forensics_flag(severity);
