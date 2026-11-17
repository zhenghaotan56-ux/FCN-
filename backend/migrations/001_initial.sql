PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS valuations (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    terms_hash TEXT NOT NULL,
    terms TEXT NOT NULL,
    market TEXT NOT NULL,
    result TEXT
);

CREATE INDEX IF NOT EXISTS valuations_user_created ON valuations(username, created_at DESC);

CREATE TABLE IF NOT EXISTS issuer_quotes (
    terms_hash TEXT NOT NULL,
    issuer TEXT NOT NULL,
    quoted_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    basis TEXT NOT NULL CHECK (basis IN ('annual', 'full_term')),
    coupon REAL NOT NULL,
    PRIMARY KEY (terms_hash, issuer, quoted_at)
);
