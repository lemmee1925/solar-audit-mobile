# Native API adapter — audit/photo/completion phase

## What changed

The versioned Flask API now exposes these bearer-token endpoints:

- `POST /api/mobile/v1/audits/{local_audit_id}/manifest`
- `POST /api/mobile/v1/audits/{local_audit_id}/photos/{photo_id}`
- `POST /api/mobile/v1/audits/{local_audit_id}/complete`

These endpoints delegate to the existing PWA `/api/offline/sync/audit`, `/api/offline/sync/photo`, and `/api/offline/sync/complete` handlers. That is intentional: existing plant/site checks, idempotent audit upsert, photo storage, incomplete-observation validation, scoring, and PDF generation remain owned by the PWA code paths. The adapter does not duplicate those rules.

## Required configuration

Set these server environment variables only after deployment/security review:

- `MOBILE_API_ENABLED=1`
- `MOBILE_API_SIGNING_SECRET=<cryptographically-random secret of at least 32 characters>`
- `SECRET_KEY=<separate strong Flask session secret>`

Expose the service over HTTPS only. Apply request-rate limits to `/api/mobile/v1/auth/token` at the reverse proxy or hosting layer. Do not commit secrets to source control.

## Payload contract

The manifest body must match the existing `/api/offline/sync/audit` JSON contract, including `local_uuid` equal to the path ID. The photo request must be multipart and include `local_uuid`, `audit_local_uuid`, `question_id`, and the existing asset mapping fields expected by `/api/offline/sync/photo`, plus the `file` part. Completion JSON must include `audit_local_uuid` equal to the path ID.

The native UI's local checkpoint keys (such as `safety-0`) are not the same as server numeric question IDs. The current APK uses a cautious mapping from cached reference data and checkpoint/equipment labels; it must be validated with real test audits before background sync is enabled. The Android `SyncWorker` remains deliberately disabled. Observation synchronization is user-triggered from the Observations screen; local completion is not treated as server submission until the backend confirms it.

## Validation checklist

1. With `MOBILE_API_ENABLED` unset, token issuance and all protected endpoints return `503`.
2. With valid configuration, invalid credentials return generic `401`.
3. Missing, expired, or modified bearer token returns `401`.
4. User disabled after token issuance is rejected on the next request.
5. Wrong installation/site ID returns `401`/`404` without exposing other-site data.
6. Manifest ID mismatch returns `400`; repeated valid manifest requests return the same audit identity/number.
7. Photo path/form ID mismatch returns `400`; photo retries are idempotent under existing PWA logic.
8. Completion with unresolved NC observations returns `409`; only the existing completion handler can mark an audit submitted.
9. Verify a submitted audit's server-generated PDF and scoring against a PWA-created audit.
10. Run tests against a disposable database and test uploads; never use production audit data for initial validation.

## Known limits

- The install's current data model remains single-site per Flask installation.
- Background automatic sync is not enabled; audit and observation synchronization remain explicit user-triggered actions. Retry and end-to-end testing are still required before considering background sync.
- Runtime integration tests were not run in this environment because the Flask dependency stack is not installed here. Python syntax compilation passed only; it is not a runtime certification.


## Native observation synchronization

The native API now exposes these observation workflow routes (all require the existing bearer token):

- `GET /api/mobile/v1/observations` — list observations scoped to the configured site and the signed-in user's plant assignments. Supports `?audit_id=<server/local UUID>`.
- `POST /api/mobile/v1/observations/{observation_id}/details` — update finding text, remarks, severity, responsible person, and target date using the same validation and event logging as the web Observations workflow.
- `POST /api/mobile/v1/observations/{observation_id}/update` — start work, save action taken/closure date, or submit for review using the existing `site_scope.py` workflow helpers.
- `POST /api/mobile/v1/observations/{observation_id}/closure-photos/{photo_id}` — upload closure evidence using the existing backend photo-storage helper. The photo ID is used as an idempotency key, so retries do not create duplicate closure-photo records.
- `POST /api/mobile/v1/observations/{observation_id}/review` — reviewer accept/reject/reopen using the established reviewer-role rules.
- `POST /api/mobile/v1/observations/{observation_id}/reassign` — reviewer-only severity/owner/target-date changes.

The backend remains the source of truth. Its current workflow creates canonical observations from NC/fail checkpoints; OFI remains local-only until the backend data model/workflow explicitly supports OFI observations. Do not translate OFI to NC/fail, because that would corrupt the audit record. The native list includes withdrawn observations too, so the APK can reconcile a checkpoint that was changed away from NC before the audit was submitted.

### Android smoke test

1. Sign in to the native API and synchronize the audit manifest.
2. Submit/complete the audit on the server. Open **Observations** in the APK and tap **Sync observations**. NC findings should link to their existing backend observation IDs; repeated refreshes must not create duplicate observations.
3. Open **Complete details**, edit severity, description, responsible person, and target date, then save. Verify the same fields in the backend's Observations page.
4. Add action-taken text, actual closure date, and at least one action photo. Save, then tap **Submit for review**. The backend should show the observation as submitted for review with closure evidence attached.
5. Accept/reject the observation in the backend web workflow, then tap **Sync observations** in the APK. The new server status and review metadata should appear in the app.
6. Repeat a sync and photo upload; verify no duplicate observations or closure-photo records. Test one failure with the server unavailable and confirm the local record remains saved with a sync error.
