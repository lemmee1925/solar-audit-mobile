import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# One fresh, isolated database directory shared by the test modules.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="solar_audit_pytest_")
os.environ["SOLAR_AUDIT_DATA_DIR"] = _TEST_DATA_DIR
# Never let a developer shell DATABASE_URL redirect workflow tests to a
# shared staging/production database. models.py will use its default
# SQLite database under the isolated SOLAR_AUDIT_DATA_DIR created above.
os.environ.pop("DATABASE_URL", None)
os.environ["SECRET_KEY"] = "isolated-pytest-secret-not-for-production"