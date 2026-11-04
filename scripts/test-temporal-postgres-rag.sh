#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
postgres_container="agat-temporal-postgres-rag-${$}-$(date +%s)"
cleanup() {
  docker rm --force --volumes "${postgres_container}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The schema Job and runtime use the same restricted roles as Fleet/HA tests.
npm run build --workspace @agat/coordinator
docker run --detach --name "${postgres_container}" \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB=agat --env POSTGRES_USER=postgres --env POSTGRES_PASSWORD=admin-temporal-rag-test \
  --env AGAT_POSTGRES_MIGRATION_PASSWORD=migration-temporal-rag-test \
  --env AGAT_POSTGRES_SYSTEM_PASSWORD=system-temporal-rag-test \
  --env AGAT_POSTGRES_TENANT_PASSWORD=tenant-temporal-rag-test \
  --volume "${repo_root}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro" \
  postgres:17.6-alpine >/dev/null
postgres_port="$(docker port "${postgres_container}" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
ready=false
for _ in $(seq 1 60); do
  if docker exec "${postgres_container}" pg_isready --host 127.0.0.1 --username postgres --dbname agat >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "${ready}" != true ]]; then
  docker logs --tail 30 "${postgres_container}" >&2
  exit 1
fi
export AGAT_POSTGRES_URL="postgresql://agat_system:system-temporal-rag-test@127.0.0.1:${postgres_port}/agat"
export AGAT_POSTGRES_TENANT_URL="postgresql://agat_tenant:tenant-temporal-rag-test@127.0.0.1:${postgres_port}/agat"
export AGAT_POSTGRES_SSL_MODE=disable AGAT_REGION=eu-test-1 AGAT_RESIDENCY_DOMAIN=eu-test
export AGAT_POSTGRES_EXPECTED_REPLICAS=1 AGAT_POSTGRES_POOL_MAX=2
export AGAT_POSTGRES_ADMISSION_CONCURRENCY=2 AGAT_POSTGRES_ADMISSION_DURATION_MS=500
export AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS=10 AGAT_POSTGRES_ADMISSION_P99_MS=1000
AGAT_POSTGRES_MIGRATION_URL="postgresql://agat_migrator:migration-temporal-rag-test@127.0.0.1:${postgres_port}/agat" \
  node apps/coordinator/dist/postgres-schema-migrator.js
# The nested launcher owns and removes its own Temporal container. Only RAG
# scenarios opt in to PostgreSQL; the other live scenarios retain their stores.
AGAT_TEST_TEMPORAL_STATE_STORE=postgresql bash scripts/test-temporal-rag.sh --test-name-pattern='Temporal RAG' "$@"
