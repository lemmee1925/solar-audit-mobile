-- Run as PostgreSQL administrator.
-- Creates the non-production staging database/user.
CREATE USER solar_audit_staging WITH PASSWORD 'CHANGE_ME_STAGING_PASSWORD';
CREATE DATABASE solar_audit_staging OWNER solar_audit_staging;
GRANT ALL PRIVILEGES ON DATABASE solar_audit_staging TO solar_audit_staging;
