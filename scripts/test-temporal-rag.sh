#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
temporal_container="agat-temporal-rag-${$}-$(date +%s)"
cleanup() {
  docker rm --force "${temporal_container}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

npm run build --workspace @agat/coordinator
npm run build --workspace @agat/temporal-worker
docker run --detach --name "${temporal_container}" \
  --publish 127.0.0.1::7233 temporalio/temporal:1.8.1 \
  server start-dev --ip 0.0.0.0 --headless >/dev/null
temporal_port="$(docker port "${temporal_container}" 7233/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
ready=false
for _ in $(seq 1 60); do
  if docker exec "${temporal_container}" temporal operator cluster health --address 127.0.0.1:7233 >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "${ready}" != true ]]; then
  docker logs --tail 30 "${temporal_container}" >&2
  exit 1
fi
AGAT_TEST_TEMPORAL_ADDRESS="127.0.0.1:${temporal_port}" \
  node --import tsx --test "$@" apps/coordinator/test/temporal-rag.integration.test.ts
