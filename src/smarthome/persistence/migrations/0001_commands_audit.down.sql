-- Rollback of 0001. DESTROYS all commands, idempotency keys and audit events: back up first.
DROP TABLE IF EXISTS audit_events;
DROP FUNCTION IF EXISTS smarthome_audit_guard();
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS commands;
