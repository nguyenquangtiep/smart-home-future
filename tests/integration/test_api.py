import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from smarthome.api.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_invoke_greeting(client: TestClient) -> None:
    resp = client.post("/api/v1/agent/invoke", json={"message": "xin chào"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"reply"}
    assert "Xin chào" in body["reply"]
    assert resp.headers["content-type"] == "application/json; charset=utf-8"


def test_invoke_strips_surrounding_whitespace(client: TestClient) -> None:
    resp = client.post("/api/v1/agent/invoke", json={"message": "   một hai   "})
    assert resp.status_code == 200
    assert "(2 từ)" in resp.json()["reply"]


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   \t\n "},
        {},
        {"message": 123},
        {"text": "xin chào"},
    ],
)
def test_invoke_rejects_invalid(client: TestClient, payload: dict) -> None:
    resp = client.post("/api/v1/agent/invoke", json=payload)
    assert resp.status_code == 422


def test_invoke_rejects_non_json(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agent/invoke", content="xin chào", headers={"content-type": "text/plain"}
    )
    assert resp.status_code == 422


def test_invoke_does_not_log_message(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    secret = "mật-khẩu-bí-mật-12345"
    # The "smarthome" logger does not propagate to root, so attach caplog's handler directly.
    logger = logging.getLogger("smarthome")
    logger.addHandler(caplog.handler)
    try:
        client.post("/api/v1/agent/invoke", json={"message": secret})
    finally:
        logger.removeHandler(caplog.handler)
    assert caplog.records, "expected access/agent log records"
    assert secret not in caplog.text
