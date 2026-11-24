-- 0001: commands, idempotency keys and append-only audit events.

CREATE TABLE commands (
    id                      text PRIMARY KEY,
    home_id                 text        NOT NULL,
    device_id               text        NOT NULL,
    capability              text        NOT NULL,
    value                   jsonb       NOT NULL,
    actor_id                text        NOT NULL,
    origin                  text        NOT NULL,
    status                  text        NOT NULL CHECK (status IN (
                                'requested', 'awaiting_confirmation', 'rejected', 'dispatching',
                                'dispatched', 'succeeded', 'timed_out', 'failed', 'cancelled',
                                'expired', 'interrupted')),
    reasons                 jsonb       NOT NULL DEFAULT '[]'::jsonb,
    idempotency_key         text,
    created_at              timestamptz NOT NULL,
    updated_at              timestamptz NOT NULL,
    dispatched_at           timestamptz,
    completed_at            timestamptz,
    confirmation_expires_at timestamptz,
    confirmed_by            text,
    correlation_id          text,
    verification            text CHECK (verification IN ('correlated', 'state_match')),
    verified_value          jsonb
);

CREATE INDEX commands_home_created_idx ON commands (home_id, created_at DESC);
CREATE INDEX commands_unfinished_idx ON commands (status)
    WHERE status IN ('requested', 'dispatching', 'dispatched');
-- At most one command in flight per device capability.
CREATE UNIQUE INDEX commands_one_in_flight_idx ON commands (device_id, capability)
    WHERE status IN ('dispatching', 'dispatched');

CREATE TABLE idempotency_keys (
    home_id     text        NOT NULL,
    actor_id    text        NOT NULL,
    key         text        NOT NULL,
    fingerprint text        NOT NULL,
    command_id  text        NOT NULL REFERENCES commands (id) ON DELETE CASCADE
                            DEFERRABLE INITIALLY DEFERRED,
    expires_at  timestamptz NOT NULL,
    PRIMARY KEY (home_id, actor_id, key)
);

CREATE TABLE audit_events (
    seq        bigserial PRIMARY KEY,
    at         timestamptz NOT NULL,
    home_id    text        NOT NULL,
    actor_id   text,
    origin     text,
    action     text        NOT NULL,
    device_id  text,
    command_id text,  -- no FK: audit outlives purged commands
    detail     jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_events_home_seq_idx ON audit_events (home_id, seq DESC);

-- Append-only: UPDATE is always refused; DELETE only inside an explicit purge transaction
-- that sets "SET LOCAL smarthome.allow_audit_purge = 'on'".
CREATE FUNCTION smarthome_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('smarthome.allow_audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$;

CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION smarthome_audit_guard();
