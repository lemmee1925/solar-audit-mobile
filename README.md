# Solar Audit Platform

> **Authoritative Production Architecture:** **Central Multi-Site PostgreSQL Mode** (`CENTRAL_MODE=1`, `DATABASE_URL=postgresql+psycopg://...`, served via `backend/production_server.py` or `Dockerfile` on port `8080`) is the **single authoritative production target** for multi-user, HQ, and mobile deployments (see `CENTRAL_DEPLOYMENT.md` and `deployment/azure/README.md`).
>
> **Legacy / Dev-Only Mode:** The standalone single-site SQLite desktop/installer sections below (`backend/run_prod.py`, `installer.iss`, `solar_audit.spec`) are retained for offline single-laptop or local dev/testing only and must **not** be used for multi-user production.

---

## Solar Audit Platform — Offline Site Edition (Legacy / Standalone)

An offline, per-site Windows desktop version of the Solar Audit Platform. Each
installed copy runs entirely on the site laptop — no internet, no cloud
database, no manual Python/PostgreSQL install required — and is permanently
bound to exactly one site's data.

This document covers development setup, building the Windows installer,
installing at a site, and day-to-day operation (backup/restore/export).
See `ARCHITECTURE.md` for how site isolation, storage, and packaging work
under the hood, and `TEST_REPORT.md` for what's been verified and how.

## Photo upload reliability update

- Each equipment photo is uploaded immediately after capture; users do not need to press **Save Asset Audit** for each photo.
- One image is sent per HTTP request (maximum 50 MB per image), and uploads are serialized to avoid concurrent high-memory bursts.
- Pending photos are written to browser IndexedDB before upload. If the network drops, they remain queued and retry when connectivity returns or when the same audit/equipment is reopened.
- Uploads are idempotent using a client-generated photo UUID, so a retry after a lost response does not intentionally create a second copy.
- The server reduces image dimensions before adding the GPS stamp to bound processing memory. Server-side request handling uses Waitress with 8 threads when installed; Flask's threaded server is the fallback.

Install/update dependencies from the `solar_audit` folder before launching:

```powershell
python -m pip install -r requirements.txt
```

For the desktop launcher, run `python backend\launcher.py` from the `solar_audit` folder. To run the backend directly, use `python backend\app.py`.

## 1. Development setup

```bash
python -m venv venv
# Windows: venv\Scripts\activate      Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`. On first run you'll be redirected to `/setup`
to assign this install a Site ID (e.g. `MH001`) and Site Name, and create the
first administrator account. After that, `/setup` is locked — see
`ARCHITECTURE.md` for why.

The database, photos, reports, exports, and backups are written under a data
directory next to the source in dev mode (see `paths.py`). Set
`SOLAR_AUDIT_DATA_DIR=/some/path` to point at a different location (useful
for running multiple isolated instances side by side during development).

The audit checklist (technical equipment checkpoints plus Safety and Civil / Infrastructure site-level checkpoints) seeds itself automatically
on first run — no manual `seed.py` step needed. `seed.py` can still be run
directly (`python seed.py`) but it's idempotent and safe to skip.

## 2. Building the Windows installer

This must be done **on a Windows machine** — PyInstaller builds a
platform-native executable, so a `.exe` cannot be produced from Linux/Mac.

1. On the Windows build machine, with the venv above set up:
   ```
   pip install -r requirements.txt
   pyinstaller solar_audit.spec
   ```
   This produces `dist\SolarAudit\SolarAudit.exe` plus its bundled Python
   runtime and all dependencies — nothing else needs to be installed on the
   site laptop.

