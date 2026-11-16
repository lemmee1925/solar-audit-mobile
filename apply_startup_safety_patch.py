from pathlib import Path
from datetime import datetime
import shutil
import sys

root = Path(__file__).resolve().parent
backend = root / "backend"
required = ["app.py", "seed.py", "bootstrap_central.py"]
if not all((backend / name).is_file() for name in required):
    raise SystemExit("Run this script from the SolarAuditMobile project root (the folder containing backend/).")

changes = {}

# app.py: only auto-seed the local SQLite workflow.
app_path = backend / "app.py"
app = app_path.read_text(encoding="utf-8-sig")
old_app = (
    "from seed import ensure_seeded, ensure_safety_checklist, ensure_civil_checklist\n"
    "ensure_seeded()  # idempotent: populates the shared checklist taxonomy on a brand-new install\n"
    "ensure_safety_checklist()  # idempotent: Safety Audit checklist (also for installs seeded by older builds)\n"
    "ensure_civil_checklist()  # idempotent: Civil / Infrastructure checkpoint audit\n"
)
new_app = (
    "from seed import ensure_seeded, ensure_safety_checklist, ensure_civil_checklist\n"
    "# SQLite remains self-initializing for the offline workflow. PostgreSQL\n"
    "# schema and shared reference data are provisioned explicitly, not at startup.\n"
    "if engine.dialect.name == 'sqlite':\n"
    "    ensure_seeded()\n"
    "    ensure_safety_checklist()\n"
    "    ensure_civil_checklist()\n"
)
if app.count(old_app) != 1:
    raise SystemExit("Safety check failed: expected startup seed block not found exactly once in app.py.")
changes["app.py"] = app.replace(old_app, new_app, 1)

# seed.py: seed functions insert reference rows only; they never create schema.
seed_path = backend / "seed.py"
seed = seed_path.read_text(encoding="utf-8-sig")
old_import = "from models import Base, engine, Session, AssetType, AuditSection, AuditQuestion\n"
if seed.count(old_import) != 1:
    raise SystemExit("Safety check failed: expected seed.py import not found exactly once.")
seed = seed.replace(old_import, "from models import Session, AssetType, AuditSection, AuditQuestion\n", 1)
old_create = "    Base.metadata.create_all(engine)\n"
if seed.count(old_create) != 1:
    raise SystemExit("Safety check failed: expected seed.py create_all call not found exactly once.")
seed = seed.replace(old_create, "", 1)
main_marker = "\n\nif __name__ == '__main__':\n"
if seed.count(main_marker) != 1:
    raise SystemExit("Safety check failed: seed.py command-line block not found exactly once.")
seed = seed.split(main_marker, 1)[0] + (
    "\n\n# Reference-data provisioning is intentionally invoked through\n"
    "# provision_reference_data.py with an explicit confirmation flag.\n"
)
changes["seed.py"] = seed

# bootstrap_central.py: only create central records in an already migrated DB.
boot_path = backend / "bootstrap_central.py"
boot = boot_path.read_text(encoding="utf-8-sig")
old_boot_import = "from models import Base, Session, Organization, Site, User, engine\n"
if boot.count(old_boot_import) != 1:
    raise SystemExit("Safety check failed: expected bootstrap_central.py import not found exactly once.")
boot = boot.replace(
    old_boot_import,
    "from sqlalchemy import inspect\nfrom models import Session, Organization, Site, User, engine\n",
    1,
)
old_boot_create = "    Base.metadata.create_all(engine)\n"
new_boot_check = (
    "    # Central bootstrap creates records only; schema is managed by Alembic.\n"
    "    if engine.dialect.name != 'postgresql':\n"
    "        raise SystemExit('Central bootstrap is intended for PostgreSQL only.')\n"
    "    required_tables = {'organizations', 'sites', 'users', 'alembic_version'}\n"
    "    existing_tables = set(inspect(engine).get_table_names())\n"
    "    missing_tables = sorted(required_tables - existing_tables)\n"
    "    if missing_tables:\n"
    "        raise SystemExit(\n"
    "            'Database schema is not ready. Apply the approved Alembic migrations first. '\n"
    "            f'Missing tables: {\", \".join(missing_tables)}'\n"
    "        )\n"
)
if boot.count(old_boot_create) != 1:
    raise SystemExit("Safety check failed: expected bootstrap create_all call not found exactly once.")
changes["bootstrap_central.py"] = boot.replace(old_boot_create, new_boot_check, 1)

# Validate all source fragments before the first write.
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = root / f"BACKUP_PRE_STARTUP_SAFETY_{stamp}"
backup.mkdir(parents=True, exist_ok=False)
for name in changes:
    shutil.copy2(backend / name, backup / name)

try:
    for name, content in changes.items():
        (backend / name).write_text(content, encoding="utf-8", newline="\n")
    shutil.copy2(Path(__file__).with_name("provision_reference_data.py"),
                 backend / "provision_reference_data.py")
except Exception:
    for name in changes:
        shutil.copy2(backup / name, backend / name)
    raise

print("Startup safety patch applied.")
print(f"Original files backed up to: {backup}")
print("Changed: backend/app.py, backend/seed.py, backend/bootstrap_central.py")
print("Added: backend/provision_reference_data.py")
print("No database was accessed or modified.")
