#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="${$}-$(date +%s)"
postgres_container="agat-state-migration-postgres-${suffix}"
postgres_password="admin-state-migration-test"
migration_password="migration-state-test"
system_password="system-state-test"
tenant_password="tenant-state-test"

cleanup() {
  docker rm --force "${postgres_container}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach \
  --name "${postgres_container}" \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB=agat \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD="${postgres_password}" \
  --env AGAT_POSTGRES_MIGRATION_PASSWORD="${migration_password}" \
  --env AGAT_POSTGRES_SYSTEM_PASSWORD="${system_password}" \
  --env AGAT_POSTGRES_TENANT_PASSWORD="${tenant_password}" \
  --volume "${repo_root}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro" \
  postgres:17.6-alpine >/dev/null

postgres_port="$(docker port "${postgres_container}" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
for _ in $(seq 1 60); do
  if docker exec "${postgres_container}" pg_isready --username postgres --dbname agat >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "${postgres_container}" pg_isready --username postgres --dbname agat >/dev/null

docker exec "${postgres_container}" createdb --username postgres agat_rehearsal
docker exec \
  --env POSTGRES_DB=agat_rehearsal \
  "${postgres_container}" \
  /docker-entrypoint-initdb.d/10-agat.sh >/dev/null

AGAT_TEST_MIGRATION_POSTGRES_URL="postgresql://agat_migrator:${migration_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_TEST_MIGRATION_RUNTIME_URL="postgresql://agat_system:${system_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_TEST_MIGRATION_POSTGRES_TENANT_URL="postgresql://agat_tenant:${tenant_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_TEST_REHEARSAL_POSTGRES_URL="postgresql://agat_migrator:${migration_password}@127.0.0.1:${postgres_port}/agat_rehearsal" \
AGAT_TEST_REHEARSAL_RUNTIME_URL="postgresql://agat_system:${system_password}@127.0.0.1:${postgres_port}/agat_rehearsal" \
AGAT_TEST_REHEARSAL_POSTGRES_TENANT_URL="postgresql://agat_tenant:${tenant_password}@127.0.0.1:${postgres_port}/agat_rehearsal" \
node --import tsx --test apps/coordinator/test/sqlite-postgres-migrator.integration.test.ts
