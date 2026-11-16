# Database lifecycle — Solar Audit Central

The project intentionally uses three separate databases:

- `solar_audit_test`: developer/security/concurrency tests. Disposable.
- `solar_audit_staging`: production-like UAT. Disposable/rebuildable.
- `solar_audit_prod`: real company data. Created fresh at production cutover.

The application creates its tables through SQLAlchemy metadata in the current build. For long-term production operation, schema changes should move to the project's migration process rather than ad-hoc table editing.

Do not copy the test database into production merely because the tests pass. Passing tests prove behavior; production must start with a clean database and a controlled bootstrap of organizations, sites, plants, users and other required master data.
