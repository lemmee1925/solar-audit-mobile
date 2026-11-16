# Solar Audit v11 — Global HQ Administrator

v11 adds a true global `hq_admin` role for central deployments.

- `hq_admin` has `users.site_id = NULL` and is not tied to a site.
- HQ has a global site portfolio at `/hq`.
- HQ can create/edit/activate/deactivate sites.
- HQ can create and manage users across sites.
- HQ selects an active site from the top-bar site selector to operate the existing audit UI.
- `site_admin` remains restricted to its own site.
- Legacy `admin` remains accepted as a backwards-compatible site-admin alias.
- Engineers and technicians remain site/plant scoped.
- A PostgreSQL/Alembic migration changes `users.site_id` to nullable.
