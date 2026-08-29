#!/usr/bin/env bash

set -Eeuo pipefail

readonly namespace="agat"
readonly expected_context="docker-desktop"
readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly repo_root="$(cd -- "${script_dir}/.." && pwd)"
readonly manifests_dir="${repo_root}/deploy/k8s/docker-desktop"
readonly image_tag="${AGAT_K8S_IMAGE_TAG:-1.2.0}"
readonly coordinator_image="agat-local/coordinator:${image_tag}"
readonly worker_image="agat-local/worker:${image_tag}"
readonly temporal_worker_image="agat-local/temporal-worker:${image_tag}"

die() {
  printf 'Ошибка: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "не найдена команда $1"
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

gateway_is_ready() {
  local headers
  headers="$(curl --fail --silent --show-error --max-time 2 \
    --dump-header - --output /dev/null \
    "http://127.0.0.1:8787/api/v1/health" 2>/dev/null)" || return 1
  printf '%s' "${headers}" | grep -Eiq '^server:[[:space:]]*kong/'
}

require_command kubectl
require_command docker
require_command curl
require_command openssl
require_command node

current_context="$(kubectl config current-context 2>/dev/null || true)"
[[ "${current_context}" == "${expected_context}" ]] || die \
  "активен kube-context '${current_context:-не задан}', ожидался '${expected_context}'"

kubectl wait --for=condition=Ready node --all --timeout=60s >/dev/null || die \
  "узел Docker Desktop Kubernetes не готов"

node_arch="$(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')"
case "${node_arch}" in
  amd64|arm64) ;;
  *) die "неподдерживаемая архитектура Kubernetes-узла: ${node_arch:-неизвестно}" ;;
esac
readonly platform="linux/${node_arch}"

model_base_url="${AGAT_MODEL_BASE_URL:-http://host.docker.internal:11434/v1}"
web_enabled="${AGAT_WEB_ENABLED:-true}"
web_search_url="${AGAT_WEB_SEARCH_URL:-http://agat-search:8080/search}"
otel_enabled="${AGAT_OTEL_ENABLED:-false}"
otel_exporter_endpoint="${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-${OTEL_EXPORTER_OTLP_ENDPOINT:-}}"
worker_models="${AGAT_WORKER_MODELS:-}"
embedding_models="${AGAT_EMBEDDING_MODELS:-embeddinggemma}"
if [[ -z "${worker_models}" && "${model_base_url}" == "http://host.docker.internal:11434/v1" ]]; then
  ollama_tags="$(curl --fail --silent --show-error --max-time 3 \
    "http://127.0.0.1:11434/api/tags" 2>/dev/null || true)"
  if [[ -n "${ollama_tags}" ]]; then
    worker_models="$(printf '%s' "${ollama_tags}" | node --input-type=module -e '
      let raw = "";
      for await (const chunk of process.stdin) raw += chunk;
      const models = JSON.parse(raw).models ?? [];
      const supportsCompletion = (model) =>
        !/embed/i.test(model.name ?? model.model ?? "") &&
        (!Array.isArray(model.capabilities) || model.capabilities.includes("completion"));
      const completionModels = models.filter(supportsCompletion).sort((left, right) =>
        (Number(left.size) || Number.MAX_SAFE_INTEGER) -
        (Number(right.size) || Number.MAX_SAFE_INTEGER),
      );
      const selected = models.find((model) => (model.name ?? model.model) === "qwen3:8b") ??
        completionModels[0];
      if (selected) process.stdout.write(selected.name ?? selected.model);
    ')"
    unset ollama_tags
    if [[ -n "${worker_models}" ]]; then
      printf 'Автоматически выбрана установленная Ollama-модель: %s\n' "${worker_models}"
    fi
  fi
fi
if [[ -z "${worker_models}" ]]; then
  existing_worker_models="$(kubectl get deployment/agat-worker \
    --namespace "${namespace}" \
    --output='jsonpath={.spec.template.spec.containers[?(@.name=="worker")].env[?(@.name=="AGAT_WORKER_MODELS")].value}' \
    2>/dev/null || true)"
  if [[ -n "${existing_worker_models}" ]]; then
    worker_models="${existing_worker_models}"
    printf 'Ollama недоступна; сохранена модель текущего worker: %s\n' "${worker_models}"
  else
    worker_models="qwen3:8b"
    printf '%s\n' \
      'Ollama-модель не определена; используется qwen3:8b. Установите её или задайте AGAT_WORKER_MODELS.'
  fi
  unset existing_worker_models
