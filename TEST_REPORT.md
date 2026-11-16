# Test Report

All tests below were actually executed against a running instance of the
app (not just reasoned about) — Flask dev server + SQLite, driven via
`requests` sessions simulating real browser form submissions and file
uploads. Scripts are included so these can be re-run.

## 1. Full workflow smoke test (`smoke_test.py`)

Single site (MH001), single server, exercises the entire audit lifecycle:

| Step | Result |
|---|---|
| First-run `/setup` wizard reachable and completes | ✅ Pass |
| `/setup` correctly locked/redirects after first configuration | ✅ Pass |
| Login with the account created during setup | ✅ Pass |
| Create a plant | ✅ Pass |
| Create an audit, assigned a plant | ✅ Pass |
| Audit gets a human-readable `AUD-2026-MH001-######` number | ✅ Pass |
| Add equipment (asset) to the audit | ✅ Pass |
| Answer a checklist question with a remark and a photo upload | ✅ Pass |
| Photo visible on the equipment page after upload | ✅ Pass |
| Submit the audit (locks it) | ✅ Pass |
| Generate PDF report, verify it's a real non-trivial PDF | ✅ Pass |
| Export ZIP contains `audit_report.pdf`, `audit_data.json`, and the photo | ✅ Pass |
| Exported JSON has correct `site_id`, `schema_version`, `audit_id` format | ✅ Pass |
| Create a backup, confirm it appears in Settings | ✅ Pass |
| Requesting a nonexistent audit ID doesn't crash (handled gracefully) | ✅ Pass |

**Result: all steps passed** after fixes described in section 4.

## 2. Site isolation test (`isolation_test.py`)

Two fully independent installs, each its own server process and its own
SQLite file — MH001 on port 5000, MH002 on port 5001 — simulating two
separate site laptops:

| Test | Result |
|---|---|
| Each site completes its own independent `/setup` (MH001 / MH002) | ✅ Pass |
| Each site creates its own plant and audit | ✅ Pass |
| Site A's server, given Site B's audit ID directly in the URL, does not return Site B's data (physically separate database — the ID doesn't exist there) | ✅ Pass |
| Site A backup downloaded, then uploaded to Site B's Restore form | ✅ Pass — **refused**, exact reason surfaced: "this backup belongs to site MH001, not this installation (MH002)" |
| Site B's own audit data confirmed untouched after the refused restore attempt | ✅ Pass |

## 3. Restore positive-path test (`restore_positive_test.py`)

Simulates the supported "replacement laptop" scenario: same site ID, backup
taken from the original machine, restored onto a freshly set-up install.

