import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from smarthome.api.main import create_app
from smarthome.core.config import Settings
from smarthome.demo_home import HOME_ID

BASE = f"/api/v1/homes/{HOME_ID}"
OWNER = {"X-Dev-User": "user-owner"}
MEMBER = {"X-Dev-User": "user-member"}
GUEST = {"X-Dev-User": "user-guest"}


def _settings(**overrides: object) -> Settings:
    values = {
        "environment": "test",
        "dev_identity_enabled": True,
        "sim_latency_s": 0.02,
        "verify_timeout_s": 0.3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


def _wait(client: TestClient, command_id: str, headers: dict) -> dict:
    deadline = time.monotonic() + 2
    while True:
        body = client.get(f"{BASE}/commands/{command_id}", headers=headers).json()
        if body["status"] not in {"dispatching", "dispatched"} or time.monotonic() > deadline:
            return body
        time.sleep(0.02)


def test_auth_disabled_by_default() -> None:
    with TestClient(create_app(_settings(dev_identity_enabled=False))) as c:
        resp = c.get(f"{BASE}/devices", headers=OWNER)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"


def test_dev_identity_refused_in_production() -> None:
    with pytest.raises(RuntimeError):
        _settings(environment="production")


def test_non_member_and_unknown_home_get_404(client: TestClient) -> None:
    assert client.get(f"{BASE}/devices", headers={"X-Dev-User": "stranger"}).status_code == 404
    assert client.get("/api/v1/homes/other/devices", headers=OWNER).status_code == 404
    assert client.get(f"{BASE}/devices").status_code == 401


def test_home_structure_and_device_views(client: TestClient) -> None:
    body = client.get(BASE, headers=MEMBER).json()
    assert body["home"]["simulated"] is True
    assert {r["name"] for r in body["home"]["rooms"]} == {"Phòng khách", "Phòng ngủ"}
    assert len(body["devices"]) == 10
    light = next(d for d in body["devices"] if d["id"] == "dev-living-light")
    assert light["placement"]["room_id"] == "room-living"
    on_off = next(c for c in light["capabilities"] if c["kind"] == "on_off")
    assert on_off == {**on_off, "status": "simulated", "value": False, "source": "simulated"}


def test_command_accepted_then_verified(client: TestClient) -> None:
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-living-light", "capability": "on_off", "value": True},
        headers={**MEMBER, "Idempotency-Key": "abc"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] in {"dispatched", "succeeded"}
    final = _wait(client, resp.json()["id"], MEMBER)
    assert final["status"] == "succeeded"
    device = client.get(f"{BASE}/devices/dev-living-light", headers=MEMBER).json()
    assert next(c for c in device["capabilities"] if c["kind"] == "on_off")["value"] is True


def test_silent_device_reports_timed_out(client: TestClient) -> None:
    put = client.put(
        f"{BASE}/simulator/devices/dev-living-ac/behavior",
        json={"behavior": "silent"},
        headers=OWNER,
    )
    assert put.status_code == 204
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-living-ac", "capability": "target_temperature", "value": 26},
        headers=MEMBER,
    )
    final = _wait(client, resp.json()["id"], MEMBER)
    assert final["status"] == "timed_out"


@pytest.mark.parametrize(
    ("headers", "payload", "status_code", "code"),
    [
        (GUEST, {"device_id": "dev-living-light", "capability": "on_off", "value": True}, 403,
         "FORBIDDEN_ROLE"),
        (MEMBER, {"device_id": "dev-living-light", "capability": "brightness", "value": 500}, 422,
         "INVALID_VALUE"),
    ],
)  # fmt: skip
def test_rejected_commands(
    client: TestClient, headers: dict, payload: dict, status_code: int, code: str
) -> None:
    resp = client.post(f"{BASE}/commands", json=payload, headers=headers)
    assert resp.status_code == status_code
    assert resp.json()["status"] == "rejected"
    assert resp.json()["reasons"][0]["code"] == code


def test_unknown_device_is_404(client: TestClient) -> None:
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "nope", "capability": "on_off", "value": True},
        headers=MEMBER,
    )
    assert resp.status_code == 404


def test_unlock_needs_server_side_owner_confirmation(client: TestClient) -> None:
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-front-lock", "capability": "lock", "value": "unlocked"},
        headers=MEMBER,
    )
    assert resp.status_code == 202
    command = resp.json()
    assert command["status"] == "awaiting_confirmation"
    url = f"{BASE}/commands/{command['id']}/confirmation"
    assert client.post(url, json={"approve": True}, headers=MEMBER).status_code == 403
    assert client.post(url, json={"approve": True}, headers=OWNER).status_code == 200
    assert _wait(client, command["id"], OWNER)["status"] == "succeeded"


def test_audit_owner_only(client: TestClient) -> None:
    client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-living-tv", "capability": "on_off", "value": True},
        headers=MEMBER,
    )
    assert client.get(f"{BASE}/audit", headers=MEMBER).status_code == 403
    events = client.get(f"{BASE}/audit", headers=OWNER).json()
    assert any(e["action"] == "command.requested" for e in events)


# ---- hardening 2026-11 ----------------------------------------------------------------------


def test_idempotency_replay_and_conflict(client: TestClient) -> None:
    payload = {"device_id": "dev-living-light", "capability": "on_off", "value": True}
    headers = {**MEMBER, "Idempotency-Key": "api-k1"}
    first = client.post(f"{BASE}/commands", json=payload, headers=headers)
    again = client.post(f"{BASE}/commands", json=payload, headers=headers)
    assert first.status_code == again.status_code == 202
    assert again.json()["id"] == first.json()["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    other = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-front-lock", "capability": "lock", "value": "unlocked"},
        headers=headers,
    )
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_verified_command_reports_verification_method(client: TestClient) -> None:
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-living-tv", "capability": "on_off", "value": True},
        headers=MEMBER,
    )
    final = _wait(client, resp.json()["id"], MEMBER)
    assert final["status"] == "succeeded"
    assert final["verification"] == "correlated"


def test_production_requires_database() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(_settings(environment="production", dev_identity_enabled=False))


def test_command_history_survives_app_restart(pg_dsn: str) -> None:
    settings = _settings(database_url=pg_dsn)
    with TestClient(create_app(settings)) as c:
        resp = c.post(
            f"{BASE}/commands",
            json={"device_id": "dev-living-light", "capability": "on_off", "value": True},
            headers=MEMBER,
        )
        command_id = resp.json()["id"]
        assert _wait(c, command_id, MEMBER)["status"] == "succeeded"
    with TestClient(create_app(settings)) as c:
        again = c.get(f"{BASE}/commands/{command_id}", headers=MEMBER)
        assert again.status_code == 200
        assert again.json()["status"] == "succeeded"
        events = c.get(f"{BASE}/audit", headers=OWNER).json()
        assert any(e["command_id"] == command_id for e in events)


def test_empty_database_url_means_not_configured() -> None:
    assert _settings(database_url="").database_url is None
    assert _settings(database_url="   ").database_url is None


def test_store_unavailable_before_dispatch_returns_503(client: TestClient) -> None:
    from smarthome.control.commands import CommandStatus

    store = client.app.state.control.store
    original = store.save

    async def failing_save(record):  # noqa: ANN001
        if record.status is CommandStatus.DISPATCHING:
            raise OSError("db down")
        await original(record)

    store.save = failing_save
    resp = client.post(
        f"{BASE}/commands",
        json={"device_id": "dev-living-light", "capability": "on_off", "value": True},
        headers=MEMBER,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "STORE_UNAVAILABLE"
    assert "db down" not in resp.text
