#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

for command_name in curl jq node npm ollama python3; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  fi
done

port="${AGAT_E2E_PORT:-8793}"
chat_model="${AGAT_E2E_CHAT_MODEL:-llama3.2:latest}"
embedding_model="${AGAT_E2E_EMBEDDING_MODEL:-nomic-embed-text:latest}"
model_url="${AGAT_E2E_MODEL_URL:-http://127.0.0.1:11434/v1}"

if [[ ! "${port}" =~ ^[0-9]+$ ]] || (( port < 1024 || port > 65535 )); then
  echo "AGAT_E2E_PORT must be an integer between 1024 and 65535" >&2
  exit 1
fi
if ! node -e '
  const url = new URL(process.argv[1]);
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost" || url.hostname === "[::1]";
  if (url.protocol !== "http:" || !loopback || url.username || url.password) process.exit(1);
' "${model_url}"; then
  echo "AGAT_E2E_MODEL_URL must point to loopback; this test never sends documents to a remote model" >&2
  exit 1
fi

ollama show "${embedding_model}" >/dev/null
ollama show "${chat_model}" >/dev/null

e2e_dir="$(mktemp -d /tmp/agat-real-rag-e2e.XXXXXX)"
coordinator_pid=""
admin_token="agat-e2e-admin-token"
enrollment_token="agat-e2e-enrollment-token"
coordinator_url="http://127.0.0.1:${port}"

