# Native Android API — first implementation milestone

This working copy adds a bearer-token authentication path for the native Android client. The existing browser login/session and CSRF flow remains in place.

## Endpoints

- `POST /api/mobile/login` — JSON `{ "username": "...", "password": "...", "device_name": "..." }`; returns a 30-day bearer token and site-scoped user profile.
- `POST /api/mobile/logout` — requires `Authorization: Bearer <token>`; revokes that token.
- Existing `/api/offline/ping`, `/api/offline/bootstrap`, `/api/offline/sync/audit`, `/api/offline/sync/photo`, and `/api/offline/sync/complete` accept the same bearer token as an alternative to the existing browser session.

Tokens are generated using a cryptographically secure random generator. Only SHA-256 token hashes are stored in `mobile_api_tokens`. Tokens expire after 30 days and can be revoked. Disabled users, inactive sites, expired tokens, revoked tokens, and HQ global accounts are not accepted by this native audit login path. HQ administration remains in the web console.

## Upload limit

The Flask request limit default was raised from 10 MiB to 60 MiB to accommodate a single image of up to 50 MiB plus multipart overhead. Set `MAX_CONTENT_LENGTH=62914560` explicitly in the server environment if deployment configuration overrides the default. This is a request-size limit, not a total per-audit storage quota; total storage capacity and retention still need a separate policy.

## Migration

`backend/migrations/versions/20261008_mobile_api_tokens.py` adds the token table after `20261007_hq_admin`. The migration checks whether the table already exists because `models.py` currently calls `Base.metadata.create_all()` during import.

## Test

Run from the `backend` directory in the project's virtual environment:

```powershell
python -m pytest tests/mobile_api_test.py -v
```

The test checks invalid credentials, refusal of global HQ login through the native auditing API, token hash storage, bearer access to the existing ping endpoint, and logout revocation. It has not been executed in this review environment because Flask and Flask-Login are not installed here and package installation is unavailable; run it locally before using this working copy.
