#!/usr/bin/env bash

set -Eeuo pipefail

readonly namespace="agat"
readonly expected_context="docker-desktop"

gateway_is_ready() {
  local headers
  headers="$(curl --fail --silent --show-error --max-time 2 \
    --dump-header - --output /dev/null \
    "http://127.0.0.1:8787/api/v1/health" 2>/dev/null)" || return 1
  printf '%s' "${headers}" | grep -Eiq '^server:[[:space:]]*kong/'
}

current_context="$(kubectl config current-context 2>/dev/null || true)"
if [[ "${current_context}" != "${expected_context}" ]]; then
  printf "Ошибка: активен kube-context '%s', ожидался '%s'.\n" \
    "${current_context:-не задан}" "${expected_context}" >&2
  exit 1
fi

gateway_ready=false
identity_ready=false
temporal_ready=false
gateway_is_ready && gateway_ready=true
curl --fail --silent --show-error --max-time 2 \
  "http://127.0.0.1:8080/realms/agat/.well-known/openid-configuration" >/dev/null 2>&1 && identity_ready=true
curl --fail --silent --show-error --max-time 2 \
  "http://127.0.0.1:8233/" >/dev/null 2>&1 && temporal_ready=true

if ${gateway_ready} && ${identity_ready} && ${temporal_ready}; then
  printf '%s\n' \
    'Панель уже доступна на http://127.0.0.1:8787.' \
    'Keycloak уже доступен на http://127.0.0.1:8080.' \
    'Temporal UI уже доступен на http://127.0.0.1:8233.'
  exit 0
fi

pids=()
cleanup() {
  local pid
  for pid in "${pids[@]}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  wait "${pids[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! ${gateway_ready}; then
  kubectl port-forward \
    --namespace "${namespace}" \
    --address 127.0.0.1 \
    service/agat-gateway \
    8787:8787 &
  pids+=("$!")
fi

if ! ${identity_ready}; then
  kubectl port-forward \
    --namespace "${namespace}" \
    --address 127.0.0.1 \
    service/agat-keycloak \
    8080:8080 &
  pids+=("$!")
fi

if ! ${temporal_ready}; then
  kubectl port-forward \
    --namespace "${namespace}" \
    --address 127.0.0.1 \
    service/agat-temporal-ui \
    8233:8233 &
  pids+=("$!")
fi

for _attempt in $(seq 1 30); do
  gateway_ready=false
  identity_ready=false
  temporal_ready=false
  gateway_is_ready && gateway_ready=true
  curl --fail --silent --show-error --max-time 2 \
    "http://127.0.0.1:8080/realms/agat/.well-known/openid-configuration" >/dev/null 2>&1 && identity_ready=true
  curl --fail --silent --show-error --max-time 2 \
    "http://127.0.0.1:8233/" >/dev/null 2>&1 && temporal_ready=true
  ${gateway_ready} && ${identity_ready} && ${temporal_ready} && break
  for pid in "${pids[@]}"; do
    kill -0 "${pid}" >/dev/null 2>&1 || {
      printf '%s\n' 'Ошибка: один из port-forward процессов завершился.' >&2
      exit 1
    }
  done
  sleep 1
done

if ! ${gateway_ready} || ! ${identity_ready} || ! ${temporal_ready}; then
  printf '%s\n' 'Ошибка: сервисы не стали доступны за 30 секунд.' >&2
  exit 1
fi

printf '%s\n' \
  'Панель: http://127.0.0.1:8787' \
  'Keycloak: http://127.0.0.1:8080' \
  'Temporal UI: http://127.0.0.1:8233' \
  'Остановка port-forward — Ctrl+C.'

while true; do
  for pid in "${pids[@]}"; do
    kill -0 "${pid}" >/dev/null 2>&1 || {
      printf '%s\n' 'Ошибка: один из port-forward процессов завершился.' >&2
      exit 1
    }
  done
  sleep 2
done
