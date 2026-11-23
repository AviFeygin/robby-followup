-- Common schema. Runs unchanged on SQLite and Postgres.
-- Every table carries shop_id as the first column of its primary key,
-- and every foreign key includes it, so a row cannot point across shops.

CREATE TABLE IF NOT EXISTS shops (
    shop_id          TEXT NOT NULL,
    org_id           TEXT NOT NULL,
    name             TEXT NOT NULL,
    sending_enabled  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (shop_id)
);

CREATE TABLE IF NOT EXISTS customers (
    shop_id     TEXT NOT NULL,
    id          TEXT NOT NULL,
    phone_e164  TEXT NOT NULL,
    name        TEXT NOT NULL,
    PRIMARY KEY (shop_id, id),
    UNIQUE (shop_id, phone_e164),
    FOREIGN KEY (shop_id) REFERENCES shops (shop_id)
);

CREATE TABLE IF NOT EXISTS quotes (
    shop_id          TEXT NOT NULL,
    id               TEXT NOT NULL,
    customer_id      TEXT NOT NULL,
    tech_name        TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    status           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_contact_at  TEXT,
    PRIMARY KEY (shop_id, id),
    FOREIGN KEY (shop_id, customer_id) REFERENCES customers (shop_id, id)
);

-- Events are append only. quote_id has no foreign key on purpose:
-- a webhook can arrive before the quote it refers to.
CREATE TABLE IF NOT EXISTS events (
    shop_id      TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    quote_id     TEXT,
    type         TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    channel      TEXT,
    direction    TEXT,
    raw_json     TEXT NOT NULL,
    PRIMARY KEY (shop_id, event_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    shop_id             TEXT NOT NULL,
    id                  TEXT NOT NULL,
    quote_id            TEXT NOT NULL,
    customer_id         TEXT NOT NULL,
    status              TEXT NOT NULL,
    status_reason       TEXT,
    tier                INTEGER NOT NULL,
    reason_code         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    framing             TEXT,
    body                TEXT NOT NULL,
    approved_body_hash  TEXT,
    written_by          TEXT NOT NULL,
    requires_owner      INTEGER NOT NULL DEFAULT 0,
    approver_note       TEXT,
    decided_by          TEXT,
    decided_at          TEXT,
    created_at          TEXT NOT NULL,
    allowed_json        TEXT NOT NULL DEFAULT '{}',
    signals_json        TEXT NOT NULL DEFAULT '{}',
    policy_version      TEXT NOT NULL,
    PRIMARY KEY (shop_id, id),
    FOREIGN KEY (shop_id, quote_id) REFERENCES quotes (shop_id, id),
    FOREIGN KEY (shop_id, customer_id) REFERENCES customers (shop_id, id)
);

-- One live draft per customer. A second engine run cannot create another.
CREATE UNIQUE INDEX IF NOT EXISTS drafts_one_live_per_customer
    ON drafts (shop_id, customer_id)
    WHERE status IN ('draft', 'approved', 'sending', 'failed');

CREATE TABLE IF NOT EXISTS offers (
    shop_id      TEXT NOT NULL,
    id           TEXT NOT NULL,
    draft_id     TEXT NOT NULL,
    quote_id     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value_cents  INTEGER NOT NULL,
    expires_at   TEXT,
    slot_ref     TEXT,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (shop_id, id),
    UNIQUE (shop_id, draft_id),
    FOREIGN KEY (shop_id, draft_id) REFERENCES drafts (shop_id, id)
);

CREATE TABLE IF NOT EXISTS outbox (
    shop_id              TEXT NOT NULL,
    id                   TEXT NOT NULL,
    draft_id             TEXT NOT NULL,
    idempotency_key      TEXT NOT NULL,
    to_phone             TEXT NOT NULL,
    body                 TEXT NOT NULL,
    channel              TEXT NOT NULL,
    status               TEXT NOT NULL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    provider_message_id  TEXT,
    last_error           TEXT,
    created_at           TEXT NOT NULL,
    sent_at              TEXT,
    PRIMARY KEY (shop_id, id),
    UNIQUE (shop_id, draft_id),
    UNIQUE (shop_id, idempotency_key),
    FOREIGN KEY (shop_id, draft_id) REFERENCES drafts (shop_id, id)
);

CREATE TABLE IF NOT EXISTS baselines (
    shop_id      TEXT NOT NULL,
    metric       TEXT NOT NULL,
    value        REAL NOT NULL,
    sample_n     INTEGER NOT NULL,
    source       TEXT NOT NULL,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (shop_id, metric)
);

CREATE TABLE IF NOT EXISTS decisions (
    shop_id         TEXT NOT NULL,
    id              TEXT NOT NULL,
    draft_id        TEXT NOT NULL,
    quote_id        TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    signals_json    TEXT NOT NULL,
    baselines_json  TEXT NOT NULL,
    action_json     TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    PRIMARY KEY (shop_id, id)
);
