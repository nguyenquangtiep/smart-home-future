"""Startup wiring: load the home configuration and build the control path from it (ADR 0006).

With a database the configuration comes from PostgreSQL and nothing else: an empty database or
invalid data stops the application with a clear message instead of falling back to the demo
home. Without a database (local/test only, enforced by ``Settings``) the demo home is held in
memory.
"""

from smarthome.control.execution import ControlService
from smarthome.control.membership import MembershipSource, StaticMembershipSource
from smarthome.core.config import Settings
from smarthome.core.logging import get_logger
from smarthome.demo_home import INITIAL_STATE, build_demo_config
from smarthome.domain.config import ConfigInvalidError
from smarthome.domain.models import HomeConfig
from smarthome.gateway.base import DeviceGateway
from smarthome.gateway.simulator import SimulatorGateway
from smarthome.persistence.base import CommandStore
from smarthome.persistence.config_store import (
    HomeConfigStore,
    MemoryHomeConfigStore,
    PostgresHomeConfigStore,
)

logger = get_logger("bootstrap")


class StartupError(RuntimeError):
    """The application cannot start safely. The message says what to do."""


async def open_config_store(settings: Settings) -> HomeConfigStore:
    if settings.database_url is None:
        logger.warning("SMARTHOME_DATABASE_URL not set: using the in-memory demo home")
        return MemoryHomeConfigStore([build_demo_config()[0]])
    return await PostgresHomeConfigStore.open(settings.database_url.get_secret_value())


async def load_configs(config_store: HomeConfigStore) -> list[HomeConfig]:
    try:
        configs = await config_store.load_all()
    except ConfigInvalidError as exc:
        raise StartupError(f"the stored home configuration is invalid: {exc}") from exc
    if not configs:
        raise StartupError(
            "no home is configured in the database. Run "
            "`python -m smarthome.persistence seed-demo` (local/test only) or import a "
            "configuration, then start the application again."
        )
    return configs


async def build_control(
    settings: Settings,
    store: CommandStore,
    configs: list[HomeConfig],
    membership: MembershipSource | None = None,
) -> ControlService:
    gateways: dict[str, DeviceGateway] = {}
    simulators: dict[str, SimulatorGateway] = {}
    for config in configs:
        for gw in config.gateways:
            if gw.kind != "simulator":
                raise StartupError(
                    f"gateway {gw.id!r} is of kind {gw.kind!r}, which is not implemented yet"
                )
            simulators[gw.id] = SimulatorGateway(latency_s=settings.sim_latency_s)
            gateways[gw.id] = simulators[gw.id]
    devices = [d for config in configs for d in config.devices]
    if membership is None:
        membership = StaticMembershipSource(build_demo_config()[1])
    control = ControlService(
        homes=[c.home for c in configs],
        members=[],
        membership=membership,
        devices=devices,
        gateways=gateways,
        store=store,
        verify_timeout_s=settings.verify_timeout_s,
        dispatch_timeout_s=settings.dispatch_timeout_s,
        confirmation_ttl_s=settings.confirmation_ttl_s,
        idempotency_ttl_s=settings.idempotency_ttl_s,
        clock_skew_s=settings.verification_clock_skew_s,
    )
    # 1) Close out commands a previous process left unfinished (never resent).
    recovered = await control.recover()
    # 2) Resync device state from the gateway (the source of truth after a restart). Only the
    #    simulator has a known initial state.
    for device in devices:
        sim = simulators.get(device.gateway)
        if sim is not None and device.id in INITIAL_STATE:
            await sim.report(device.id, INITIAL_STATE[device.id])
    # 3) Record what the devices now report for each interrupted command.
    await control.reconcile_interrupted()
    if recovered:
        logger.warning("recovered %d interrupted command(s)", len(recovered))
    return control
