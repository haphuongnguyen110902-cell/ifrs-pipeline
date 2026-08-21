-- V0 schema: one company, full provenance from raw XBRL fact -> normalized metric

CREATE TABLE IF NOT EXISTS company (
    company_id      SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    lei             TEXT,              -- Legal Entity Identifier, if available
    country         TEXT,
    sector          TEXT,
    created_at      TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS filing (
    filing_id       SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES company(company_id),
    filing_date     DATE,
    fiscal_year_end DATE,
    taxonomy_version TEXT,             -- e.g. 'ESEF 2022' / 'IFRS 2023'
    source_url      TEXT,
    source_file     TEXT NOT NULL,     -- local path to the .zip/.xhtml package
    parsed_at       TIMESTAMP,
    UNIQUE(company_id, fiscal_year_end)
);

CREATE TABLE IF NOT EXISTS period (
    period_id       SERIAL PRIMARY KEY,
    filing_id       INTEGER REFERENCES filing(filing_id),
    start_date      DATE,               -- NULL for instant periods (balance sheet)
    end_date        DATE NOT NULL,
    period_type     TEXT NOT NULL CHECK (period_type IN ('instant', 'duration'))
);

CREATE TABLE IF NOT EXISTS ifrs_concept (
    concept_id      SERIAL PRIMARY KEY,
    normalized_name TEXT NOT NULL UNIQUE,  -- e.g. 'revenue', 'net_debt', 'ebit'
    statement       TEXT NOT NULL CHECK (statement IN ('income_statement','balance_sheet','cash_flow','other')),
    display_label   TEXT,
    display_order   INTEGER,
    sign_convention TEXT DEFAULT 'positive' -- 'positive' or 'negative' expected sign
);

CREATE TABLE IF NOT EXISTS concept_mapping (
    mapping_id      SERIAL PRIMARY KEY,
    concept_id      INTEGER REFERENCES ifrs_concept(concept_id),
    xbrl_tag        TEXT NOT NULL,          -- e.g. 'ifrs-full:Revenue'
    taxonomy_version TEXT,
    UNIQUE(xbrl_tag, taxonomy_version)
);

CREATE TABLE IF NOT EXISTS fact_value (
    value_id        SERIAL PRIMARY KEY,
    filing_id       INTEGER REFERENCES filing(filing_id),
    period_id       INTEGER REFERENCES period(period_id),
    concept_id      INTEGER REFERENCES ifrs_concept(concept_id),
    raw_xbrl_tag    TEXT NOT NULL,          -- original tag as it appeared in the filing
    value           NUMERIC,
    currency        TEXT,
    decimals        INTEGER,                -- XBRL 'decimals' attribute, for precision tracking
    context_ref     TEXT,                   -- original XBRL context id, for traceability/debugging
    dimensions      JSONB,                  -- any XBRL dimensions (segment, member) attached to this fact
    created_at      TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fact_value_filing ON fact_value(filing_id);
CREATE INDEX IF NOT EXISTS idx_fact_value_concept ON fact_value(concept_id);
CREATE INDEX IF NOT EXISTS idx_fact_value_period ON fact_value(period_id);
