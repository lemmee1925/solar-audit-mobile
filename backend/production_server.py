"""Windows/Linux production entry point for Solar Audit central mode.

Run this with the application virtual environment and required environment
variables (or a .env file in the project root) configured.
Waitress is used instead of Flask's development server.
"""
import os
from pathlib import Path
from waitress import serve


def _load_dotenv_if_present():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv_if_present()
from app import app  # noqa: E402


def _truthy(value):
    return str(value or "").lower() in {"1", "true", "yes", "on"}


if not _truthy(os.environ.get("CENTRAL_MODE")):
    raise SystemExit("CENTRAL_MODE=1 is required for the production server.")
if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    raise SystemExit("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://...) for production.")
if not os.environ.get("SECRET_KEY"):
    raise SystemExit("SECRET_KEY is required for production.")
if _truthy(os.environ.get("MOBILE_API_ENABLED", "1")):
    os.environ.setdefault("MOBILE_API_ENABLED", "1")
    secret = os.environ.get("MOBILE_API_SIGNING_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("MOBILE_API_SIGNING_SECRET (>= 32 chars) is required when MOBILE_API_ENABLED=1.")

threads = int(os.environ.get("WAITRESS_THREADS", "24"))
connection_limit = int(os.environ.get("WAITRESS_CONNECTION_LIMIT", "500"))
channel_timeout = int(os.environ.get("WAITRESS_CHANNEL_TIMEOUT", "180"))
host = os.environ.get("WAITRESS_HOST", "0.0.0.0")
port = int(os.environ.get("WAITRESS_PORT", "8080"))

print(f"Solar Audit production server: http://{host}:{port}")
print(f"Waitress threads: {threads}, connection_limit: {connection_limit}")
serve(app, host=host, port=port, threads=threads, connection_limit=connection_limit, channel_timeout=channel_timeout)
