#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="${$}-$(date +%s)"
postgres_container="agat-region-loss-postgres-${suffix}"
postgres_password="admin-region-loss-test"
migration_password="migration-region-loss-test"
system_password="system-region-loss-test"
tenant_password="tenant-region-loss-test"

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
  # The image's initialization server accepts Unix sockets before the final TCP server starts.
  if docker exec "${postgres_container}" pg_isready --host 127.0.0.1 --username postgres --dbname agat >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "${postgres_container}" pg_isready --host 127.0.0.1 --username postgres --dbname agat >/dev/null

AGAT_POSTGRES_MIGRATION_URL="postgresql://agat_migrator:${migration_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_URL="postgresql://agat_system:${system_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_TENANT_URL="postgresql://agat_tenant:${tenant_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_SSL_MODE=disable \
AGAT_REGION=eu-west-1 \
AGAT_RESIDENCY_DOMAIN=eu \
AGAT_POSTGRES_EXPECTED_REPLICAS=1 \
AGAT_POSTGRES_POOL_MAX=1 \
AGAT_POSTGRES_ADMISSION_CONCURRENCY=2 \
AGAT_POSTGRES_ADMISSION_DURATION_MS=500 \
AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS=10 \
AGAT_POSTGRES_ADMISSION_P99_MS=1000 \
node --import tsx --test apps/coordinator/test/region-loss-dr-postgres.integration.test.ts
