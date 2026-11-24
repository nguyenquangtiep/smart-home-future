"""Durable storage for commands, idempotency keys and audit events.

``MemoryCommandStore`` is for development and tests (not durable). ``PostgresCommandStore`` is
the durable store; its schema is managed by the versioned migrations in ``migrations/``.
"""
