-- Domains table holds both imported blocklist entries and manual overrides.
-- A domain can only be in ONE list at a time (PK enforces this), matching the
-- "adding to one list removes it from the other" behavior of the dashboard.
CREATE TABLE IF NOT EXISTS domains (
    domain     TEXT PRIMARY KEY,
    list_type  TEXT NOT NULL CHECK(list_type IN ('block', 'allow')),
    source     TEXT NOT NULL DEFAULT 'manual',
    added_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domains_list_type ON domains(list_type);

-- Query log for the dashboard. Trimmed periodically by db.py to stay bounded.
CREATE TABLE IF NOT EXISTS decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    domain         TEXT NOT NULL,
    verdict        TEXT NOT NULL,
    matched_domain TEXT,
    source         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts      ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_domain  ON decisions(domain);
CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(verdict);
