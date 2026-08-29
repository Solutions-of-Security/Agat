#!/usr/bin/env bash

set -Eeuo pipefail

readonly action="${1:-status}"
readonly address="${AGAT_TEMPORAL_ADDRESS:-127.0.0.1:7233}"
readonly namespace="${AGAT_TEMPORAL_NAMESPACE:-agat}"
readonly deployment_name="${AGAT_TEMPORAL_DEPLOYMENT_NAME:-agat-processes}"
readonly build_id="${AGAT_TEMPORAL_BUILD_ID:-}"
readonly percentage="${AGAT_TEMPORAL_RAMP_PERCENTAGE:-5}"

die() {
  printf 'Ошибка: %s\n' "$*" >&2
  exit 1
}

command -v temporal >/dev/null 2>&1 || die "не найдена команда temporal"
[[ -n "${address}" && "${address}" != *"://"* && ! "${address}" =~ [[:space:]/?#@] ]] || die \
  "AGAT_TEMPORAL_ADDRESS должен иметь формат host:port"
[[ "${namespace}" =~ ^[A-Za-z0-9._-]+$ ]] || die "некорректный AGAT_TEMPORAL_NAMESPACE"
[[ "${deployment_name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$ ]] || die \
  "некорректный AGAT_TEMPORAL_DEPLOYMENT_NAME"

connection_args=(--address "${address}" --namespace "${namespace}")
tls_enabled=false
case "${AGAT_TEMPORAL_TLS:-false}" in
  1|true|TRUE|yes|YES|on|ON) tls_enabled=true ;;
  0|false|FALSE|no|NO|off|OFF) ;;
  *) die "AGAT_TEMPORAL_TLS должен быть boolean" ;;
esac
if [[ -n "${AGAT_TEMPORAL_CLIENT_CERT_PATH:-}" && -z "${AGAT_TEMPORAL_CLIENT_KEY_PATH:-}" ]] \
  || [[ -z "${AGAT_TEMPORAL_CLIENT_CERT_PATH:-}" && -n "${AGAT_TEMPORAL_CLIENT_KEY_PATH:-}" ]]; then
  die "AGAT_TEMPORAL_CLIENT_CERT_PATH и AGAT_TEMPORAL_CLIENT_KEY_PATH должны задаваться вместе"
fi
if [[ "${tls_enabled}" == false ]] && [[ -n "${AGAT_TEMPORAL_CA_CERT_PATH:-}${AGAT_TEMPORAL_CLIENT_CERT_PATH:-}${AGAT_TEMPORAL_SERVER_NAME_OVERRIDE:-}${AGAT_TEMPORAL_API_KEY:-}" ]]; then
  die "TLS-файлы, server name и API key требуют AGAT_TEMPORAL_TLS=true"
fi
[[ "${tls_enabled}" == false ]] || connection_args+=(--tls)
[[ -z "${AGAT_TEMPORAL_CA_CERT_PATH:-}" ]] || connection_args+=(--tls-ca-path "${AGAT_TEMPORAL_CA_CERT_PATH}")
[[ -z "${AGAT_TEMPORAL_CLIENT_CERT_PATH:-}" ]] || connection_args+=(--tls-cert-path "${AGAT_TEMPORAL_CLIENT_CERT_PATH}")
[[ -z "${AGAT_TEMPORAL_CLIENT_KEY_PATH:-}" ]] || connection_args+=(--tls-key-path "${AGAT_TEMPORAL_CLIENT_KEY_PATH}")
[[ -z "${AGAT_TEMPORAL_SERVER_NAME_OVERRIDE:-}" ]] || connection_args+=(--tls-server-name "${AGAT_TEMPORAL_SERVER_NAME_OVERRIDE}")
if [[ -n "${AGAT_TEMPORAL_API_KEY:-}" ]]; then
  # Temporal CLI reads this standard environment variable. Keeping the secret
  # out of argv prevents it from appearing in process listings and shell traces.
  export TEMPORAL_API_KEY="${AGAT_TEMPORAL_API_KEY}"
fi

case "${action}" in
  status)
    temporal worker deployment describe \
      --name "${deployment_name}" \
      "${connection_args[@]}"
    ;;
  ramp)
    [[ "${build_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,254}$ ]] || die \
      "задайте immutable AGAT_TEMPORAL_BUILD_ID"
    [[ "${percentage}" =~ ^[0-9]+$ ]] || die \
      "AGAT_TEMPORAL_RAMP_PERCENTAGE должен быть целым числом от 1 до 99"
    (( percentage >= 1 && percentage <= 99 )) || die \
      "AGAT_TEMPORAL_RAMP_PERCENTAGE должен быть целым числом от 1 до 99"
    temporal worker deployment set-ramping-version \
      --deployment-name "${deployment_name}" \
      --build-id "${build_id}" \
      --percentage "${percentage}" \
      --yes \
      "${connection_args[@]}"
    ;;
  promote)
    [[ "${build_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,254}$ ]] || die \
      "задайте immutable AGAT_TEMPORAL_BUILD_ID"
    temporal worker deployment set-current-version \
      --deployment-name "${deployment_name}" \
      --build-id "${build_id}" \
      --yes \
      "${connection_args[@]}"
    ;;
  version)
    [[ "${build_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,254}$ ]] || die \
      "задайте AGAT_TEMPORAL_BUILD_ID"
    temporal worker deployment describe-version \
      --deployment-name "${deployment_name}" \
      --build-id "${build_id}" \
      "${connection_args[@]}"
    ;;
  *)
    die "использование: $0 {status|ramp|promote|version}"
    ;;
esac
