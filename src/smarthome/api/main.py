"""FastAPI application entry point: ``uvicorn smarthome.api.main:app``."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from smarthome import __version__
from smarthome.api.deps import get_graph
from smarthome.api.routes import agent, auth, health, homes, members, simulator
from smarthome.api.security import authenticate_request
from smarthome.auth import passwords
from smarthome.auth.service import AuthService
from smarthome.auth.store import PostgresAuthStore
from smarthome.bootstrap import build_control, load_configs, open_config_store
from smarthome.core.config import Settings, get_settings
from smarthome.core.logging import configure_logging, get_logger
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        get_graph()  # compile once at startup
        passwords.configure(
            settings.argon2_max_concurrent, settings.argon2_max_pending, settings.argon2_wait_s
        )
        # Compute the dummy hash now, in a thread, not inside the first login on the event loop.
        await asyncio.to_thread(passwords.dummy_hash)
        store = await open_store(settings)
        config_store = await open_config_store(settings)
        auth_store: PostgresAuthStore | None = None
        try:
            membership = None
            if settings.database_url is not None:
                auth_store = await PostgresAuthStore.open(settings.database_url.get_secret_value())
                app.state.auth = AuthService(auth_store, settings)
                membership = auth_store.membership_source()
            # Startup recovery marks unfinished commands INTERRUPTED; only safe if no other
            # instance is running against the same database.
            await store.acquire_instance_lock()
            configs = await load_configs(config_store)
            app.state.control = await build_control(settings, store, configs, membership)
            logger.info(
                "startup complete gateway=%s durable=%s", settings.device_gateway, store.durable
            )
            yield
        finally:
            if auth_store is not None:
                await auth_store.close()
            await config_store.close()
            await store.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
        # Default-deny: every route needs a session unless it is on PUBLIC_ROUTES.
        dependencies=[Depends(authenticate_request)],
        # Interactive docs and the schema are for local/test only (ADR 0005).
        docs_url="/docs" if settings.permissive else None,
        redoc_url="/redoc" if settings.permissive else None,
        openapi_url="/openapi.json" if settings.permissive else None,
    )
    app.state.settings = settings

    @app.exception_handler(passwords.HasherBusyError)
    async def hasher_busy(request: Request, exc: passwords.HasherBusyError) -> Response:
        # Same answer for every email and route: reveals nothing about accounts (R3).
        return UTF8JSONResponse(
            status_code=503,
            content={"detail": {"code": "AUTH_BUSY", "message": "Hệ thống đang bận, thử lại sau."}},
            headers={"Retry-After": "1"},
        )

    app.state.auth = None  # set at startup when a database is configured
    if settings.trusted_proxy_ips:
        # Honour X-Forwarded-For/-Proto only from these proxies; nothing else reads them.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=list(settings.trusted_proxy_ips))

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
    app.include_router(auth.router)
    app.include_router(homes.router)
    app.include_router(members.router)
    if settings.permissive:
        app.include_router(simulator.router)
    return app


app = create_app()
