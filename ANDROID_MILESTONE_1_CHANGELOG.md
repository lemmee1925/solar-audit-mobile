# Android Milestone 1 — Working Copy Changelog

Baseline: `SolarAuditMobile_central_security_v11.zip`
Working directory: `SolarAuditV11_AndroidDev`

## Implemented in this working copy

1. Added `MobileAPIToken` model with SHA-256 token hash storage, user relationship, device label, expiry, and revocation timestamp.
2. Added Alembic revision `20261008_mobile_api_tokens` after the v11 HQ-admin migration. The migration is idempotent because the current model import calls `Base.metadata.create_all()` before migrations may be run.
3. Added `POST /api/mobile/login`. It verifies credentials, rejects inactive users/sites, issues a random 30-day token, and only stores its hash. Global HQ accounts stay on the web console; the mobile API login is for site-scoped accounts.
4. Added bearer-token request authentication for Flask-Login and `POST /api/mobile/logout` for revocation.
5. Allowed valid bearer tokens to authenticate the existing `/api/offline/*` API requests without browser CSRF tokens. Cookie-authenticated browser requests still require the existing CSRF token.
6. Changed unauthenticated `/api/*` requests to return JSON `401` instead of redirecting to an HTML login page. Browser page behavior remains a redirect.
7. Raised Flask's default maximum request size from 10 MiB to 60 MiB, allowing one 50 MiB photo plus multipart overhead. The environment variable `MAX_CONTENT_LENGTH` still overrides this value.
8. Added `backend/tests/mobile_api_test.py` for invalid credentials, HQ-account restriction, hashed token storage, bearer access, and token revocation.
9. Added this API contract and changelog documentation.

## Validation performed here

- Python syntax compilation passed for `app.py`, `models.py`, the new migration, and the new test file.
- Automated Flask tests were **not run** in this environment because Flask and Flask-Login are unavailable and package installation is blocked. This is not a passing test result. Run the new test and existing suite in the project's Windows virtual environment before connecting Android.
- No existing staging database was modified. The original uploaded ZIP remains unchanged.

## Next work

1. Run the new API test and existing regression tests locally.
2. Resolve any failures before changing production/staging schema.
3. Validate bootstrap/audit/photo/complete endpoints with bearer auth and confirm role/plant scoping.
4. Then create the clean native Kotlin Android project and implement encrypted token storage, Room persistence, app-private photo files, and WorkManager sync.
