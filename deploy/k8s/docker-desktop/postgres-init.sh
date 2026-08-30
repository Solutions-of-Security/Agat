#!/bin/sh

set -eu

: "${AGAT_POSTGRES_SYSTEM_PASSWORD:?AGAT_POSTGRES_SYSTEM_PASSWORD is required}"
: "${AGAT_POSTGRES_TENANT_PASSWORD:?AGAT_POSTGRES_TENANT_PASSWORD is required}"

psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set=system_password="${AGAT_POSTGRES_SYSTEM_PASSWORD}" \
  --set=tenant_password="${AGAT_POSTGRES_TENANT_PASSWORD}" <<'SQL'
SELECT format(
  'CREATE ROLE agat_system LOGIN BYPASSRLS PASSWORD %L',
  :'system_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_system') \gexec

SELECT format(
  'CREATE ROLE agat_tenant LOGIN NOBYPASSRLS PASSWORD %L',
  :'tenant_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_tenant') \gexec

SELECT format('ALTER ROLE agat_system WITH LOGIN BYPASSRLS PASSWORD %L', :'system_password') \gexec
SELECT format('ALTER ROLE agat_tenant WITH LOGIN NOBYPASSRLS PASSWORD %L', :'tenant_password') \gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE agat TO agat_system, agat_tenant;
GRANT USAGE, CREATE ON SCHEMA public TO agat_system;
GRANT USAGE ON SCHEMA public TO agat_tenant;

-- Agat migrations install exact table grants after FORCE RLS policies exist.
-- The tenant role intentionally receives no blanket default privileges here.
SQL
