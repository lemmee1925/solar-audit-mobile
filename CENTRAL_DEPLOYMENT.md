# Solar Audit — Central Deployment (Authoritative Production Architecture)

**Central Multi-Site PostgreSQL Mode** is the **authoritative production architecture** for all multi-user, HQ, and Android mobile deployments.

## Authoritative Production Mode (`production_server.py` / `Dockerfile`)
- `CENTRAL_MODE=1` (enforced at startup)
- `DATABASE_URL=postgresql+psycopg://...` (enforced at startup — SQLite is rejected)
- `SECRET_KEY` and `MOBILE_API_SIGNING_SECRET` are mandatory
- `sites` registry resolves the authenticated user's site; `/hq` provides multi-site HQ management
- No `/setup` wizard; `/health` provides the monitoring endpoint
- Audit number allocation uses a PostgreSQL row-locked counter (`next_audit_number`)

## Legacy / Dev-Only Mode (`run_prod.py` / `app.py`)
- `CENTRAL_MODE` unset/false with SQLite database and `config/site_config.json`
- Retained **only** for local development, isolated tests, or legacy standalone single-laptop installs — **do not use for multi-user production**.

## Recommended production stack

Cloudflare Tunnel → Waitress → Flask → PostgreSQL (Windows HQ)

## First central bootstrap

Set environment variables first:

```powershell
$env:CENTRAL_MODE="1"
$env:DATABASE_URL="postgresql+psycopg://solar_audit:CHANGE_ME@localhost:5432/solar_audit"
$env:SECRET_KEY="GENERATE_A_LONG_RANDOM_SECRET"
```

Then from `backend/`:

```powershell
python bootstrap_central.py --organization "CleanMax" --site-id MH001 --site-name "Example Solar Site" --admin hqadmin --admin-name "HQ Admin" --password "CHANGE_ME_NOW"
```

Do **not** use a real production password in shell history if the IT/security policy prohibits it; use a secret-management mechanism instead.

## Important next work before production

1. Add and enforce CSRF protection across browser forms and define the CSRF strategy for offline JSON sync.
2. Complete migration of all site-sensitive queries to authenticated-user/site registry context.
3. Add production session/security headers and rate limiting.
4. Keep the local single-site ZIP backup/restore endpoints disabled in central mode; implement PostgreSQL + photo backup separately.
5. Add automated PostgreSQL + photo backups.
6. Add staging and CI tests.
7. Run 10/25/50/100-user load tests.
8. Only then deploy to pilot sites.


## Windows production entry point

Use `backend/production_server.py`. It refuses to start unless `CENTRAL_MODE=1` and `SECRET_KEY` are configured. It uses Waitress with configurable threads (default 8). See `deployment/windows/INSTALL_WINDOWS.md`.
