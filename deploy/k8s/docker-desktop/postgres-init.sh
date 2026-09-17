#!/bin/sh

set -eu

: "${AGAT_POSTGRES_SYSTEM_PASSWORD:?AGAT_POSTGRES_SYSTEM_PASSWORD is required}"
: "${AGAT_POSTGRES_MIGRATION_PASSWORD:?AGAT_POSTGRES_MIGRATION_PASSWORD is required}"
: "${AGAT_POSTGRES_TENANT_PASSWORD:?AGAT_POSTGRES_TENANT_PASSWORD is required}"

psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set=migration_password="${AGAT_POSTGRES_MIGRATION_PASSWORD}" \
  --set=system_password="${AGAT_POSTGRES_SYSTEM_PASSWORD}" \
  --set=tenant_password="${AGAT_POSTGRES_TENANT_PASSWORD}" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(867530904);

SELECT format(
  'CREATE ROLE agat_migrator LOGIN BYPASSRLS PASSWORD %L',
  :'migration_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_migrator') \gexec

SELECT format(
  'CREATE ROLE agat_system LOGIN BYPASSRLS PASSWORD %L',
  :'system_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_system') \gexec

SELECT format(
  'CREATE ROLE agat_tenant LOGIN NOBYPASSRLS PASSWORD %L',
  :'tenant_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_tenant') \gexec

SELECT format(
  'ALTER ROLE agat_migrator WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS CONNECTION LIMIT 2 PASSWORD %L',
  :'migration_password'
) \gexec
SELECT format(
  'ALTER ROLE agat_system WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS CONNECTION LIMIT 32 PASSWORD %L',
  :'system_password'
) \gexec
SELECT format(
  'ALTER ROLE agat_tenant WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 32 PASSWORD %L',
  :'tenant_password'
) \gexec

REASSIGN OWNED BY agat_system TO agat_migrator;
SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), current_user) \gexec
ALTER SCHEMA public OWNER TO agat_migrator;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format(
  'REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC, agat_migrator, agat_system, agat_tenant',
  current_database()
) \gexec
SELECT format(
  'GRANT CONNECT ON DATABASE %I TO agat_migrator, agat_system, agat_tenant',
  current_database()
) \gexec
REVOKE CREATE ON SCHEMA public FROM agat_system, agat_tenant;
GRANT USAGE, CREATE ON SCHEMA public TO agat_migrator;
GRANT USAGE ON SCHEMA public TO agat_system, agat_tenant;

-- The separate schema Job installs exact runtime/tenant grants after FORCE RLS exists.
COMMIT;
SQL
