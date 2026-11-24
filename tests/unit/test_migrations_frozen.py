"""Applied migrations are history: they must never be edited (AC-06-12). Change the schema with a
new migration instead. Line endings are normalised so Windows and Linux checkouts agree."""

import hashlib

import pytest

from smarthome.persistence.migrate import MIGRATIONS_DIR, discover

# sha256 of each file's text with "\n" newlines.
FROZEN = {
    "0001_commands_audit.up.sql": (
        "27c9e99d81741ccde6e5d99756aa8e7e9cafb09b7213a1a1b1c3716b43e75c69"
    ),
    "0001_commands_audit.down.sql": (
        "592ee97cd74a6788a2be3822f9e43c2dfe537484167dd56d35d955b31b588335"
    ),
}


@pytest.mark.parametrize(("name", "digest"), sorted(FROZEN.items()))
def test_migration_0001_is_unchanged(name: str, digest: str) -> None:
    text = (MIGRATIONS_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode()).hexdigest() == digest


def test_migrations_are_contiguous_and_reversible() -> None:
    migrations = discover()
    assert [m.version for m in migrations] == [1, 2, 3]
    assert [m.name for m in migrations] == ["commands_audit", "home_config", "auth"]
    assert all(m.down_sql.strip() for m in migrations)
