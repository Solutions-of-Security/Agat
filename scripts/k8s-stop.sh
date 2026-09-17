#!/usr/bin/env bash

set -Eeuo pipefail

readonly namespace="agat"
readonly expected_context="docker-desktop"

current_context="$(kubectl config current-context 2>/dev/null || true)"
if [[ "${current_context}" != "${expected_context}" ]]; then
  printf "Ошибка: активен kube-context '%s', ожидался '%s'.\n" \
    "${current_context:-не задан}" "${expected_context}" >&2
  exit 1
fi

if ! kubectl get namespace "${namespace}" >/dev/null 2>&1; then
  printf 'Namespace %s не существует.\n' "${namespace}"
  exit 0
fi

deployments=()
for deployment in agat-gateway agat-coordinator agat-temporal-worker agat-temporal agat-worker agat-search agat-keycloak agat-keycloak-postgres agat-coordinator-postgres; do
  if kubectl get "deployment/${deployment}" --namespace "${namespace}" >/dev/null 2>&1; then
    deployments+=("deployment/${deployment}")
  fi
done

while IFS= read -r deployment; do
  if [[ -n "${deployment}" ]]; then
    deployments+=("${deployment}")
  fi
done < <(kubectl get deployments \
  --namespace "${namespace}" \
  --selector='agat.local/managed=true' \
  --output=name)

if ((${#deployments[@]})); then
  kubectl scale "${deployments[@]}" --namespace "${namespace}" --replicas=0
fi
printf '%s\n' 'АГАТ остановлен. PVC и Secret сохранены; npm run k8s:up возобновит работу.'
