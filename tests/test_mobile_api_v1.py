from app import app


def test_token_endpoint_disabled_returns_503(monkeypatch):
    monkeypatch.delenv("MOBILE_API_ENABLED", raising=False)
    monkeypatch.delenv("MOBILE_API_SIGNING_SECRET", raising=False)

    response = app.test_client().post(
        "/api/mobile/v1/auth/token",
        json={"username": "test-user", "password": "test-password"},
    )

    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_bootstrap_disabled_returns_503(monkeypatch):
    monkeypatch.delenv("MOBILE_API_ENABLED", raising=False)

    response = app.test_client().get("/api/mobile/v1/bootstrap")

    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_enabled_api_without_signing_secret_returns_503(monkeypatch):
    monkeypatch.setenv("MOBILE_API_ENABLED", "1")
    monkeypatch.delenv("MOBILE_API_SIGNING_SECRET", raising=False)

    response = app.test_client().post(
        "/api/mobile/v1/auth/token",
        json={"username": "test-user", "password": "test-password"},
    )

    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_protected_endpoint_without_bearer_token_returns_401(monkeypatch):
    monkeypatch.setenv("MOBILE_API_ENABLED", "1")
    monkeypatch.setenv(
        "MOBILE_API_SIGNING_SECRET",
        "isolated-test-signing-secret-at-least-32-chars",
    )

    response = app.test_client().get("/api/mobile/v1/bootstrap")

    assert response.status_code == 401
    assert response.get_json()["ok"] is False


def test_observation_list_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("MOBILE_API_ENABLED", "1")
    monkeypatch.setenv(
        "MOBILE_API_SIGNING_SECRET",
        "isolated-test-signing-secret-at-least-32-chars",
    )

    response = app.test_client().get("/api/mobile/v1/observations")

    assert response.status_code == 401
    assert response.get_json()["ok"] is False


def test_mobile_api_cors_allows_put_patch_delete_and_if_none_match():
    response = app.test_client().options(
        "/api/mobile/v1/plants/test-plant/specs",
        headers={"Origin": "http://localhost"},
    )

    assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost"
    allow_methods = response.headers.get("Access-Control-Allow-Methods", "")
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert method in allow_methods
    allow_headers = response.headers.get("Access-Control-Allow-Headers", "")
    assert "If-None-Match" in allow_headers
    assert "ETag" in response.headers.get("Access-Control-Expose-Headers", "")
