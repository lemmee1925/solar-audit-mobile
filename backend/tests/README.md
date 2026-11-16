# Test scripts

These drive a real running instance of the app via HTTP (not mocks) —
they're what was actually run to produce `TEST_REPORT.md`.

## Running them

1. Start the app pointed at a throwaway data directory so tests don't
   touch your real site data:
   ```
   set SOLAR_AUDIT_DATA_DIR=C:\temp\solar_audit_test    (Windows)
   export SOLAR_AUDIT_DATA_DIR=/tmp/solar_audit_test    (Linux/Mac)
   set PORT=5010
   python app.py
   ```
2. In another terminal, with the same venv active:
   ```
   pip install requests pillow
   python tests/smoke_test.py
   ```

Each script assumes a fresh, unconfigured database (it runs `/setup`
itself) — delete the data directory between runs, or use a new one.

- `smoke_test.py` — full single-site workflow: setup → login → plant →
  audit → equipment → photo → submit → PDF → export → backup.
- `plant_assignment_test.py` — run immediately after `smoke_test.py` on the
  *same* server (it reuses the admin login and adds more plants/users) —
  verifies a technician assigned to one plant cannot see/create/access
  another plant's data at the same site.
- `plant_specs_test.py` — run after `plant_assignment_test.py` on the same
  server — verifies plant specifications save/persist/auto-fill into new
  audits, and verifies force-delete (including the wrong-confirmation
  refusal case).
- `isolation_test.py` — needs **two** running servers on different ports
  with different `SOLAR_AUDIT_DATA_DIR`s (simulating two separate site
  laptops); edit the `SITE_A`/`SITE_B` URLs at the top if your ports differ.
  Verifies cross-site URL access is blocked and a mismatched-site restore
  is refused.
- `restore_positive_test.py` — needs the same Site A server from
  `isolation_test.py` plus a third fresh server (same site ID, simulating a
  replacement laptop); verifies restoring a backup from the *same* site
  actually works end-to-end.
