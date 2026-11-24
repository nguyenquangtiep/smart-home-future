"""FastAPI application entry point: ``uvicorn smarthome.api.main:app``."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from smarthome import __version__
from smarthome.api.deps import get_graph
from smarthome.api.routes import agent, health, homes
from smarthome.control.execution import ControlService
from smarthome.core.config import Settings, get_settings
from smarthome.core.logging import configure_logging, get_logger
from smarthome.demo_home import INITIAL_STATE, build_demo_home
from smarthome.gateway.simulator import SimulatorGateway
from smarthome.persistence.base import CommandStore

logger = get_logger("api")


class UTF8JSONResponse(JSONResponse):
    # Explicit charset so clients like Windows PowerShell 5.1 decode Vietnamese text correctly.
    media_type = "application/json; charset=utf-8"


async def open_store(settings: Settings) -> CommandStore:
    if settings.database_url is None:
        from smarthome.persistence.memory import MemoryCommandStore

        logger.warning("SMARTHOME_DATABASE_URL not set: commands and audit are NOT durable")
        return MemoryCommandStore()
    from smarthome.persistence.postgres import PostgresCommandStore

    return await PostgresCommandStore.open(settings.database_url.get_secret_value())


async def build_control(settings: Settings, store: CommandStore) -> ControlService:
    """Wire the control path. Only the simulator gateway exists today (see task P0-09)."""
    if settings.device_gateway != "simulator":  # pragma: no cover - guarded by Settings type
        raise RuntimeError(f"gateway {settings.device_gateway!r} is not implemented")
    simulator = SimulatorGateway(latency_s=settings.sim_latency_s)
    home, members, devices = build_demo_home()
    control = ControlService(
        homes=[home],
        members=members,
        devices=devices,
        gateways={simulator.name: simulator},
        store=store,
        verify_timeout_s=settings.verify_timeout_s,
        dispatch_timeout_s=settings.dispatch_timeout_s,
        confirmation_ttl_s=settings.confirmation_ttl_s,
        idempotency_ttl_s=settings.idempotency_ttl_s,
        clock_skew_s=settings.verification_clock_skew_s,
    )
    # 1) Close out commands a previous process left unfinished (never resent).
    recovered = await control.recover()
    # 2) Resync device state from the gateway (the source of truth after a restart).
    for device_id, values in INITIAL_STATE.items():
        await simulator.report(device_id, values)
    # 3) Record what the devices now report for each interrupted command.
    await control.reconcile_interrupted()
    if recovered:
        logger.warning("recovered %d interrupted command(s)", len(recovered))
    return control


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.dev_identity_enabled and settings.environment == "production":
        raise RuntimeError("SMARTHOME_DEV_IDENTITY_ENABLED must not be used in production")
    if settings.database_url is None and settings.environment == "production":
        raise RuntimeError("SMARTHOME_DATABASE_URL is required in production")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        get_graph()  # compile once at startup
        store = await open_store(settings)
        try:
            # Startup recovery marks unfinished commands INTERRUPTED; only safe if no other
            # instance is running against the same database.
            await store.acquire_instance_lock()
            app.state.control = await build_control(settings, store)
            logger.info(
                "startup complete gateway=%s durable=%s", settings.device_gateway, store.durable
            )
            yield
        finally:
            await store.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # Path only (no query string, headers or body) to avoid leaking data.
        logger.info(
            "%s %s -> %d (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    app.include_router(health.router)
    app.include_router(agent.router)
    app.include_router(homes.router)
    return app


app = create_app()
