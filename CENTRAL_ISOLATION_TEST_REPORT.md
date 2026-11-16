# Central Multi-Site Isolation Test — v7

This release adds a regression harness for the central shared-database model.

## Scenario

- One Flask process
- One database
- Site A: `MH001` / user `alice`
- Site B: `MH002` / user `bob`
- Both sites contain plants, audits, equipment, observations and evidence-photo records.

## Security checks

The test verifies that each user can access their own site while cross-site ID manipulation is rejected for:

- audit site details
- audit equipment
- safety audit
- plant specifications
- audit report generation
- observation detail
- evidence photos
- audit deletion
- offline audit sync using a foreign `plant_id`

It also verifies that a legitimate same-site offline audit sync still succeeds.

## Important test setting

The harness disables CSRF **only inside the test process** so authorization behavior can be tested directly. Production keeps `CSRF_ENABLED=1` by default.

## Run on Windows

From the project root, inside the project's `.venv`:

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:CENTRAL_MODE="1"
$env:SECRET_KEY="central-isolation-test-secret"
$env:CSRF_ENABLED="0"
python backend/tests/central_isolation_test.py
```

Expected final line:

```text
ALL CENTRAL MULTI-SITE ISOLATION TESTS PASSED
```
