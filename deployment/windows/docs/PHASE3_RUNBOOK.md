# Phase 3 — Windows HQ Production Runbook

## Database lifecycle

| Environment | Database | Purpose |
|---|---|---|
| Test | `solar_audit_test` | Current PostgreSQL concurrency/security testing only |
| Staging | `solar_audit_staging` | Production-like deployment and UAT |
| Production | `solar_audit_prod` | Real HQ/site data after cutover |

**Never promote `solar_audit_test` into production.** Production is a fresh database created during the final cutover.

## Phase 3A — Staging
1. Install/verify PostgreSQL on the HQ server.
2. Create `solar_audit_staging` and a dedicated login.
3. Deploy the current application package into a staging application directory.
4. Configure `CENTRAL_MODE=1`, staging `DATABASE_URL`, a staging-only `SECRET_KEY`, and Waitress on a local port.
5. Run central isolation tests and PostgreSQL concurrency tests against staging.
6. Create sample organizations/sites/users and perform end-to-end audit + offline-sync UAT.
7. Test backup and restore before production cutover.

## Phase 3B — Production database creation
Do this only after staging passes.

1. Create a **new** `solar_audit_prod` database and `solar_audit_prod` login using `sql/02_create_production.sql` with real secrets.
2. Do not reuse the test/staging database.
3. Point the production application only to `solar_audit_prod`.
4. Start the application locally through Waitress.
5. Check `/health` locally.
6. Run the production preflight script.

## Phase 3C — Backups
- Daily PostgreSQL custom-format dump to a separate disk/path.
- Keep an off-server copy according to company policy.
- Back up the photo/file storage separately; a PostgreSQL dump does not contain uploaded photos.
- Periodically perform a restore drill into a disposable database.

## Phase 3D — Service/restart
For the first pilot, Windows Task Scheduler may launch `run_solar_audit.ps1` at startup. For mature deployment, use the company's approved Windows service wrapper.

## Phase 3E — Cutover gate
Production cutover is allowed only when:
- Central isolation passes.
- PostgreSQL concurrency passes.
- Staging UAT passes.
- Backup + restore drill passes.
- Health check passes after restart.
- Cloudflare Tunnel is ready but the origin is still bound to `127.0.0.1`.

## Rollback
1. Stop the production application.
2. Restore the previous application package.
3. Restore the last verified production database backup only if data rollback is required and approved.
4. Restore photo storage from the matching backup if needed.
5. Start the previous package and verify `/health`.
