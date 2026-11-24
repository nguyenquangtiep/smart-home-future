"""S1-05: ControlService asks a MembershipSource every time (AC-07-11, AC-07-20; DR-H08)."""

import asyncio
from collections.abc import Awaitable, Callable

from smarthome.control.execution import CommandStatus, ConfirmationError, ControlService
from smarthome.control.membership import StaticMembershipSource
from smarthome.control.policy import Actor, CommandRequest
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain.capabilities import CapabilityKind, Role
from smarthome.domain.models import Member
from smarthome.gateway.simulator import SimulatorGateway

OWNER = Actor(user_id="user-owner", home_id=HOME_ID, role=Role.OWNER)
MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
HEATER_ON = CommandRequest(
    device_id="dev-water-heater", capability=CapabilityKind.ON_OFF, value=True
)
SECOND_OWNER = Member(
    user_id="user-owner-2", home_id=HOME_ID, display_name="Chủ nhà 2", role=Role.OWNER
)


def run(test: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(test())


async def _setup(**kwargs: object) -> tuple[ControlService, StaticMembershipSource]:
    sim = SimulatorGateway(latency_s=0.02)
    home, members, devices = build_demo_home()
    source = StaticMembershipSource([*members, SECOND_OWNER])
    control = ControlService(
        [home], [], devices, {"simulator": sim}, verify_timeout_s=0.2, membership=source, **kwargs
    )
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)
    return control, source


def test_static_source_matches_the_old_member_dict() -> None:
    """Regression: passing ``members`` still works and answers like the old dict."""

    async def body() -> None:
        home, members, devices = build_demo_home()
        control = ControlService([home], members, devices, {"simulator": SimulatorGateway()})
        for member in members:
            assert await control.member(HOME_ID, member.user_id) == member
        assert await control.member(HOME_ID, "stranger") is None
        assert await control.member("other-home", "user-owner") is None

    run(body)


def test_member_lookup_reflects_changes_immediately() -> None:
    async def body() -> None:
        control, source = await _setup()
        assert (await control.member(HOME_ID, "user-member")).role is Role.MEMBER  # type: ignore[union-attr]
        source.set(
            Member(user_id="user-member", home_id=HOME_ID, display_name="m", role=Role.GUEST)
        )
        assert (await control.member(HOME_ID, "user-member")).role is Role.GUEST  # type: ignore[union-attr]
        source.remove(HOME_ID, "user-member")
        assert await control.member(HOME_ID, "user-member") is None

    run(body)


async def _pending(control: ControlService):  # noqa: ANN202
    record = await control.submit(MEMBER, HEATER_ON)
    assert record.status is CommandStatus.AWAITING_CONFIRMATION
    return record


def test_demoted_approver_cannot_approve() -> None:
    async def body() -> None:
        control, source = await _setup()
        record = await _pending(control)
        # user-owner-2 was an owner when the request arrived; demoted before the click.
        stale_owner = OWNER.model_copy(update={"user_id": "user-owner-2"})
        source.set(SECOND_OWNER.model_copy(update={"role": Role.MEMBER}))
        try:
            await control.confirm(stale_owner, record.id, approve=True)
        except ConfirmationError as exc:
            assert exc.code == "FORBIDDEN_ROLE"
        else:
            raise AssertionError("a demoted owner must not approve")
        # The command is still pending and the device never received anything.
        assert (await control.get_command(HOME_ID, record.id)).status is (
            CommandStatus.AWAITING_CONFIRMATION
        )
        assert control.state.observation("dev-water-heater", CapabilityKind.ON_OFF).value is False  # type: ignore[union-attr]

    run(body)


def test_removed_approver_cannot_approve() -> None:
    async def body() -> None:
        control, source = await _setup()
        record = await _pending(control)
        source.remove(HOME_ID, "user-owner")
        try:
            await control.confirm(OWNER, record.id, approve=True)
        except ConfirmationError as exc:
            assert exc.code == "FORBIDDEN_ROLE"
        else:
            raise AssertionError("a removed owner must not approve")

    run(body)


def test_removed_requester_is_rejected_on_approval() -> None:
    async def body() -> None:
        control, source = await _setup()
        record = await _pending(control)
        source.remove(HOME_ID, "user-member")
        result = await control.confirm(OWNER, record.id, approve=True)
        assert result.status is CommandStatus.REJECTED
        assert result.reasons[0].code == "REQUESTER_REMOVED"
        assert control.state.observation("dev-water-heater", CapabilityKind.ON_OFF).value is False  # type: ignore[union-attr]

    run(body)


def test_demoted_requester_is_judged_by_the_new_role() -> None:
    async def body() -> None:
        control, source = await _setup()
        record = await _pending(control)
        source.set(
            Member(user_id="user-member", home_id=HOME_ID, display_name="m", role=Role.GUEST)
        )
        result = await control.confirm(OWNER, record.id, approve=True)
        assert result.status is CommandStatus.REJECTED
        assert result.reasons[0].code == "FORBIDDEN_ROLE"

    run(body)


def test_unchanged_membership_still_approves() -> None:
    async def body() -> None:
        control, _ = await _setup()
        record = await _pending(control)
        result = await control.confirm(OWNER, record.id, approve=True)
        assert result.status in {CommandStatus.DISPATCHED, CommandStatus.SUCCEEDED}

    run(body)
