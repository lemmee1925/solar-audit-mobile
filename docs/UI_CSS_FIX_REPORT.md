# UI CSS Fix — 2026-10-06

The existing page-specific CSS was defined through Jinja `extra_style` blocks. To make the existing UI styling deterministic across the normal Flask renderer and the PWA/offline cache, the CSS from the existing templates is now also emitted as `frontend/static/page-styles.css`.

`base.html` loads this stylesheet after `mobile.css`. The service worker cache was bumped from v7 to v8 and now precaches `page-styles.css`. No audit/database/UI markup was rewritten.

Affected existing page styles: dashboard, observations, observation detail, equipment audit, safety audit, and mobile home.
