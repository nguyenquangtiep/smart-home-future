"""Who belongs to which home, and with which role.

``ControlService`` asks a ``MembershipSource`` every time it needs a role (LLD P0-07, D-07-6), so
a demotion or removal takes effect on the next call, including the re-check made when an owner
approves a pending command. The static source keeps development and tests working without a
database; the PostgreSQL source lives in ``smarthome.auth``.
"""

from collections.abc import Iterable
from typing import Protocol

from smarthome.domain.models import Member


class MembershipSource(Protocol):
    async def get(self, home_id: str, user_id: str) -> Member | None:
        """The current membership, or None when the user is not (or no longer) in the home."""
        ...


class StaticMembershipSource:
    """A fixed list, for local/test. Not used with PostgreSQL."""

    def __init__(self, members: Iterable[Member]) -> None:
        self._members = {(m.home_id, m.user_id): m for m in members}

    async def get(self, home_id: str, user_id: str) -> Member | None:
        return self._members.get((home_id, user_id))

    def set(self, member: Member) -> None:
        """Test helper: add or replace a member."""
        self._members[(member.home_id, member.user_id)] = member

    def remove(self, home_id: str, user_id: str) -> None:
        self._members.pop((home_id, user_id), None)