fi

coordinator_existed=false
worker_existed=false
search_existed=false
gateway_existed=false
keycloak_existed=false
temporal_worker_existed=false
kubectl get deployment/agat-coordinator --namespace "${namespace}" >/dev/null 2>&1 && coordinator_existed=true
kubectl get deployment/agat-worker --namespace "${namespace}" >/dev/null 2>&1 && worker_existed=true
kubectl get deployment/agat-search --namespace "${namespace}" >/dev/null 2>&1 && search_existed=true
kubectl get deployment/agat-gateway --namespace "${namespace}" >/dev/null 2>&1 && gateway_existed=true
kubectl get deployment/agat-keycloak --namespace "${namespace}" >/dev/null 2>&1 && keycloak_existed=true
kubectl get deployment/agat-temporal-worker --namespace "${namespace}" >/dev/null 2>&1 && temporal_worker_existed=true

kubectl apply --filename "${manifests_dir}/namespace.yaml" >/dev/null

secret_exists=false
kubectl get secret/agat-secrets --namespace "${namespace}" >/dev/null 2>&1 && secret_exists=true

read_secret_value() {
  local key="$1"
  local encoded
  encoded="$(kubectl get secret/agat-secrets \
    --namespace "${namespace}" \
    --output="jsonpath={.data.${key}}")" || die "не удалось прочитать ${key} из agat-secrets"
  if [[ -n "${encoded}" ]]; then
    printf '%s' "${encoded}" | base64 --decode
  fi
}