2. Install [Inno Setup](https://jrsoftware.org/isinfo.php) (free), then either
   open `installer.iss` in the Inno Setup Compiler and click **Compile**, or
   from the command line:
   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
   ```
   This produces `Output\SolarAuditSetup.exe`.

3. Optional: replace `icon=None` in `solar_audit.spec` with a path to a real
   `.ico` file before building, if you want a custom taskbar/shortcut icon.

## 3. Installing at a site

1. Copy `SolarAuditSetup.exe` to the site laptop (USB, email, shared drive —
   it doesn't need internet to install or run).
2. Double-click it, click through the installer (creates a desktop shortcut).
3. Launch **Solar Audit** from the desktop shortcut. The app starts a local
   server and opens your default browser automatically — no command line.
4. On first launch, the **Site Setup** page appears. Enter:
   - **Site ID** — e.g. `MH001` (2–20 letters/numbers, permanent)
   - **Site Name** — e.g. `ABC Solar Plant`
   - First administrator username/full name/password
5. Log in and start auditing.

The Site ID cannot be changed afterward from inside the app (by design — see
`ARCHITECTURE.md`, requirement #25). If a site genuinely needs reassigning,
an administrator must delete `config\site_config.json` under
`%PROGRAMDATA%\SolarAudit` directly and re-run setup — this is a deliberate
out-of-band action, not something an ordinary user can do by mistake.

## 4. Where data lives

Once installed, all mutable data lives under `%PROGRAMDATA%\SolarAudit\`
(shared across Windows user accounts on that machine, writable without
admin rights on every launch):

```
%PROGRAMDATA%\SolarAudit\
├── config\site_config.json     Site ID/name for this install
├── data\solar_audit.db         SQLite database — all audits, users, etc.
├── photos\                     Uploaded equipment photos
├── reports\                    Generated PDF reports
├── exports\                    (transient — export ZIPs stream to download)
└── backups\                    Backup ZIPs created via Settings
```

The installed application folder (Program Files) is never written to at
runtime — it's treated as read-only code.

## 5. Backing up data

Go to **Settings → Backup → Create Backup Now**. This creates a timestamped
ZIP (database + photos + site config) under the backups folder and lets you
download it — copy it to a USB drive or shared location for safekeeping.
Existing backups are never overwritten.

## 6. Restoring data

Go to **Settings → Restore**, choose a backup ZIP, and confirm. Before doing
anything destructive, the app:

- Validates the ZIP and checks the site ID inside it matches this
  installation's site ID — **a backup from a different site is refused
  outright**, with a clear error, to prevent ever mixing sites' data.
- Takes an automatic safety backup of the current data first.
- Replaces the database and photos, then asks you to restart the app.

This is the supported path for replacing a laptop: set up the new machine
with the *same* Site ID, then restore the old machine's latest backup onto it.

## 7. Exporting an audit for the central office

Open a submitted audit (or find it under **Reports**) and click
**Export Audit (ZIP)**. This produces `<audit-number>.zip` containing:

```
AUD-2026-MH001-000123/
├── audit_report.pdf
├── audit_data.json      structured data for import, not scraped from the PDF
└── photos/
```

Transfer this ZIP to the central office by USB, email, or shared drive.

## 8. Roles & plant assignment

There are three roles: **admin** (full control — all plants, users,
settings, and the only role that can delete audits/reports), **engineer**,
and **technician**. Engineer and technician currently have identical
permissions: both only see and can audit the plant(s) they've been
specifically assigned to, even if the site has other plants.

To assign a user to plant(s): **Users → New User** (or **Edit** an existing
one), pick their role, then check which plant(s) they should see — this
list only appears for engineer/technician (admin is always unrestricted).
A user with no plants checked will see nothing until an admin assigns one.

## 8b. Plant specifications

Rather than retyping a plant's equipment counts and site details on every
single audit, enter them once: **Plants → Edit Specs**. Every new audit
created for that plant afterward starts pre-filled with these values —
still fully editable on that specific audit if something's actually
changed on-site. Editing a plant's specs later doesn't change audits
already created, only new ones going forward.

## 8c. Deleting plants

The normal **Delete** button on the Plants page is blocked if the plant has
any audits recorded against it — this is intentional, to avoid silently
destroying audit history. If you genuinely need to remove a plant along
with all its audits (including submitted ones), admin can use **Force
Delete**, which asks you to type the plant's exact name to confirm. This
cannot be undone except by restoring a backup taken beforehand.

## 8d. Troubleshooting: equipment dropdown is empty

The equipment checklist (asset types + questions, ~21 types / ~139
questions) seeds itself automatically on first launch. If you're running a
database created before this was fixed, or something else caused it to be
empty, go to **Settings** — it shows the current asset-type/question counts
and a **Reseed Checklist** button that fixes this without touching anything
else in the database.

## 9. Project structure

```
app.py              Flask routes (all site-scoped)
models.py           SQLAlchemy models, SQLite engine setup
paths.py            Dev vs. installed (frozen) path resolution
site_config.py       Reads/writes config/site_config.json
site_scope.py        Site-scoped query helpers (get_audit_or_404, etc.)
launcher.py         Desktop entry point: picks a port, starts Flask, opens browser
seed.py             Idempotent checklist seeding, runs automatically at startup
pdf_report.py       ReportLab PDF generation
question_data.py    Equipment checklist taxonomy (shared, not site-specific)
version.py          APP_VERSION / EXPORT_SCHEMA_VERSION
solar_audit.spec    PyInstaller build spec
installer.iss       Inno Setup installer script
templates/, static/ UI (fonts vendored locally — no CDN dependency)
```


## Mobile audit build

This build adds a mobile-first audit entry point at `/mobile` and PWA assets.
On a phone, the audit equipment page uses the rear camera (`capture=environment`)
and browser geolocation. When a photo is captured with GPS available, latitude,
longitude, GPS accuracy and capture time are stored in `audit_response_photos`
and the same information is visibly stamped onto the image using Pillow.

Important deployment note: phone camera and geolocation APIs require a secure
context (HTTPS, or localhost). The Windows offline laptop installer can still
run unchanged, but a phone should connect to a properly hosted HTTPS instance
for production use. This build is the mobile UI/camera/GPS foundation; a full
offline phone data queue + HQ synchronization layer should be added before
production rollout to plants with unreliable internet.

## 9. Observations, closure workflow, scoring and Excel import (v2 features)

**Responses.** Every checkpoint (Technical per-equipment, and the Safety tab) is answered
*Complied / Not Complied / Not Applicable*, with optional remarks and up to 4 camera/gallery photos,
stored against the exact audit + equipment + checkpoint. (The database keeps the legacy values
`pass/fail/na`, so old audits are unchanged.)

**Observations.** Marking a checkpoint *Not Complied* reveals the observation panel: observation text,
severity (Critical/Major/Minor), responsible person/team, target closure date, remarks. An audit
cannot be submitted while a Not Complied checkpoint lacks those details. Changing a checkpoint back to
Complied/N/A *withdraws* its observation (history is kept, nothing is deleted).

**Closure workflow** (menu: *Observations*): owner records action taken, closure date and closure
evidence photo(s) -> *Submit for review* -> an **engineer or admin** (the Quality/Engineering reviewer)
*Accepts* (the only way to reach Closed), *Rejects* (comment required, goes back to the owner) or later
*Reopens* (comment required). *Overdue* is derived from the target date. Every step is logged
(who/when/what) and shown in the observation's History.

**Scoring** is configured in `<data dir>/config/scoring_config.json` (created on first use; edit and
save, no restart). No weightages existed in the project, so defaults are placeholders -
**please confirm them**: severity weights Critical 5 / Major 3 / Minor 1, complied weight 1; rating
bands Excellent >=90, Good >=75, Needs Improvement >=60, else Poor. Score = weighted complied /
(weighted complied + weighted not-complied). A per-checkpoint `weight` (from the Excel) overrides the
complied weight for that checkpoint. The score is frozen onto the audit at submission.

**Report** is generated automatically on submission (and on demand from Reports).

**Importing checkpoints from Excel** (in your existing `backend/migrate_new_questions.py`; with Excel file arguments it is a safe additive import and dry-run by default, with no arguments it still runs the original migrate() exactly as before):

    python migrate_new_questions.py FileA.xlsx FileB.xlsx            # preview, writes nothing
    python migrate_new_questions.py FileA.xlsx FileB.xlsx --apply
    python migrate_new_questions.py Safety.xlsx --safety --apply     # Safety tab checkpoints
    python migrate_new_questions.py FileA.xlsx --create-type PSS --apply   # new equipment type

Every row lands in a CSV report (ADDED / DUPLICATE / POSSIBLE_DUPLICATE / UNMAPPED / EMPTY) next to
the database. Unmapped equipment names are never guessed: map them in
`<data dir>/config/checkpoint_aliases.json` or create the type with `--create-type`, then re-run
(re-runs skip duplicates). Existing checkpoints are never edited or deleted; retired ones are
soft-deleted (`is_active=0`) so past audits and reports keep working.

Scoring and observation-workflow logic lives in the existing `backend/site_scope.py`. Tests: `cd backend && python -m pytest tests/test_workflow.py -v`.


## 10. Mobile PWA offline-first mode

The installed PWA now has a real offline audit workspace at `/static/offline.html`.

Before going to a low-connectivity site, the signed-in user should open **Mobile Audit → Start / Continue Offline Audit → Download / refresh data** while online. This downloads the assigned plants, plant specifications, checklist questions, mandatory/weight metadata and scoring configuration into IndexedDB.

Offline audit state is stored in IndexedDB (not localStorage), including audit drafts, responses, observation details and photo Blobs. Changes are debounced/autosaved while the user types. A persistent sync queue records CREATE/UPDATE/UPLOAD/COMPLETE work. When connectivity returns, the page retries automatically; Service Worker Background Sync is an additional trigger, not the only sync mechanism.

The Flask `/api/offline/*` endpoints use the normal Flask-Login session and site/plant access controls. Passwords and sync tokens are never stored in the browser. Client-generated UUIDs make audit creation and photo upload idempotent, so interrupted retries do not create duplicate audits/photos.

The Service Worker caches the PWA launch shell and offline workspace so the installed app can launch without the server. Server-rendered pages continue to use the existing online workflow and architecture.

