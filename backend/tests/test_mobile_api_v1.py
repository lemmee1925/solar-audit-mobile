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


def test_store_photo_rejects_spoofed_non_image_bytes(tmp_path):
    from io import BytesIO
    from werkzeug.datastructures import FileStorage
    from app import app, _store_photo

    fake = FileStorage(stream=BytesIO(b"not-a-real-jpeg-payload"), filename="evil.jpg")
    with app.test_request_context():
        result = _store_photo(fake, "test_magic_bytes", "p1", {}, "1")
    assert result is None


def test_login_rate_limiter_blocks_repeated_failures(monkeypatch):
    from rate_limit import is_rate_limited, record_failure, clear_failures

    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "60")
    key = "test:127.0.0.1:attacker"
    clear_failures(key)
    assert is_rate_limited(key)[0] is False
    record_failure(key)
    record_failure(key)
    record_failure(key)
    limited, retry_after = is_rate_limited(key)
    assert limited is True
    assert retry_after >= 1
    clear_failures(key)


def test_mobile_api_token_logout_revokes_bearer_token(monkeypatch):
    from werkzeug.security import generate_password_hash
    from app import app
    from models import Session, User, MobileAPIToken
    from paths import site_config_path
    from site_config import load_site_config, save_site_config

    monkeypatch.setenv("MOBILE_API_ENABLED", "1")
    monkeypatch.setenv("MOBILE_API_SIGNING_SECRET", "s" * 32)
    monkeypatch.setenv("DISABLE_LOGIN_RATE_LIMIT", "1")
    had_site_config = site_config_path().exists()
    site = load_site_config() or save_site_config("TEST01", "Test Site")
    try:
        db = Session()
        try:
            user = db.query(User).filter_by(username="rev_tester", site_id=site.site_id).first()
            if not user:
                user = User(
                    username="rev_tester",
                    full_name="Revocation Tester",
                    password_hash=generate_password_hash("secret123"),
                    role="site_admin",
                    is_active=True,
                    site_id=site.site_id,
                )
                db.add(user)
                db.commit()
        finally:
            db.close()

        client = app.test_client()
        login_resp = client.post("/api/mobile/v1/auth/token", json={"username": "rev_tester", "password": "secret123"})
        assert login_resp.status_code == 200
        token = login_resp.get_json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/mobile/v1/bootstrap", headers=headers).status_code == 200
        assert client.post("/api/mobile/v1/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/mobile/v1/bootstrap", headers=headers).status_code == 401

        db = Session()
        try:
            assert db.query(MobileAPIToken).count() >= 1
        finally:
            db.close()
    finally:
        if not had_site_config:
            site_config_path().unlink(missing_ok=True)