cleanup() {
  local status=$?
  if [[ -n "${coordinator_pid}" ]] && kill -0 "${coordinator_pid}" >/dev/null 2>&1; then
    kill "${coordinator_pid}" >/dev/null 2>&1 || true
    wait "${coordinator_pid}" 2>/dev/null || true
  fi
  if (( status == 0 )) && [[ "${e2e_dir}" == /tmp/agat-real-rag-e2e.* ]] && [[ -d "${e2e_dir}" ]] && [[ ! -L "${e2e_dir}" ]]; then
    rm -rf -- "${e2e_dir}"
  else
    echo "E2E artifacts: ${e2e_dir}" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [[ "${AGAT_E2E_SKIP_BUILD:-false}" != "true" ]]; then
  npm run build --workspace @agat/coordinator >/dev/null
fi

env \
  AGAT_DB_PATH="${e2e_dir}/agat.db" \
  AGAT_ARTIFACTS_DIR="${e2e_dir}/artifacts" \
  AGAT_ADMIN_TOKEN="${admin_token}" \
  AGAT_ENROLLMENT_TOKEN="${enrollment_token}" \
  AGAT_HOST=127.0.0.1 \
  AGAT_PORT="${port}" \
  AGAT_SEED_DEMO=false \
  AGAT_SERVE_WEB=false \
  AGAT_OIDC_ENABLED=false \
  AGAT_MCP_ENABLED=false \
  node apps/coordinator/dist/server.js >"${e2e_dir}/coordinator.log" 2>&1 &
coordinator_pid=$!

for _attempt in {1..80}; do
  if curl --fail --silent "${coordinator_url}/api/v1/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${coordinator_pid}" >/dev/null 2>&1; then
    echo "Coordinator exited before becoming healthy" >&2
    sed -n '1,160p' "${e2e_dir}/coordinator.log" >&2
    exit 1
  fi
  sleep 0.25
done
curl --fail --silent --show-error "${coordinator_url}/api/v1/health" >/dev/null

admin_headers=(
  -H "X-Agat-Admin-Token: ${admin_token}"
  -H "Content-Type: application/json"
)

collection_payload="$(jq -n \
  --arg embeddingModel "${embedding_model}" \
  '{name:"Real Ollama E2E",description:"Real embedding and retrieval contract",embeddingModel:$embeddingModel,chunkSize:400,chunkOverlap:40,topK:3}')"
collection_response="$(curl --fail --silent --show-error \
  -X POST "${coordinator_url}/api/v1/knowledge/collections" \
  "${admin_headers[@]}" --data-binary "${collection_payload}")"
collection_id="$(jq -er '.id' <<<"${collection_response}")"

document_content=$'# Политика хранения данных\n\nВсе клиентские данные АГАТ хранятся только в локальном контуре заказчика. Передача текстов документов во внешние облачные model endpoints запрещена. Резервные копии SQLite создаются ежедневно и шифруются ключом организации.\n\n# Восстановление\n\nЦелевое время восстановления сервиса RTO составляет 45 минут. Целевая точка восстановления RPO составляет 24 часа. После восстановления оператор обязан проверить целостность knowledge collections и hashes документов.\n\n# Работа агентов\n\nEmbeddings вычисляются outbound-only worker на локальном Ollama endpoint. Coordinator хранит chunks, vectors и provenance, но самостоятельно к модели не обращается. Каждый использованный фрагмент должен иметь provenance-маркер источника.'
document_payload="$(jq -n \
  --arg content "${document_content}" \
  '{name:"operations-handbook.md",sourceUri:"agat://e2e/operations-handbook",mediaType:"text/markdown",content:$content}')"
curl --fail --silent --show-error \
  -X POST "${coordinator_url}/api/v1/knowledge/collections/${collection_id}/documents" \
  "${admin_headers[@]}" --data-binary "${document_payload}" >/dev/null

worker_args=(
  workers/agat_worker.py
  --coordinator "${coordinator_url}"
  --enrollment-token "${enrollment_token}"
  --name real-ollama-e2e
  --models "${chat_model}"
  --embedding-models "${embedding_model}"
  --model-url "${model_url}"
  --model-discovery off
  --credentials "${e2e_dir}/worker.json"
  --poll-interval 0.2
  --no-web
  --once
)

python3 "${worker_args[@]}"

knowledge_export="$(curl --fail --silent --show-error \
  "${coordinator_url}/api/v1/knowledge/export" "${admin_headers[@]}")"
document_status="$(jq -er '.collections[0].documents[0].status' <<<"${knowledge_export}")"
embedding_dimensions="$(jq -er '.collections[0].documents[0].chunks[0].embeddingDimensions' <<<"${knowledge_export}")"
if [[ "${document_status}" != "ready" ]] || [[ ! "${embedding_dimensions}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Document was not indexed by the real embedding model" >&2
  exit 1
fi
jq -e --argjson dimensions "${embedding_dimensions}" '
  [.collections[0].documents[0].chunks[].embeddingDimensions] |
  length > 0 and all(. == $dimensions)
' <<<"${knowledge_export}" >/dev/null

run_payload="$(jq -n \
  --arg collectionId "${collection_id}" \
  '{name:"Real RAG answer",input:"Ответь строго по локальному handbook: где хранятся клиентские данные и чему равен RTO? Дай краткий ответ и обязательно поставь provenance-маркер рядом с каждым фактом.",approvalRequired:false,agentIds:["collector"],knowledgeCollectionIds:[$collectionId],resultDestination:"history"}')"
run_response="$(curl --fail --silent --show-error \
  -X POST "${coordinator_url}/api/v1/runs" \
  "${admin_headers[@]}" --data-binary "${run_payload}")"
run_id="$(jq -er '.id' <<<"${run_response}")"

python3 "${worker_args[@]}"

run_result="$(curl --fail --silent --show-error \
  "${coordinator_url}/api/v1/runs/${run_id}" "${admin_headers[@]}")"
trace_result="$(curl --fail --silent --show-error \
  "${coordinator_url}/api/v1/runs/${run_id}/trace" "${admin_headers[@]}")"

jq -e '.status == "completed" and .stages[0].status == "completed"' <<<"${run_result}" >/dev/null
stage_output="$(jq -er '.stages[0].output' <<<"${run_result}")"
if ! grep -Eq '\[K[0-9]+\]' <<<"${stage_output}"; then
  echo "The real model answer did not contain a provenance marker" >&2
  exit 1
fi

retrieval_count="$(jq '[.events[] | select(.type == "knowledge.retrieved")] | length' <<<"${trace_result}")"
retrieval_hits="$(jq '[.events[] | select(.type == "knowledge.retrieved")][0].data.hits | length' <<<"${trace_result}")"
query_dimensions="$(jq -er '[.events[] | select(.type == "knowledge.retrieved")][0].data.queries[0].dimensions' <<<"${trace_result}")"
if [[ "${retrieval_count}" != "1" ]] || (( retrieval_hits < 1 )) || [[ "${query_dimensions}" != "${embedding_dimensions}" ]]; then
  echo "Retrieval trace does not match the indexed vectors" >&2
  exit 1
fi
jq -e '
  [.events[] | select(.type == "knowledge.retrieved")][0].data as $retrieval |
  ($retrieval.queries[0] | has("vector") | not) and
  ($retrieval.queries[0].vectorSha256 | length == 64) and
  any($retrieval.hits[]; .provenance.sourceUri == "agat://e2e/operations-handbook" and (.provenance.chunkSha256 | length == 64))
' <<<"${trace_result}" >/dev/null

best_score="$(jq -er '[.events[] | select(.type == "knowledge.retrieved")][0].data.hits[0].score' <<<"${trace_result}")"
printf 'Real Ollama RAG E2E passed\n'
printf '  embedding model: %s · dimensions: %s\n' "${embedding_model}" "${embedding_dimensions}"
printf '  chat model: %s · retrieval hits: %s · best score: %s\n' "${chat_model}" "${retrieval_hits}" "${best_score}"
printf '  run: %s · provenance marker present\n' "${run_id}"
