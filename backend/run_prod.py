"""Start the Solar Audit server (Waitress WSGI on 0.0.0.0:5000) with Mobile API v1 enabled."""
import os
import sys

# Ensure backend directory is on sys.path when invoked from project root
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Enable the Native Mobile API v1 by default when starting via run_prod.py
os.environ.setdefault("MOBILE_API_ENABLED", "1")
os.environ.setdefault(
    "MOBILE_API_SIGNING_SECRET",
    os.environ.get("SECRET_KEY") or "solar-audit-mobile-api-signing-secret-v1-local-dev-key-2026",
)

from app import app
from waitress import serve

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    threads = int(os.environ.get("WAITRESS_THREADS", 8))
    print(f"Solar Audit server starting on http://127.0.0.1:{port} (bound to {host}:{port}, Waitress {threads} threads)", flush=True)
    print(f"Native Mobile API v1 endpoint: http://127.0.0.1:{port}/api/mobile/v1", flush=True)
    serve(app, host=host, port=port, threads=threads, channel_timeout=180)
