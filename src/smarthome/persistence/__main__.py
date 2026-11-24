"""Database administration: ``python -m smarthome.persistence <command>``.

Commands:
  status                                   show current and latest schema version
  upgrade [--to N]                         apply pending migrations (back up first)
  downgrade --to N --confirm-data-loss     roll back migrations above N (destroys their data)
  purge                                    delete finished commands and audit past retention

Reads the connection string from SMARTHOME_DATABASE_URL (or .env).
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from smarthome.core.config import get_settings
from smarthome.domain.state import utc_now
from smarthome.persistence import migrate
from smarthome.persistence.postgres import PostgresCommandStore, connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smarthome.persistence", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    up = sub.add_parser("upgrade")
    up.add_argument("--to", type=int, default=None)
    down = sub.add_parser("downgrade")
    down.add_argument("--to", type=int, required=True)
    down.add_argument("--confirm-data-loss", action="store_true")
    sub.add_parser("purge")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.database_url is None:
        print("SMARTHOME_DATABASE_URL is not set", file=sys.stderr)
        return 2
    dsn = settings.database_url.get_secret_value()

    if args.command == "purge":
        return asyncio.run(
            _purge(dsn, settings.command_retention_days, settings.audit_retention_days)
        )

    with connect(dsn) as conn:
        if args.command == "status":
            print(f"current={migrate.current_version(conn)} latest={migrate.latest_version()}")
        elif args.command == "upgrade":
            print(f"applied={migrate.upgrade(conn, args.to)}")
        elif args.command == "downgrade":
            if not args.confirm_data_loss:
                print("refusing: downgrade drops data; pass --confirm-data-loss", file=sys.stderr)
                return 2
            print(f"reverted={migrate.downgrade(conn, args.to)}")
    return 0


async def _purge(dsn: str, command_days: int, audit_days: int) -> int:
    store = await PostgresCommandStore.open(dsn)
    try:
        now = utc_now()
        commands, audit = await store.purge(
            now - timedelta(days=command_days), now - timedelta(days=audit_days)
        )
        print(f"purged commands={commands} audit_events={audit}")
    finally:
        await store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