if ! ${secret_exists} || is_true "${AGAT_K8S_ROTATE_SECRETS:-false}"; then
  stored_model_api_key=""
  stored_search_secret=""
  stored_credentials_key=""
  stored_keycloak_postgres_password=""
  stored_keycloak_bootstrap_password=""
  stored_keycloak_demo_password=""
  stored_temporal_internal_token=""
  if ${secret_exists}; then
    [[ -n "${AGAT_ADMIN_TOKEN:-}" ]] || die \
      "для ротации задайте AGAT_ADMIN_TOKEN"
    [[ -n "${AGAT_ENROLLMENT_TOKEN:-}" ]] || die \
      "для ротации задайте AGAT_ENROLLMENT_TOKEN"
    stored_model_api_key="$(read_secret_value "model-api-key")"
    stored_search_secret="$(read_secret_value "search-secret")"
    stored_credentials_key="$(read_secret_value "credentials-key")"
    stored_keycloak_postgres_password="$(read_secret_value "keycloak-postgres-password")"
    stored_keycloak_bootstrap_password="$(read_secret_value "keycloak-bootstrap-password")"
    stored_keycloak_demo_password="$(read_secret_value "keycloak-demo-password")"
    stored_temporal_internal_token="$(read_secret_value "temporal-internal-token")"

    if [[ -n "${AGAT_CREDENTIALS_KEY:-}" && -n "${stored_credentials_key}" && "${AGAT_CREDENTIALS_KEY}" != "${stored_credentials_key}" ]]; then
      die "AGAT_CREDENTIALS_KEY нельзя менять без миграции уже зашифрованных credentials"
    fi
    if [[ -n "${AGAT_KEYCLOAK_POSTGRES_PASSWORD:-}" && -n "${stored_keycloak_postgres_password}" && "${AGAT_KEYCLOAK_POSTGRES_PASSWORD}" != "${stored_keycloak_postgres_password}" ]]; then
      die "пароль persistent Keycloak PostgreSQL нельзя менять этим скриптом"
    fi
    if [[ -n "${AGAT_KEYCLOAK_BOOTSTRAP_PASSWORD:-}" && -n "${stored_keycloak_bootstrap_password}" && "${AGAT_KEYCLOAK_BOOTSTRAP_PASSWORD}" != "${stored_keycloak_bootstrap_password}" ]]; then
      die "bootstrap password Keycloak не обновляет уже созданного admin-пользователя"
    fi
    if [[ -n "${AGAT_KEYCLOAK_DEMO_PASSWORD:-}" && -n "${stored_keycloak_demo_password}" && "${AGAT_KEYCLOAK_DEMO_PASSWORD}" != "${stored_keycloak_demo_password}" ]]; then
      die "пароль agat-admin меняйте через Keycloak, а не ротацией Kubernetes Secret"
    fi
  fi

  admin_token="${AGAT_ADMIN_TOKEN:-$(openssl rand -hex 32)}"
  enrollment_token="${AGAT_ENROLLMENT_TOKEN:-$(openssl rand -hex 32)}"
  model_api_key="${AGAT_MODEL_API_KEY:-${stored_model_api_key:-ollama}}"
  search_secret="${AGAT_SEARCH_SECRET:-${stored_search_secret}}"
  credentials_key="${stored_credentials_key:-${AGAT_CREDENTIALS_KEY:-}}"
  keycloak_postgres_password="${stored_keycloak_postgres_password:-${AGAT_KEYCLOAK_POSTGRES_PASSWORD:-}}"
  keycloak_bootstrap_password="${stored_keycloak_bootstrap_password:-${AGAT_KEYCLOAK_BOOTSTRAP_PASSWORD:-}}"
  keycloak_demo_password="${stored_keycloak_demo_password:-${AGAT_KEYCLOAK_DEMO_PASSWORD:-}}"
  temporal_internal_token="${AGAT_TEMPORAL_INTERNAL_TOKEN:-${stored_temporal_internal_token}}"
  [[ -n "${search_secret}" ]] || search_secret="$(openssl rand -hex 32)"
  [[ -n "${credentials_key}" ]] || credentials_key="$(openssl rand -hex 32)"
  [[ -n "${keycloak_postgres_password}" ]] || keycloak_postgres_password="$(openssl rand -hex 24)"
  [[ -n "${keycloak_bootstrap_password}" ]] || keycloak_bootstrap_password="$(openssl rand -hex 18)"
  [[ -n "${keycloak_demo_password}" ]] || keycloak_demo_password="$(openssl rand -hex 12)"
  [[ -n "${temporal_internal_token}" ]] || temporal_internal_token="$(openssl rand -hex 32)"

  kubectl create secret generic agat-secrets \
    --namespace "${namespace}" \
    --from-literal="admin-token=${admin_token}" \
    --from-literal="enrollment-token=${enrollment_token}" \
    --from-literal="model-api-key=${model_api_key}" \
    --from-literal="search-secret=${search_secret}" \
    --from-literal="credentials-key=${credentials_key}" \
    --from-literal="keycloak-postgres-password=${keycloak_postgres_password}" \
    --from-literal="keycloak-bootstrap-password=${keycloak_bootstrap_password}" \
    --from-literal="keycloak-demo-password=${keycloak_demo_password}" \
    --from-literal="temporal-internal-token=${temporal_internal_token}" \
    --dry-run=client \
    --output=yaml | kubectl apply --filename - >/dev/null

  unset admin_token enrollment_token model_api_key search_secret credentials_key
  unset keycloak_postgres_password keycloak_bootstrap_password keycloak_demo_password
  unset temporal_internal_token
  unset stored_model_api_key stored_search_secret stored_credentials_key
  unset stored_keycloak_postgres_password stored_keycloak_bootstrap_password stored_keycloak_demo_password
  unset stored_temporal_internal_token
  if ${secret_exists}; then
    printf '%s\n' 'Kubernetes Secret agat-secrets безопасно обновлён; persistent secrets сохранены.'
  else
    printf '%s\n' 'Kubernetes Secret agat-secrets создан.'
  fi
elif [[ -n "${AGAT_ADMIN_TOKEN:-}${AGAT_ENROLLMENT_TOKEN:-}${AGAT_MODEL_API_KEY:-}${AGAT_SEARCH_SECRET:-}${AGAT_CREDENTIALS_KEY:-}${AGAT_KEYCLOAK_POSTGRES_PASSWORD:-}${AGAT_KEYCLOAK_BOOTSTRAP_PASSWORD:-}${AGAT_KEYCLOAK_DEMO_PASSWORD:-}${AGAT_TEMPORAL_INTERNAL_TOKEN:-}" ]]; then
  printf '%s\n' \
    'Существующий agat-secrets сохранён; для ротации задайте AGAT_K8S_ROTATE_SECRETS=true и оба токена.'
fi

search_secret_data="$(kubectl get secret/agat-secrets \
  --namespace "${namespace}" \
  --output='jsonpath={.data.search-secret}')"
