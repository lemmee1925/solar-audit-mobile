# Azure Deployment Guide (Solar Audit Backend + HQ)

## Required Azure Resources
1. **Azure Database for PostgreSQL Flexible Server** (create database `solar_audit_prod`).
2. **Azure Container Apps** or **Azure App Service (Linux Container)** using the root `Dockerfile`.
3. **Azure Files** share mounted at `/data` (maps to `SOLAR_AUDIT_DATA_DIR=/data` for persistent `photos/`, `reports/`, and `backups/`).

## Required Environment Variables
Set these in Azure App Settings / Container App Secrets:
- `CENTRAL_MODE=1`
- `DATABASE_URL=postgresql+psycopg://<user>:<password>@<pg-host>:5432/solar_audit_prod?sslmode=require`
- `SECRET_KEY=<64-char-hex>`
- `MOBILE_API_ENABLED=1`
- `MOBILE_API_SIGNING_SECRET=<64-char-hex>`
- `SOLAR_AUDIT_DATA_DIR=/data`
- `TRUST_PROXY_HEADERS=1`
- `WAITRESS_HOST=0.0.0.0`
- `WAITRESS_PORT=8080`
- `WAITRESS_THREADS=24`
- `WAITRESS_CONNECTION_LIMIT=500`
- `WAITRESS_CHANNEL_TIMEOUT=180`
- `DB_POOL_SIZE=20`
- `DB_MAX_OVERFLOW=30`

## Notes
- Azure App Service / Container Apps terminates HTTPS automatically and forwards `X-Forwarded-For` / `X-Forwarded-Proto`; `TRUST_PROXY_HEADERS=1` enables `ProxyFix` so rate limiting and external URLs work accurately.
- Keep **replicas = 1** (single instance) unless rate limiting (`backend/rate_limit.py`) is moved to a shared store (e.g., Redis).
- After provisioning the database, run Alembic migrations / `backend/bootstrap_central.py` once before opening traffic.
