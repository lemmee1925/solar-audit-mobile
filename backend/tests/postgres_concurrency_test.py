"""Phase 2C PostgreSQL concurrency regression test.

Run from project root with PostgreSQL configured:
  $env:PYTHONPATH="$PWD\backend"
  $env:CENTRAL_MODE="1"
  $env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB"
  python backend/tests/postgres_concurrency_test.py

The test exercises the production audit-number allocator directly with many
independent SQLAlchemy sessions/transactions. It proves that concurrent
writers cannot receive duplicate audit numbers and that counters remain
independent per site.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    raise SystemExit("DATABASE_URL must be a PostgreSQL URL for Phase 2C.")

from sqlalchemy import text

from models import Base, Session, engine, AuditNumberCounter
from site_scope import next_audit_number

YEAR = 2026
SITE_A = "P2C-A"
SITE_B = "P2C-B"
WORKERS = 24


def cleanup():
    db = Session()
    try:
        db.execute(text("DELETE FROM audit_number_counters WHERE site_id IN (:a, :b) AND year=:y"),
                   {"a": SITE_A, "b": SITE_B, "y": YEAR})
        db.commit()
    finally:
        db.close()


def allocate(site_id):
    db = Session()
    try:
        number = next_audit_number(db, site_id, YEAR)
        db.commit()
        return number
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_site(site_id):
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(allocate, site_id) for _ in range(WORKERS)]
        for f in as_completed(futures):
            results.append(f.result())
    return results


def assert_sequence(numbers, site_id):
    expected = [f"AUD-{YEAR}-{site_id}-{i:06d}" for i in range(1, WORKERS + 1)]
    actual = sorted(numbers)
    if actual != expected:
        raise AssertionError(
            f"{site_id}: expected exact sequence 1..{WORKERS}, got {actual}"
        )
    if len(set(numbers)) != WORKERS:
        raise AssertionError(f"{site_id}: duplicate audit numbers detected")


def main():
    Base.metadata.create_all(engine)
    cleanup()

    # Run two sites concurrently so the test covers both contention on the
    # same counter and independent counters for different sites.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(run_site, SITE_A)
        fb = pool.submit(run_site, SITE_B)
        numbers_a = fa.result()
        numbers_b = fb.result()

    assert_sequence(numbers_a, SITE_A)
    assert_sequence(numbers_b, SITE_B)

    if set(numbers_a) & set(numbers_b):
        raise AssertionError("Audit number collision across sites")

    db = Session()
    try:
        counters = {
            row.site_id: row.last_sequence
            for row in db.query(AuditNumberCounter)
            .filter(AuditNumberCounter.site_id.in_([SITE_A, SITE_B]),
                    AuditNumberCounter.year == YEAR)
            .all()
        }
    finally:
        db.close()

    if counters != {SITE_A: WORKERS, SITE_B: WORKERS}:
        raise AssertionError(f"Unexpected final counters: {counters}")

    cleanup()
    print("[OK ] PostgreSQL is the active database")
    print(f"[OK ] {WORKERS} concurrent allocations on {SITE_A} produced unique 1..{WORKERS}")
    print(f"[OK ] {WORKERS} concurrent allocations on {SITE_B} produced unique 1..{WORKERS}")
    print("[OK ] Site counters remained independent")
    print("[OK ] No audit-number collision occurred across sites")
    print("\nPHASE 2C POSTGRESQL CONCURRENCY TEST PASSED")


if __name__ == "__main__":
    main()
