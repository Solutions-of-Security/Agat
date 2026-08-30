#!/usr/bin/env bash

set -Eeuo pipefail

readonly namespace="agat"
readonly expected_context="docker-desktop"

gateway_is_ready() {
  local headers
  headers="$(curl --fail --silent --show-error --max-time 3 \
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

kubectl get deployments,pods,services,persistentvolumeclaims --namespace "${namespace}"

status=0
health_body="$(curl --fail --silent --show-error --max-time 3 \
  "http://127.0.0.1:8787/api/v1/health" 2>/dev/null || true)"
if [[ -n "${health_body}" ]] \
  && gateway_is_ready \
  && [[ "${health_body}" == *'"mode":"temporal"'* ]] \
  && [[ "${health_body}" == *'"driver":"postgresql"'* ]]; then
  printf '%s' "${health_body}"
  printf '\nAPI Gateway: доступен на http://127.0.0.1:8787\n'
else
  printf '%s\n' \
    'Kong/Temporal/PostgreSQL health через localhost:8787 недоступен; проверьте pods или используйте npm run k8s:forward.' >&2
  status=1
fi
unset health_body

if curl --fail --silent --show-error --max-time 3 \
  "http://127.0.0.1:8080/realms/agat/.well-known/openid-configuration" >/dev/null; then
  printf '%s\n' 'Keycloak: доступен на http://127.0.0.1:8080'
else
  printf '%s\n' \
    'Keycloak через localhost:8080 недоступен; используйте npm run k8s:forward.' >&2
  status=1
fi

if curl --fail --silent --show-error --max-time 3 \
  "http://127.0.0.1:8233/" >/dev/null; then
  printf '%s\n' 'Temporal UI: доступен на http://127.0.0.1:8233'
else
  printf '%s\n' \
    'Temporal UI через localhost:8233 недоступен; используйте npm run k8s:forward.' >&2
  status=1
fi

exit "${status}"
