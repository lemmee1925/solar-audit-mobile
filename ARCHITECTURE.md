# Architecture

> **Authoritative Production Architecture:** Production deployments use **Central Multi-Site PostgreSQL Mode** (`CENTRAL_MODE=1`, `DATABASE_URL=postgresql+psycopg://...`, `backend/production_server.py` / `Dockerfile`) with Waitress WSGI (`24` threads) and PostgreSQL connection pooling (`pool_size=20`, `max_overflow=30`). See `CENTRAL_DEPLOYMENT.md` and `deployment/azure/README.md`.
>
> The "Offline Site Edition" (SQLite) documentation below describes the legacy standalone single-laptop mode and local test fallback.

---

# Architecture — Offline Site Edition (Legacy / Standalone)

## 1. Overview

This is the same Flask/SQLAlchemy/Flask-Login/ReportLab application as
before, adapted to run as a single-tenant, offline, per-site desktop app
instead of a shared multi-user Postgres web service. The core audit
workflow (plants → audits → equipment checklist → responses/photos →
submit → PDF) is unchanged; what's new is everything around it: site
identity, local storage, packaging, backup/restore, and export.

## 2. Database: PostgreSQL → SQLite

**Why SQLite is appropriate here:** each site installation is genuinely
single-tenant — one laptop, one site's data, no concurrent multi-machine
writers. SQLite's concurrency model (single-writer, file-based) is a good
fit; there is no scenario in this deployment where Postgres's extra
capability (many concurrent writers, network access, replication) is
needed. The trade-off is the one PostgreSQL-only feature actually in use:
the `postgresql.UUID` column type.

**What changed:** every primary/foreign key that used `postgresql.UUID` (on
`Plant`, `Asset`, `Audit`, `AuditSiteDetail`, `AuditAsset`, `AuditResponse`,
`AuditResponsePhoto`, `CorrectiveAction`) now uses a plain `String(36)`
storing `str(uuid.uuid4())` — the same pattern `User` already used before
this change. This is portable across SQLite/Postgres/MySQL and required no
change to how IDs are generated or compared, only to the column type
declaration and (in a couple of routes) making sure a raw `uuid.UUID` object
is never passed into a query filter or model field — SQLite's driver expects
a plain string, not a `UUID` object, unlike psycopg2 which handled that
implicitly. (This was caught by testing, not just code review — see
`TEST_REPORT.md`.)

**Migration risk:** none for a brand-new install (this whole architecture is
for new offline installs, not an in-place migration of an existing Postgres
production database). If you ever need to migrate an *existing* Postgres
deployment's data into a site's SQLite file, that's a one-time data export/
import job, not something this codebase needs to do automatically.

