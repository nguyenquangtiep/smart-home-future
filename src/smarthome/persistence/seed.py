"""``seed-demo``: create the simulated demo home in the database (local/test only).

The command takes the instance lock first, so it cannot run while the application is running
(the application's in-memory configuration would no longer match the database), and refuses
outside local/test so simulated data never reaches a real deployment.
"""

from smarthome.core.config import Settings
from smarthome.demo_home import HOME_ID, build_demo_config
from smarthome.persistence.config_store import PostgresHomeConfigStore
from smarthome.persistence.postgres import PostgresCommandStore


class SeedRefusedError(RuntimeError):
    pass


async def seed_demo(settings: Settings) -> str:
    """Seed the demo home. Returns "created" or "exists"; raises SeedRefusedError or
    InstanceLockError."""
    if not settings.permissive:
        raise SeedRefusedError(
            f"seed-demo is only allowed in local/test, not in {settings.environment!r}"
        )
    if settings.database_url is None:
        raise SeedRefusedError("SMARTHOME_DATABASE_URL is not set")
    dsn = settings.database_url.get_secret_value()
    lock = await PostgresCommandStore.open(dsn)
    try:
        await lock.acquire_instance_lock()  # raises InstanceLockError while the app runs
        config_store = await PostgresHomeConfigStore.open(dsn)
        try:
            if any(c.home.id == HOME_ID for c in await config_store.load_all()):
                return "exists"
            config, _members = build_demo_config()
            await config_store.replace_home(config, None)
            return "created"
        finally:
            await config_store.close()
    finally:
        await lock.close()
