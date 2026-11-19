FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOLAR_AUDIT_DATA_DIR=/data \
    WAITRESS_HOST=0.0.0.0 \
    WAITRESS_PORT=8080 \
    TRUST_PROXY_HEADERS=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY config/ ./config/

RUN mkdir -p /data/photos /data/reports /data/backups

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

CMD ["python", "backend/production_server.py"]
