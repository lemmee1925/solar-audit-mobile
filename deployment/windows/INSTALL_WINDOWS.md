# Solar Audit — Windows Central Production

## Stack

Cloudflare Tunnel → Waitress → Flask → PostgreSQL

For this Windows deployment, **do not use `flask run` or `app.run()`**.
`backend/production_server.py` starts Waitress.

## 1. Install Python dependencies

Create a dedicated virtual environment on the server and install `requirements.txt`.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Configure environment

Set these using the company's approved secret/configuration mechanism:

- `CENTRAL_MODE=1`
- `DATABASE_URL=postgresql+psycopg://...`
- `SECRET_KEY=<long random secret>`
- `WAITRESS_HOST=127.0.0.1`
- `WAITRESS_PORT=8080`
- `WAITRESS_THREADS=8`

Do not expose PostgreSQL to the internet.

## 3. Start the production server

```powershell
cd backend
..\.venv\Scripts\python.exe production_server.py
```

Check: `http://127.0.0.1:8080/health`

## 4. Keep it running

For the pilot, Windows Task Scheduler can launch the PowerShell runner at system startup with **Run whether user is logged on or not**. For a mature IT deployment, use the company's approved Windows service manager/service wrapper.

## 5. Cloudflare

Point the Cloudflare Tunnel origin at:

`http://127.0.0.1:8080`

Do not expose port 8080 directly to the internet.

## 6. Important

Waitress is the WSGI server. It does not replace PostgreSQL, backups, monitoring, or the reverse-access layer.

Before 100-site rollout, run the application's 10/25/50/100-user load tests and verify offline sync under interrupted connectivity.

## Database environments

Use separate databases: `solar_audit_test` for tests, `solar_audit_staging` for UAT, and a fresh `solar_audit_prod` created only at production cutover. Never expose PostgreSQL directly to the internet.

See `deployment/windows/docs/PHASE3_RUNBOOK.md` for the cutover and rollback gates.
