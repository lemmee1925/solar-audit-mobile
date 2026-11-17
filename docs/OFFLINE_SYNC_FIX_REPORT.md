# Offline / Photo Sync Fix Report

Date: 2026-10-06

## Confirmed issues

1. The offline photo object stored the compressed image in `response.photos[].blob` as a **data URL string**, not as a durable binary Blob in IndexedDB.
2. The photo sync code appended that string to `FormData` as `file`; Flask expects `request.files['file']`, so the server could reject the upload as missing a file.
3. The client embedded photo binary data inside audit records, causing repeated serialization and unnecessary IndexedDB memory/storage pressure.
4. Photo uploads were not independently durable: there was no per-photo upload status, retry count, server photo ID, or persistent binary-file store used by the sync process.
5. The photo endpoint could resolve equipment by name only. The updated sync flow now receives a stable local asset reference and the server returns a server asset ID after audit synchronization; photos use that server asset ID.
6. The previous client marked an audit `synced` only after the photo Promise chain completed, but it did not persist per-photo success/failure state, so a restart could not reliably resume only the remaining photos.
7. The service worker cached authenticated navigation responses (`/` and `/mobile`), which could retain user-specific HTML in the cache. The updated worker uses the static offline shell as the offline navigation fallback without caching authenticated pages.
8. The offline bootstrap did not expose a user identity to the local workspace. The updated bootstrap/ping responses provide the authenticated user ID and the PWA scopes local audit visibility/sync work to that identity.

## Main changes

### Frontend

- `frontend/static/offline.html`
  - IndexedDB schema bumped to version 6 without deleting existing stores/data.
  - Added durable `files` store usage for photo Blobs.
  - Added migration for legacy Base64/data-URL photos and legacy Blob-in-audit photos.
  - Audit records retain only lightweight photo metadata.
  - Photo capture stores the Blob first, then metadata, then queues a durable PHOTO operation.
  - GPS is optional; unavailable/denied location no longer prevents photo retention.
  - Added `QUEUED → UPLOADING → UPLOADED` and `FAILED` state tracking.
  - Added retry count, last error, and server photo ID persistence.
  - Audit sync receives the server asset map and persists `server_asset_id` locally.
  - Photos upload individually and continue after an individual failure.
  - An audit is not marked fully synced while photos remain failed/pending.
  - Sync state is persisted across restart; already-uploaded photos are skipped on retry.
  - Local drafts are associated with the authenticated offline user ID.

### Backend

- `backend/app.py`
  - Offline bootstrap/ping return authenticated offline user identity.
  - Audit sync returns `asset_map` mapping local asset references to server asset IDs.
  - Photo sync accepts the server asset ID when available, with a backward-compatible equipment-name fallback.
  - Photo uploads require an actual multipart file and validate its extension.
  - Deterministic `photo_<local_uuid>.jpg` paths preserve idempotency on retry.
  - Existing successful photo records are returned instead of creating duplicates.
  - Existing site/plant/user authorization remains enforced through the normal login and scoped audit lookup.
  - Failed photo persistence removes an orphaned file when possible.

### Service worker

- `frontend/static/sw.js`
  - Cache version bumped to `solar-audit-shell-v7`.
  - Static offline shell remains precached.
  - Authenticated navigation responses are no longer stored in the service-worker cache.
  - Offline navigation falls back to the static offline workspace.

### Tests

- Added `backend/tests/offline_sync_test.py` covering:
  - offline audit synchronization,
  - asset-map handoff,
  - multipart photo upload,
  - repeated photo upload without duplicate record,
  - repeated audit sync without duplicate audit,
  - missing multipart-file rejection.

## Tests actually executed in this environment

- Python syntax compilation of modified backend files: **PASS**.
- Python syntax compilation of new regression test: **PASS**.
- JavaScript syntax check of `offline.html` script with Node: **PASS**.
- JavaScript syntax check of `sw.js` with Node: **PASS**.
- ZIP packaging and SHA-256 generation: **PASS**.

## Tests not executed

The Flask integration suite was attempted but could not be executed in this environment because the runtime does not contain Flask/SQLAlchemy and outbound package installation is unavailable. The existing integration tests that launch separate local servers also cannot run here without those dependencies/server processes.

Therefore no end-to-end offline browser test, IndexedDB restart test, real camera test, or live Flask sync test is claimed as passed.

## Windows verification required

From the project's existing virtual environment, run:

```powershell
cd SolarAuditMobile_ready\solar_audit\backend
python -m pytest tests -q
```

Then start the application normally and test the PWA in Chrome/Edge on the phone using the acceptance scenario from the original task: install online → download reference data → disconnect → create audit → capture 10+ photos → close/reopen → complete → reconnect → sync → force one photo failure → retry → verify no duplicates.
