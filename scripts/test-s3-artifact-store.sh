#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="${$}-$(date +%s)"
postgres_container="agat-artifact-postgres-${suffix}"
minio_container="agat-artifact-minio-${suffix}"
network="agat-artifact-${suffix}"
postgres_password="admin-artifact-test"
migration_password="migration-artifact-test"
system_password="system-artifact-test"
tenant_password="tenant-artifact-test"
minio_user="agatminio"
minio_password="agat-minio-test-secret"
bucket="agat-artifact-test"

cleanup() {
  docker rm --force "${postgres_container}" "${minio_container}" >/dev/null 2>&1 || true
  docker network rm "${network}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "${network}" >/dev/null
docker run --detach \
  --name "${postgres_container}" \
  --network "${network}" \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB=agat \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD="${postgres_password}" \
  --env AGAT_POSTGRES_MIGRATION_PASSWORD="${migration_password}" \
  --env AGAT_POSTGRES_SYSTEM_PASSWORD="${system_password}" \
  --env AGAT_POSTGRES_TENANT_PASSWORD="${tenant_password}" \
  --volume "${repo_root}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro" \
  postgres:17.6-alpine >/dev/null

docker run --detach \
  --name "${minio_container}" \
  --network "${network}" \
  --publish 127.0.0.1::9000 \
  --env MINIO_ROOT_USER="${minio_user}" \
  --env MINIO_ROOT_PASSWORD="${minio_password}" \
  minio/minio:RELEASE.2025-09-07T16-13-09Z server /data >/dev/null

postgres_port="$(docker port "${postgres_container}" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
minio_port="$(docker port "${minio_container}" 9000/tcp | sed -E 's/.*:([0-9]+)$/\1/')"

for _ in $(seq 1 60); do
  if docker exec "${postgres_container}" pg_isready --username postgres --dbname agat >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "${postgres_container}" pg_isready --username postgres --dbname agat >/dev/null

for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${minio_port}/minio/health/ready" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent "http://127.0.0.1:${minio_port}/minio/health/ready" >/dev/null

AGAT_POSTGRES_MIGRATION_URL="postgresql://agat_migrator:${migration_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_URL="postgresql://agat_system:${system_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_TENANT_URL="postgresql://agat_tenant:${tenant_password}@127.0.0.1:${postgres_port}/agat" \
AGAT_POSTGRES_SSL_MODE=disable \
AGAT_POSTGRES_EXPECTED_REPLICAS=1 \
AGAT_POSTGRES_POOL_MAX=1 \
AGAT_POSTGRES_ADMISSION_CONCURRENCY=2 \
AGAT_POSTGRES_ADMISSION_DURATION_MS=500 \
AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS=10 \
AGAT_POSTGRES_ADMISSION_P99_MS=1000 \
AGAT_TEST_S3_ENDPOINT="http://127.0.0.1:${minio_port}" \
AGAT_TEST_S3_ACCESS_KEY_ID="${minio_user}" \
AGAT_TEST_S3_SECRET_ACCESS_KEY="${minio_password}" \
AGAT_TEST_S3_BUCKET="${bucket}" \
node --import tsx --test apps/coordinator/test/artifact-store-postgres.integration.test.ts
