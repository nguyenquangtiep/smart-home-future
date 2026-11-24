-- 0003: users, memberships, server-side sessions, login throttling and security events
-- (ADR 0005, LLD P0-07). Sessions store only the SHA-256 of the token; auth_events is
-- append-only and reuses the guard function from 0001.

CREATE TABLE users (
    id                  text        PRIMARY KEY CHECK (id <> ''),
    email               text        NOT NULL CHECK (email <> '' AND email = btrim(email)),
    display_name        text        NOT NULL CHECK (display_name <> ''),
    password_hash       text,
    password_changed_at timestamptz,
    disabled_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));

CREATE TABLE memberships (
    home_id    text        NOT NULL REFERENCES homes (id) ON DELETE RESTRICT,
    user_id    text        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    role       text        NOT NULL CHECK (role IN ('owner', 'member', 'guest')),
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (home_id, user_id)
);
CREATE INDEX memberships_user_idx ON memberships (user_id);

CREATE TABLE sessions (
    id           text        PRIMARY KEY CHECK (id <> ''),
    token_hash   bytea       NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    user_id      text        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    csrf_token   text        NOT NULL CHECK (length(csrf_token) >= 32),
    created_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    CHECK (expires_at > created_at)
);
CREATE INDEX sessions_user_active_idx ON sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE login_attempts (
    id         bigserial   PRIMARY KEY,
    at         timestamptz NOT NULL,
    email_hash bytea       NOT NULL CHECK (octet_length(email_hash) = 32),
    ip         text,
    succeeded  boolean     NOT NULL
);
CREATE INDEX login_attempts_email_idx ON login_attempts (email_hash, at DESC) WHERE NOT succeeded;
CREATE INDEX login_attempts_ip_idx    ON login_attempts (ip, at DESC)         WHERE NOT succeeded;

CREATE TABLE auth_events (
    seq     bigserial   PRIMARY KEY,
    at      timestamptz NOT NULL,
    user_id text,
    home_id text,
    action  text        NOT NULL CHECK (action <> ''),
    detail  jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX auth_events_user_idx ON auth_events (user_id, seq DESC);
CREATE TRIGGER auth_events_append_only BEFORE UPDATE OR DELETE ON auth_events
    FOR EACH ROW EXECUTE FUNCTION smarthome_audit_guard();
