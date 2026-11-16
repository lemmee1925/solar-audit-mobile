-- RUN ONLY DURING PRODUCTION CUTOVER, as PostgreSQL administrator.
-- Never point staging/test data here.
CREATE USER solar_audit_prod WITH PASSWORD 'CHANGE_ME_PRODUCTION_PASSWORD';
CREATE DATABASE solar_audit_prod OWNER solar_audit_prod;
GRANT ALL PRIVILEGES ON DATABASE solar_audit_prod TO solar_audit_prod;
