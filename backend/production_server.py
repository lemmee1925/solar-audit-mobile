"""Windows production entry point for Solar Audit central mode.

Run this with the application virtual environment and required environment
variables configured. Waitress is used instead of Flask's development server.
"""
import os
from waitress import serve
from app import app


def _truthy(value):
    return str(value or "").lower() in {"1", "true", "yes", "on"}


if not _truthy(os.environ.get("CENTRAL_MODE")):
    raise SystemExit("CENTRAL_MODE=1 is required for the production server.")
if not os.environ.get("SECRET_KEY"):
    raise SystemExit("SECRET_KEY is required for production.")

threads = int(os.environ.get("WAITRESS_THREADS", "8"))
host = os.environ.get("WAITRESS_HOST", "127.0.0.1")
port = int(os.environ.get("WAITRESS_PORT", "8080"))

print(f"Solar Audit production server: http://{host}:{port}")
print(f"Waitress threads: {threads}")
serve(app, host=host, port=port, threads=threads, connection_limit=200, channel_timeout=120)
