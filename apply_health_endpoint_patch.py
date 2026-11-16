from pathlib import Path
from datetime import datetime
import shutil

root = Path(__file__).resolve().parent
app_path = root / "backend" / "app.py"

if not app_path.is_file():
    raise SystemExit("backend/app.py was not found. Run this script from the project root.")

source = app_path.read_text(encoding="utf-8-sig")

if "@app.route('/health'" in source or '@app.get("/health"' in source or "@app.get('/health'" in source:
    raise SystemExit("A /health route already exists. No files changed.")

marker = (
    "app = Flask(__name__, static_folder=str(static_dir()), "
    "template_folder=str(templates_dir()))\n"
)
if source.count(marker) != 1:
    raise SystemExit("Expected Flask app initialization was not found exactly once. No files changed.")

health_route = ''' 
# Public, read-only health check for deployment verification.
# It checks database connectivity only; it does not expose credentials or errors.
@app.route('/health', methods=['GET'])
def health_check():
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return jsonify({
            "status": "healthy",
            "database": "healthy",
            "central_mode": engine.dialect.name == "postgresql",
            "version": APP_VERSION,
        }), 200
    except Exception:
        app.logger.exception("Health check: database connection failed")
        return jsonify({
            "status": "unhealthy",
            "database": "unavailable",
        }), 503

'''

updated = source.replace(marker, marker + health_route, 1)
backup_dir = root / ("BACKUP_PRE_HEALTH_ENDPOINT_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
backup_dir.mkdir(parents=True, exist_ok=False)
shutil.copy2(app_path, backup_dir / "app.py")
app_path.write_text(updated, encoding="utf-8", newline="\n")

print("Health endpoint patch applied.")
print(f"Original app.py backed up to: {backup_dir}")
print("Added: GET /health (read-only database SELECT 1)")
print("No database was accessed or modified.")