if [[ -z "${search_secret_data}" ]]; then
  search_secret="$(openssl rand -hex 32)"
  search_secret_encoded="$(printf '%s' "${search_secret}" | base64 | tr -d '\n')"
  [[ -n "${search_secret_encoded}" ]] || die "не удалось сгенерировать search-secret"
  kubectl patch secret/agat-secrets \
    --namespace "${namespace}" \
    --type=merge \
    --patch "{\"data\":{\"search-secret\":\"${search_secret_encoded}\"}}" >/dev/null
  unset search_secret search_secret_encoded
  printf '%s\n' 'В существующий agat-secrets добавлен ключ для внутреннего поиска.'
fi
unset search_secret_data

ensure_secret_key() {
  local key="$1"
  local requested_value="$2"
  local existing_data
  existing_data="$(kubectl get secret/agat-secrets \
    --namespace "${namespace}" \
    --output="jsonpath={.data.${key}}")"
  if [[ -n "${existing_data}" ]]; then
    return
  fi
  local value="${requested_value:-$(openssl rand -hex 32)}"
  local encoded
  encoded="$(printf '%s' "${value}" | base64 | tr -d '\n')"
  [[ -n "${encoded}" ]] || die "не удалось сгенерировать ${key}"
  kubectl patch secret/agat-secrets \
    --namespace "${namespace}" \
    --type=merge \
    --patch "{\"data\":{\"${key}\":\"${encoded}\"}}" >/dev/null
  printf 'В существующий agat-secrets добавлен %s.\n' "${key}"
}

ensure_secret_key "credentials-key" "${AGAT_CREDENTIALS_KEY:-}"
ensure_secret_key "keycloak-postgres-password" "${AGAT_KEYCLOAK_POSTGRES_PASSWORD:-}"
ensure_secret_key "keycloak-bootstrap-password" "${AGAT_KEYCLOAK_BOOTSTRAP_PASSWORD:-}"
ensure_secret_key "keycloak-demo-password" "${AGAT_KEYCLOAK_DEMO_PASSWORD:-}"
ensure_secret_key "temporal-internal-token" "${AGAT_TEMPORAL_INTERNAL_TOKEN:-}"

if ! is_true "${AGAT_K8S_SKIP_BUILD:-false}"; then
  printf 'Собираю %s для %s...\n' "${coordinator_image}" "${platform}"
  docker buildx build \
    --load \
    --platform "${platform}" \
    --tag "${coordinator_image}" \
    --file "${repo_root}/Dockerfile" \
    "${repo_root}"

  printf 'Собираю %s для %s...\n' "${worker_image}" "${platform}"
  docker buildx build \
    --load \
    --platform "${platform}" \
    --tag "${worker_image}" \
    --file "${repo_root}/workers/Dockerfile" \
    "${repo_root}"

  printf 'Собираю %s для %s...\n' "${temporal_worker_image}" "${platform}"
  docker buildx build \
    --load \
    --platform "${platform}" \
    --tag "${temporal_worker_image}" \
    --file "${repo_root}/apps/temporal-worker/Dockerfile" \
    "${repo_root}"
fi

kubectl apply --kustomize "${manifests_dir}"
coordinator_otel_patch="$(node --input-type=module -e '
  const [enabled, endpoint, serviceName] = process.argv.slice(1);
  process.stdout.write(JSON.stringify({
    data: {
      AGAT_OTEL_ENABLED: enabled,
      OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
      OTEL_SERVICE_NAME: serviceName,
    },
  }));
' "${otel_enabled}" "${otel_exporter_endpoint}" "${AGAT_OTEL_COORDINATOR_SERVICE_NAME:-agat-coordinator}")"
worker_otel_patch="$(node --input-type=module -e '
  const [enabled, endpoint, serviceName] = process.argv.slice(1);
  process.stdout.write(JSON.stringify({
    data: {
      AGAT_OTEL_ENABLED: enabled,
      OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
      OTEL_SERVICE_NAME: serviceName,
    },
  }));
' "${otel_enabled}" "${otel_exporter_endpoint}" "${AGAT_OTEL_WORKER_SERVICE_NAME:-agat-worker}")"
[[ -n "${coordinator_otel_patch}" && -n "${worker_otel_patch}" ]] || die \
  "не удалось сформировать конфигурацию OpenTelemetry"
kubectl patch configmap/agat-coordinator-config \
  --namespace "${namespace}" \
  --type=merge \
  --patch "${coordinator_otel_patch}" >/dev/null
