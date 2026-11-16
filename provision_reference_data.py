"""Explicitly provision shared checklist reference data in a migrated PostgreSQL DB.

Run from backend/ with DATABASE_URL set to the intended database:
    python provision_reference_data.py --confirm-reference-data

This command inserts missing reference rows using the existing idempotent
seed functions. It does not create or alter database tables.
"""
import argparse
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL must be explicitly set; no default SQLite database is allowed.")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")):
    raise SystemExit("This provisioning command is PostgreSQL-only.")

from sqlalchemy import inspect, text
from models import engine
from seed import ensure_seeded, ensure_safety_checklist, ensure_civil_checklist

EXPECTED_MIGRATION = "20261104_finding_type"
REQUIRED_TABLES = {
    "organizations", "sites", "users", "asset_types",
    "audit_sections", "audit_questions", "observations", "alembic_version",
}


def main():
    parser = argparse.ArgumentParser(description="Provision shared Solar Audit checklist reference data.")
    parser.add_argument(
        "--confirm-reference-data",
        action="store_true",
        help="Confirm that missing shared reference rows may be inserted into this PostgreSQL database.",
    )
    args = parser.parse_args()
    if not args.confirm_reference_data:
        parser.error("No changes made. Re-run with --confirm-reference-data after verifying DATABASE_URL.")

    tables = set(inspect(engine).get_table_names())
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise SystemExit(f"Database schema is incomplete; missing tables: {', '.join(missing)}")

    with engine.connect() as conn:
        revisions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if revisions != [EXPECTED_MIGRATION]:
        raise SystemExit(
            f"Expected Alembic revision {EXPECTED_MIGRATION}; found {revisions!r}. "
            "Review/apply migrations before provisioning reference data."
        )

    print("Database schema checks passed. Adding only missing shared reference rows...")
    equipment_changed = ensure_seeded()
    safety_added = ensure_safety_checklist()
    civil_added = ensure_civil_checklist()
    print(
        "Reference-data provisioning finished. "
        f"Equipment taxonomy changed: {equipment_changed}; "
        f"Safety checkpoints added: {safety_added}; "
        f"Civil checkpoints added: {civil_added}."
    )


if __name__ == "__main__":
    main()
