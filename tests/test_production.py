import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.backend.storage import RedisDict

PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$1nAFATBkZHf7FYm10EoAqw$"
    "bcQeiCVDJV5nH2dSoHhYtUlyLARtmS1ce7UBSUXokYQ"
)


def production_config(modules_path: Path, *, https_only: bool = False):
    return PytinctureConfig(
        modules_path=str(modules_path),
        default_application="demo",
        enable_user_login=True,
        session_secret="production-test-secret-at-least-32-bytes",
        session_https_only=https_only,
        environment={
            "ALLOWED_EMAILS": "e2e@example.com",
            "AUTH_PASSWORD_HASHES": json.dumps({"e2e@example.com": PASSWORD_HASH}),
        },
    )


def make_workers(tmp_path: Path, *, https_only: bool = False):
    (tmp_path / "demo.py").write_text(
        'APP_TITLE = "Production smoke"\n', encoding="utf-8"
    )
    config = production_config(tmp_path, https_only=https_only)
    return create_app(config), create_app(config)


def login(client: TestClient):
    response = client.post(
        "/demo/auth/user",
        data={"email": "e2e@example.com", "password": "demo-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


def test_signed_session_cookie_is_portable_between_workers(tmp_path):
    first, second = make_workers(tmp_path)
    with TestClient(first) as first_client, TestClient(second) as second_client:
        login(first_client)
        second_client.cookies.update(first_client.cookies)
        response = second_client.get("/demo", follow_redirects=False)
        assert response.status_code == 200


def test_local_revocations_are_worker_local_but_shared_store_propagates(tmp_path):
    first, second = make_workers(tmp_path)
    with TestClient(first) as first_client, TestClient(second) as second_client:
        login(first_client)
        second_client.cookies.update(first_client.cookies)
        first_client.get("/demo/auth/logout", follow_redirects=False)
        assert second_client.get("/demo", follow_redirects=False).status_code == 200

    first, second = make_workers(tmp_path)

    class SharedRedis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

        def delete(self, key):
            return int(self.values.pop(key, None) is not None)

        def exists(self, key):
            return int(key in self.values)

        def scan(self, cursor, match, count):
            return "0", []

    redis = SharedRedis()
    first.state.pytincture_backend.AUTH_SESSION_REVOCATIONS = RedisDict(
        key_prefix="revoked-session:", redis_client=redis, cache_reads=False
    )
    second.state.pytincture_backend.AUTH_SESSION_REVOCATIONS = RedisDict(
        key_prefix="revoked-session:", redis_client=redis, cache_reads=False
    )
    with TestClient(first) as first_client, TestClient(second) as second_client:
        login(first_client)
        second_client.cookies.update(first_client.cookies)
        assert second_client.get("/demo", follow_redirects=False).status_code == 200
        first_client.get("/demo/auth/logout", follow_redirects=False)
        rejected = second_client.get("/demo", follow_redirects=False)
        assert rejected.status_code == 307
        assert rejected.headers["location"] == "/demo/login"


def test_https_deployment_sets_secure_session_cookie(tmp_path):
    first, _ = make_workers(tmp_path, https_only=True)
    with TestClient(first, base_url="https://service.example") as client:
        response = login(client)
    assert "secure" in response.headers["set-cookie"].lower()


def test_request_completion_log_is_structured(tmp_path, caplog):
    first, _ = make_workers(tmp_path)
    with caplog.at_level(logging.INFO, logger="pytincture.security"):
        with TestClient(first) as client:
            response = client.get(
                "/healthz",
                headers={"X-Request-ID": "edge-request-42"},
            )
    assert response.status_code == 200
    events = []
    for record in caplog.records:
        try:
            events.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            continue
    completed = next(
        event for event in events if event.get("event") == "request.complete"
    )
    assert completed["correlation_id"] == "edge-request-42"
    assert completed["path"] == "/healthz"
    assert completed["status_code"] == 200
    assert completed["duration_ms"] >= 0