kubectl patch configmap/agat-worker-config \
  --namespace "${namespace}" \
  --type=merge \
  --patch "${worker_otel_patch}" >/dev/null
unset coordinator_otel_patch worker_otel_patch
kubectl set image deployment/agat-coordinator \
  --namespace "${namespace}" \
  "coordinator=${coordinator_image}" >/dev/null
kubectl set image deployment/agat-worker \
  --namespace "${namespace}" \
  "wait-for-coordinator=${worker_image}" \
  "worker=${worker_image}" >/dev/null
kubectl set image deployment/agat-temporal-worker \
  --namespace "${namespace}" \
  "temporal-worker=${temporal_worker_image}" >/dev/null

kubectl set env deployment/agat-coordinator \
  --namespace "${namespace}" \
  "AGAT_SEED_DEMO=${AGAT_SEED_DEMO:-false}" \
  "AGAT_LOCAL_WORKER_IMAGE=${worker_image}" \
  "AGAT_LOCAL_MODEL_BASE_URL=${model_base_url}" \
  "AGAT_LOCAL_WORKER_EMBEDDING_MODELS=${AGAT_LOCAL_WORKER_EMBEDDING_MODELS:-${embedding_models}}" \
  "AGAT_LOCAL_WORKER_WEB_ENABLED=${web_enabled}" \
  "AGAT_OTEL_ENABLED=${otel_enabled}" \
  "OTEL_EXPORTER_OTLP_ENDPOINT=${otel_exporter_endpoint}" \
  "OTEL_SERVICE_NAME=${AGAT_OTEL_COORDINATOR_SERVICE_NAME:-agat-coordinator}" >/dev/null
kubectl set env deployment/agat-worker \
  --namespace "${namespace}" \
  "AGAT_WORKER_NAME=${AGAT_WORKER_NAME:-docker-desktop-worker}" \
  "AGAT_WORKER_MODELS=${worker_models}" \
  "AGAT_EMBEDDING_MODELS=${embedding_models}" \
  "AGAT_MODEL_BASE_URL=${model_base_url}" \
  "AGAT_WORKER_CONCURRENCY=${AGAT_WORKER_CONCURRENCY:-1}" \
  "AGAT_WORKER_LABELS=${AGAT_WORKER_LABELS:-runtime=kubernetes,cluster=docker-desktop}" \
  "AGAT_WEB_ENABLED=${web_enabled}" \
  "AGAT_WEB_SEARCH_URL=${web_search_url}" \
  "AGAT_WEB_TIMEOUT=${AGAT_WEB_TIMEOUT:-12}" \
  "AGAT_WEB_FETCH_MAX_BYTES=${AGAT_WEB_FETCH_MAX_BYTES:-1000000}" \
  "AGAT_WEB_FETCH_MAX_CHARS=${AGAT_WEB_FETCH_MAX_CHARS:-12000}" \
  "AGAT_WEB_SEARCH_MAX_RESULTS=${AGAT_WEB_SEARCH_MAX_RESULTS:-6}" \
  "AGAT_WEB_MAX_TOOL_ROUNDS=${AGAT_WEB_MAX_TOOL_ROUNDS:-6}" \
  "AGAT_OTEL_ENABLED=${otel_enabled}" \
  "OTEL_EXPORTER_OTLP_ENDPOINT=${otel_exporter_endpoint}" \
  "OTEL_SERVICE_NAME=${AGAT_OTEL_WORKER_SERVICE_NAME:-agat-worker}" >/dev/null

if ! is_true "${AGAT_K8S_SKIP_BUILD:-false}"; then
  ${coordinator_existed} && kubectl rollout restart deployment/agat-coordinator --namespace "${namespace}" >/dev/null
  ${worker_existed} && kubectl rollout restart deployment/agat-worker --namespace "${namespace}" >/dev/null
  ${temporal_worker_existed} && kubectl rollout restart deployment/agat-temporal-worker --namespace "${namespace}" >/dev/null
fi
${search_existed} && kubectl rollout restart deployment/agat-search --namespace "${namespace}" >/dev/null
${gateway_existed} && kubectl rollout restart deployment/agat-gateway --namespace "${namespace}" >/dev/null
${keycloak_existed} && kubectl rollout restart deployment/agat-keycloak --namespace "${namespace}" >/dev/null