| Step | Result |
|---|---|
| Fresh install completes `/setup` as MH001 with a temporary admin account | ✅ Pass |
| Confirmed empty (no audits) before restore | ✅ Pass |
| Restore accepted (matching site ID) — no server error | ✅ Pass |
| Restore correctly invalidates the current session (the temp admin used during setup doesn't exist in the restored database) | ✅ Pass |
| Original site's admin credentials work again after restore | ✅ Pass |
| Original site's audit data present on the "replacement laptop" after restore | ✅ Pass |

## 4. PyInstaller packaging sanity check

A full Linux build was produced from the exact same `solar_audit.spec` used
for the real Windows build, and run standalone (not from source) as a
sanity check of the packaging itself — dependency bundling, hidden imports,
and data-file inclusion. **This does not replace testing the actual Windows
`.exe`**, which must be done on Windows (see section 6), but it does confirm
the spec file, entry point, and data-file bundling are correct before that
step.

| Check | Result |
|---|---|
| PyInstaller build completes without missing-module errors | ✅ Pass (after removing one erroneous unused hidden-import) |
| Frozen binary launches, self-seeds the checklist taxonomy on first run | ✅ Pass |
| Frozen binary writes its database/photos to the external data directory, not inside the bundled app folder | ✅ Pass |
| Full smoke test suite (section 1) re-run against the frozen binary | ✅ Pass, identical results |

## 5. Bugs found and fixed during testing

Testing surfaced several real, non-obvious bugs — this is the point of
running the suite rather than shipping on code review alone:

1. **`uuid.UUID(...)` objects passed into now-`String` columns.** Two routes
   (`audit_equipment_save`, `delete_response_photo`) parsed an incoming ID
   with `uuid.UUID(x)` and then used that `UUID` object directly as a model
   field/filter value. This worked under the old `postgresql.UUID` column
   type (psycopg2 handles `UUID` objects natively) but crashes with SQLite,
   which expects a plain string. Fixed by validating format with `uuid.UUID()`
   but always storing/filtering with `str(x)`. Caught by: equipment-save step
   of the smoke test failing with a 500.

2. **`seed.py` was untouched from the original app.** It tried to create demo
   `Plant`/`Asset` rows without a `site_id`, which now violates the NOT NULL
   constraint — and more importantly, **nothing seeded the equipment
   checklist taxonomy on a fresh install**, so a new site's app would start
   with zero questions and be unusable. Rewritten to seed only the shared,
   non-site-specific taxonomy (asset types + questions), and wired to run
   automatically and idempotently at app startup. Caught by: add-equipment
   step failing because no asset types existed.

3. **`create_admin.py`** (CLI admin-recovery script) also didn't set
   `site_id` on the user it creates. Patched to read the site ID from
   `site_config.json` and refuse to run if `/setup` hasn't been completed yet.
   Caught by: code review once the `seed.py` issue above prompted a check of
   every other script touching `User`/`Plant` directly.

4. **`engine` was never imported into `app.py`**, so the restore route
   crashed with `NameError: name 'engine' is not defined` the moment
   `engine.dispose()` was reached. Caught by: the restore-positive-path test
   failing with a 500 on `POST /settings/restore`.

5. **`login.html` only rendered `error`-category flash messages.** After a
   successful restore, the current session is (correctly) invalidated and
   the user is redirected to `/login` — but the "restore complete" success
   message was silently dropped because the login page never displayed
   `success`-category flashes. Fixed to show both categories. Caught by: the
   restore-positive-path test's assertion on the confirmation text failing
   even though the restore itself had actually succeeded.

6. **Two list templates (`all_audits.html`, `reports.html`) still displayed
   the raw UUID** instead of the new human-readable `audit_number`, even
   though `app.py` was already computing and passing it. Caught by: the
   smoke test's explicit check for `AUD-2026-MH001-` appearing on the
   audits list.

## 6. Follow-up round: plant-level access control, plant specs, deletion changes

A second round of real user feedback surfaced several gaps — some genuine
bugs, some working-as-designed behavior that wasn't explained, and one
overclaim in this document's earlier version (technician being "restricted
to own audits," which was never actually enforced in code). All of the
following were implemented and then verified with real test runs, the same
way as the first round:

| Change | Test | Result |
|---|---|---|
| Risk Exposure panel removed from dashboard; mislabeled "Risk Exposure" section title (was actually the pass-rate trend chart) corrected | Rendered dashboard HTML checked directly for absence/presence of the relevant text | ✅ Pass |
| "Reseed Checklist" safety-net button added to Settings | Verified counts display and the reseed action returns 200 and re-seeds correctly on an already-seeded DB (idempotent) | ✅ Pass |
| Plant-level access control: admin unrestricted; engineer/technician restricted to assigned plant(s) even within the same site | New script `plant_assignment_test.py` — 15 checks covering plants list, audit-creation dropdown, audits list, direct URL access to a foreign plant's audit, and creation attempts against an unassigned plant | ✅ All 15 pass |
| Audit/report deletion restricted to `admin` only (previously `engineer` could too) | Same script: technician's delete attempt returns "Admin access required" | ✅ Pass |
| Delete buttons in `all_audits.html`/`reports.html` hidden from non-admin (previously shown to engineer too, which would have been a dead/confusing button) | Manual template review + confirmed via role-gated rendering | ✅ Pass |
| Plant force-delete (admin-only, requires typing exact plant name, purges live + already-deleted audits and their photos/responses) | New script `plant_specs_test.py` — normal delete still blocked by live audits; force-delete refused with a wrong name; succeeds with the correct name; confirms the plant and its audit are both actually gone afterward | ✅ Pass |
| Plant master specification (equipment counts, site details) entered once per plant, auto-filled into new audits' `Audit` and `AuditSiteDetail` rows, still editable per audit | Same script: specs saved and persisted, `data-*` attributes present on the plant dropdown, and a newly created audit's site-details page shows the plant's saved inverter/transformer values | ✅ Pass |
| Cross-site isolation and backup/restore re-verified after all the above (to make sure the new plant-assignment layer didn't interact badly with site scoping) | Re-ran `isolation_test.py` in full | ✅ Pass, unchanged |

One thing caught along the way: two of the *test scripts themselves* (not
the app) needed a regex fix after `data-*` attributes were added to plant
`<option>` tags for the auto-fill feature — the original regex assumed
`<option value="...">` with nothing else inside the tag. Noted here because
it produced a failing test on the first run that looked like an app
regression and wasn't.

## 7. What has NOT been tested (requires Windows hardware)

- Actually running `SolarAuditSetup.exe` on a clean Windows machine with no
  Python/PostgreSQL pre-installed.
- Desktop shortcut creation and double-click launch experience.
- Behavior with the network adapter physically disabled (the offline claim
  has been validated at the code level — no CDN links, no outbound calls
  anywhere in the codebase — but not with an actual disconnected Windows
  machine).
- Windows-specific path handling (`%PROGRAMDATA%`, `Program Files` install
  path with a space in it) beyond what `pathlib` guarantees generically.

These require the Windows build step described in `README.md` section 2 to
be run on real hardware before first site deployment.
