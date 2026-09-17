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

kubectl get secret/agat-secrets \
  --namespace "${namespace}" \
  --output='go-template={{index .data "enrollment-token" | base64decode}}{{"\n"}}'