kubectl scale deployment/agat-keycloak-postgres --namespace "${namespace}" --replicas=1 >/dev/null
kubectl scale deployment/agat-keycloak --namespace "${namespace}" --replicas=1 >/dev/null
kubectl scale deployment/agat-temporal --namespace "${namespace}" --replicas=1 >/dev/null
kubectl scale deployment/agat-coordinator --namespace "${namespace}" --replicas=1 >/dev/null
kubectl scale deployment/agat-temporal-worker --namespace "${namespace}" --replicas=1 >/dev/null
kubectl scale deployment/agat-gateway --namespace "${namespace}" --replicas=1 >/dev/null
if is_true "${web_enabled}"; then
  kubectl scale deployment/agat-search --namespace "${namespace}" --replicas=1 >/dev/null
else
  kubectl scale deployment/agat-search --namespace "${namespace}" --replicas=0 >/dev/null
fi
if is_true "${AGAT_K8S_WORKER_ENABLED:-true}"; then
  kubectl scale deployment/agat-worker --namespace "${namespace}" --replicas=1 >/dev/null
else
  kubectl scale deployment/agat-worker --namespace "${namespace}" --replicas=0 >/dev/null
fi

kubectl rollout status deployment/agat-keycloak-postgres --namespace "${namespace}" --timeout=180s
kubectl rollout status deployment/agat-keycloak --namespace "${namespace}" --timeout=240s
kubectl rollout status deployment/agat-temporal --namespace "${namespace}" --timeout=180s
kubectl rollout status deployment/agat-coordinator --namespace "${namespace}" --timeout=180s
kubectl rollout status deployment/agat-temporal-worker --namespace "${namespace}" --timeout=180s
if is_true "${web_enabled}"; then
  kubectl rollout status deployment/agat-search --namespace "${namespace}" --timeout=180s
fi
if is_true "${AGAT_K8S_WORKER_ENABLED:-true}"; then
  kubectl rollout status deployment/agat-worker --namespace "${namespace}" --timeout=180s
fi
kubectl rollout status deployment/agat-gateway --namespace "${namespace}" --timeout=180s

health_url=""
identity_url=""
temporal_url=""
for _attempt in $(seq 1 20); do
  if gateway_is_ready; then
    health_url="http://127.0.0.1:8787"
  fi
  if curl --fail --silent --show-error --max-time 2 \
    "http://127.0.0.1:8080/realms/agat/.well-known/openid-configuration" >/dev/null 2>&1; then
    identity_url="http://127.0.0.1:8080"
  fi
  if curl --fail --silent --show-error --max-time 2 \
    "http://127.0.0.1:8233/" >/dev/null 2>&1; then
    temporal_url="http://127.0.0.1:8233"
  fi
  [[ -n "${health_url}" && -n "${identity_url}" && -n "${temporal_url}" ]] && break
  sleep 1
done

printf '\nАГАТ запущен в namespace %s.\n' "${namespace}"
if is_true "${web_enabled}"; then
  printf '%s\n' 'Web-инструменты: включены (внутренний SearXNG + безопасное чтение публичных страниц).'
else
  printf '%s\n' 'Web-инструменты: отключены через AGAT_WEB_ENABLED.'
fi
if [[ -n "${health_url}" ]]; then
  printf 'Панель: %s\n' "${health_url}"
else
  printf '%s\n' \
    'LoadBalancer ещё не доступен на localhost:8787. Запустите npm run k8s:forward в отдельном терминале.'
fi
if [[ -n "${identity_url}" ]]; then
  printf 'Keycloak: %s (пользователь agat-admin)\n' "${identity_url}"
else
  printf '%s\n' \
    'Keycloak ещё не доступен на localhost:8080. Запустите npm run k8s:forward в отдельном терминале.'
fi
if [[ -n "${temporal_url}" ]]; then
  printf 'Temporal UI: %s\n' "${temporal_url}"
else
  printf '%s\n' \
    'Temporal UI ещё не доступен на localhost:8233. Запустите npm run k8s:forward в отдельном терминале.'
fi
printf '%s\n' \
  'Пароль agat-admin: npm run --silent k8s:keycloak-password' \
  'Admin token для первого изменения в интерфейсе: npm run --silent k8s:admin-token' \
  'Enrollment token для подключения worker: npm run --silent k8s:enrollment-token' \
  'Состояние: npm run k8s:status'
