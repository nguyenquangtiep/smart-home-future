"""Account administration: ``python -m smarthome.auth <command>``.

Commands:
  create-owner --home HOME --email EMAIL --name NAME   first owner of an existing home
  set-password --email EMAIL                           new password, revokes all sessions
  unlock --email EMAIL                                 clear failed-login throttling
  disable-user --email EMAIL                           disable the account, revoke sessions

Passwords are read from a hidden prompt, or from stdin with --password-stdin. There is no
--password option: command lines end up in shell history and process listings.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from smarthome.auth import passwords
from smarthome.auth.service import email_digest
from smarthome.auth.store import (
    EmailInUseError,
    HomeNotFoundError,
    PostgresAuthStore,
    UserNotFoundError,
)
from smarthome.core.config import get_settings
from smarthome.domain.state import utc_now


class _RefusePassword(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, *args: object, **kwargs: object) -> None:
        parser.exit(2, "refusing: passwords are never accepted as arguments; use the prompt\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m smarthome.auth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-owner")
    create.add_argument("--home", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    for name in ("create-owner", "set-password"):
        target = create if name == "create-owner" else sub.add_parser(name)
        if name == "set-password":
            target.add_argument("--email", required=True)
        target.add_argument("--password-stdin", action="store_true")
        target.add_argument("--password", action=_RefusePassword, nargs="?", help=argparse.SUPPRESS)
    for name in ("unlock", "disable-user"):
        sub.add_parser(name).add_argument("--email", required=True)
    return parser


def _read_password(
    from_stdin: bool, stdin: TextIO, prompt: Callable[[str], str], email: str
) -> str | None:
    if from_stdin:
        raw = stdin.readline().rstrip("\r\n")
    else:
        raw = prompt("Mật khẩu mới: ")
        if prompt("Nhập lại mật khẩu: ") != raw:
            print("refusing: the two passwords differ", file=sys.stderr)
            return None
    try:
        return passwords.check_policy(raw, email)
    except passwords.WeakPasswordError as exc:
        print(f"refusing: {exc.reason}", file=sys.stderr)
        return None


def main(
    argv: list[str] | None = None,
    stdin: TextIO | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    if settings.database_url is None:
        print("SMARTHOME_DATABASE_URL is not set", file=sys.stderr)
        return 2
    dsn = settings.database_url.get_secret_value()
    password: str | None = None
    if args.command in {"create-owner", "set-password"}:
        password = _read_password(args.password_stdin, stdin or sys.stdin, prompt, args.email)
        if password is None:
            return 2
    return asyncio.run(_run(dsn, args, password))


async def _run(dsn: str, args: argparse.Namespace, password: str | None) -> int:
    store = await PostgresAuthStore.open(dsn)
    now = utc_now()
    try:
        if args.command == "create-owner":
            assert password is not None
            user_id = await store.create_owner(
                args.home, args.email, args.name, await passwords.hash_password(password), now
            )
            print(f"created owner {user_id} for home {args.home}")
        elif args.command == "set-password":
            assert password is not None
            await store.set_password(args.email, await passwords.hash_password(password), now)
            print("password set; all sessions revoked")
        elif args.command == "unlock":
            cleared = await store.unlock(args.email, email_digest(args.email), now)
            print(f"cleared {cleared} failed attempt(s)")
        elif args.command == "disable-user":
            await store.disable_user(args.email, now)
            print("user disabled; all sessions revoked")
    except HomeNotFoundError:
        print(f"refusing: home {args.home!r} does not exist", file=sys.stderr)
        return 2
    except EmailInUseError:
        print("refusing: that email already has an account", file=sys.stderr)
        return 2
    except UserNotFoundError:
        print("refusing: no such user", file=sys.stderr)
        return 2
    finally:
        await store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