**Database location:** `data/solar_audit.db` under the app's data directory
(`paths.py` resolves this to `%PROGRAMDATA%\SolarAudit\data\` when installed,
or a local `data/` folder next to the source in development).

## 3. Site isolation — two layers

### Layer 1: Physical isolation (free, by construction)

Every site gets its own installed copy of the app, each with its own SQLite
file on its own laptop. There is no network path between installs — Site A's
process cannot read Site B's file because they're never on the same machine
or network at all. This isolation doesn't depend on any application code
being correct; it's enforced by the filesystem/OS boundary between separate
installations.

### Layer 2: Application-level scoping (defense in depth)

Even though Layer 1 already prevents cross-site access in normal operation,
every relevant table also carries a `site_id` column (`Plant`, `Audit`,
`User`), and every route that reads or writes one of these — and anything
reachable through them (`AuditSiteDetail`, `AuditAsset`, `AuditResponse`,
`AuditResponsePhoto`, `CorrectiveAction`, all scoped transitively via their
`audit_id`/`asset_id` foreign keys) — filters by the current install's
`site_id`, not just by primary key. `site_scope.py` centralizes this:
`get_audit_or_404(session, audit_id)` and equivalents always add
`.filter_by(site_id=current_site().site_id)` alongside the ID lookup, so a
route can never accidentally do the unsafe `Model.query.get(id)` pattern
without a site check.

This matters for a narrower but real set of scenarios than "hacking between
sites": a backup restored onto the wrong site's laptop by mistake, or (later)
a central-office import process that might combine multiple sites' exports
into one database. Requirement #7 (URL/ID manipulation) is satisfied by this
same mechanism — changing `/audit/123` to `/audit/124` in the URL only works
if `124` both exists *and* belongs to the current site; otherwise the route
returns a "not found" response, never another site's data.

Site identity itself (`site_config.py` / `config/site_config.json`) is
locked after the one-time `/setup` wizard: the `/setup` route refuses to run
again once a config file exists (redirects to login instead), so an ordinary
user has no in-app way to change which site's data they're looking at.
Reassigning a site requires an administrator to delete the config file on
disk and re-run setup — a deliberate, out-of-band, hard-to-do-by-accident
action.

## 3b. Plant-level access control (within a site)

A single site can have more than one plant, and different people may be
responsible for different plants — a technician for Plant A shouldn't see
or be able to audit Plant B, even though both belong to the same site
install. This is a separate, finer-grained layer on top of site isolation,
implemented via `UserPlantAssignment` (`user_id`, `plant_id`):

- **Admin is always unrestricted** — sees and can manage every plant at the
  site, regardless of any assignment rows. This is checked in code
  (`role == 'admin'` short-circuits to "no restriction"), not by giving
  admin a row for every plant.
- **Engineer and technician are restricted to their assigned plant(s)** —
  set by an admin when creating or editing their account (a checklist of
  the site's plants on the Users form). A user with no plants assigned yet
  sees nothing (fails safe) rather than accidentally seeing everything.
- The restriction is enforced at the same central choke point as site
  isolation: `get_audit_or_404()` in `app.py` checks plant assignment right
  after site assignment, so every route that reads an audit through it
  (view, site-details, equipment, submit, report, export, delete) is
  covered by one check. List views (`plants`, `audits`, `reports`,
  `dashboard`, the audit-creation plant dropdown) separately filter their
  queries by the same assignment set.
- Creating a new audit for a plant you're not assigned to is blocked at
  creation time too (`audit_new_post`), not just at read time — posting a
  foreign plant_id directly is rejected with "You are not assigned to this
  plant."

## 4. Roles and permissions

| Role | Sees / can audit | Can delete audits | Can manage plants/users/settings |
|---|---|---|---|
| `admin` | Every plant at the site | Yes | Yes |
| `engineer` | Only their assigned plant(s) | No | No |
| `technician` | Only their assigned plant(s) | No | No |

`engineer` and `technician` currently have identical permissions — both are
restricted to their assigned plants and cannot delete audits or manage
plants/users/settings. The role field is preserved as a distinct value
(rather than merged into one role) in case a future requirement needs to
tell them apart, but nothing in the code currently branches on the
difference between the two. Audit and report deletion is `admin`-only.

## 4b. Deletion controls

- **Audits/reports**: `admin`-only (`audit_delete`, `report_delete`). This
  was previously also available to `engineer`; tightened per product
  decision so only admin can remove submitted audit history.
- **Plants**: the normal delete button (`plant_delete`) refuses to run if
  the plant has any live (non-deleted) audits — this has been the behavior
  since before this offline conversion and is unchanged. A separate
  **force delete** (`plant_force_delete`, admin-only) permanently removes
  the plant *and* every audit against it (live or already soft-deleted),
  including photos on disk and corrective actions, with no soft-delete
  step. It requires typing the plant's exact name as confirmation — this is
  specifically to make it hard to trigger with a single accidental click,
  not general security theatre. There is no undo except restoring from a
  backup taken beforehand.

## 5. Photo storage

Unchanged in mechanism — Pillow was already used, paths were already
relative and dynamically resolved. Photos live under
`photos/<audit_id>/<asset_id>/<filename>` inside the data directory (renamed
from the original `uploads/` for clarity now that reports/exports/backups
are siblings under the same data root — see `paths.py`).

## 6. PDF generation

Unchanged in mechanism (ReportLab, fully offline already). Additions: the
human-readable `audit_number` and the application version are now included
in the generated report, and `generate_pdf()` accepts an optional `site_id`
for the same defense-in-depth scoping described in section 3 — the PDF route
re-validates the audit belongs to the current site before rendering,
even though `get_audit_or_404` already guarantees this upstream.

## 7. Audit identification

Format: `AUD-<year>-<site_id>-<sequence>`, e.g. `AUD-2026-MH001-000123`. The
underlying UUID remains the real primary key (nothing that references
`Audit.id` needed to change); `audit_number` is a separate, indexed column
assigned once at audit creation, computed as the next unused sequence number
for that site+year. This is what's shown in the UI and included in exports —
the UUID is an internal implementation detail.

## 7b. Plant specifications (entered once, not per audit)

The equipment counts and site details that used to be typed from scratch on
every audit (building name, roof type, city, state, block count, DC/AC
capacity, inverter/transformer/panel types and counts, earthing details)
are now stored once on the `Plant` record — as the plant's master
specification — editable via a dedicated "Edit Specs" page (admin only).

When a new audit is created, `audit_new_post` copies the plant's current
master values into the new `Audit` row and its linked `AuditSiteDetail` row
as the starting point. The `audit_basic.html` form also auto-fills its own
fields client-side (via a small JS handler keyed off `data-*` attributes on
each plant `<option>`) as soon as a plant is selected. In both cases these
are only *defaults* — every field remains fully editable on that specific
audit afterward, since something can genuinely differ from the plant's
usual specification on a given visit (e.g. a transformer was swapped out).
Editing a plant's master spec later has no retroactive effect on audits
already created — it only changes the starting point for audits created
from that point on.

## 8. Export package

`GET /audit/<id>/export` builds a ZIP containing exactly `audit_report.pdf`,
`audit_data.json`, and the audit's photos — nothing else from the
application. `audit_data.json` is built directly from the same models the
PDF uses (see `_build_audit_json` in `app.py`), including `schema_version`
and `application_version`, so a future central-office importer can consume
it without parsing the PDF.

## 9. Backup & restore

**Backup**: a ZIP of the database file, the entire photos directory, and
`site_config.json`, named with a timestamp so successive backups never
overwrite each other.

**Restore**: validates the ZIP structurally, extracts to a temp directory
first (never trusts archive member paths directly — protects against a
zip-slip path-traversal attack from a malicious/corrupted ZIP), reads the
site ID from the *backup's* config file, and **refuses** the restore
outright if it doesn't match the current install's site ID. Only if that
check passes does it take an automatic safety backup of the current data,
then replace the live database and photos. This means a backup can only
ever be restored onto an install of the same site — there's no override or
"force" option, by design (requirement #24/#26).

## 10. Application versioning

`version.py` defines `APP_VERSION` and `EXPORT_SCHEMA_VERSION` separately,
since the export JSON format and the application build number can evolve
independently. Both are shown on the About page and included in every
generated PDF and export.

## 11. Packaging / installer

- **PyInstaller** (`solar_audit.spec`) bundles the Python runtime, Flask,
  SQLAlchemy, ReportLab, Pillow, and the app's own code (`templates/`,
  `static/`, including the vendored fonts) into a single `dist/SolarAudit/`
  folder with `SolarAudit.exe` as the entry point. No separate Python or
  pip install is needed on the site laptop.
- **`launcher.py`** is the actual entry point (not `app.py` directly): it
  picks a free local port (defaulting to 5000, trying upward if occupied),
  starts Flask in a background thread, opens the user's default browser to
  it, and guards against double-launching a second server over the same
  SQLite file if the shortcut is clicked twice.
- **Inno Setup** (`installer.iss`) wraps that PyInstaller output into
  `SolarAuditSetup.exe` — a normal Windows installer with a desktop shortcut,
  Start Menu entry, and uninstaller. Uninstalling deliberately does *not*
  delete the site's data in `%PROGRAMDATA%\SolarAudit` — that's a
  self-contained safety choice, not an accident of how Inno Setup works by
  default.
- **`paths.py`** distinguishes dev mode (running from source) from frozen
  mode (`sys.frozen`, i.e. running as the PyInstaller-built exe) and resolves
  the writable data directory accordingly: a local `data/`-style folder next
  to the source in dev, `%PROGRAMDATA%\SolarAudit` when installed. The
  installed application folder itself (Program Files) is never written to.

## 12. Internet dependency: what was found and fixed

The only network dependency found anywhere in the codebase was `base.html`
loading Inter/Fraunces/IBM Plex Mono from Google Fonts' CDN. Everything else
(PDF generation, photo handling, static assets, JS) was already fully local.
Fix: the exact font weights in use were fetched once (via the `@fontsource`
npm packages, during development — not at runtime) and committed as local
`.woff2` files under `static/fonts/`, with `base.html` rewritten to use local
`@font-face` rules instead of the CDN `<link>` tags. The running application
now makes zero network requests of any kind.
