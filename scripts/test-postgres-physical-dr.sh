#!/usr/bin/env bash

set -euo pipefail

readonly postgres_image="${AGAT_POSTGRES_DR_IMAGE:-postgres:17.6-alpine}"
readonly drill_id="${AGAT_POSTGRES_DRILL_ID:-$(openssl rand -hex 6)}"
readonly prefix="agat-dr-${drill_id}"
readonly network="${prefix}-network"
readonly primary="${prefix}-primary"
readonly standby="${prefix}-standby"
readonly pitr="${prefix}-pitr"
readonly archive_volume="${prefix}-archive"
readonly backup_volume="${prefix}-backup"
readonly standby_volume="${prefix}-standby-data"
readonly pitr_volume="${prefix}-pitr-data"
readonly postgres_password="$(openssl rand -hex 24)"
readonly replication_password="$(openssl rand -hex 24)"
readonly restore_point="agat_${drill_id}_target"
readonly maximum_failover_rto_seconds="${AGAT_POSTGRES_FAILOVER_RTO_SECONDS:-300}"

cleanup() {
  if [[ "${AGAT_POSTGRES_DR_KEEP:-false}" == "true" ]]; then
    printf 'Disposable DR topology сохранена с prefix %s\n' "${prefix}" >&2
    return
  fi
  docker rm --force "${primary}" "${standby}" "${pitr}" >/dev/null 2>&1 || true
  docker network rm "${network}" >/dev/null 2>&1 || true
  docker volume rm "${archive_volume}" "${backup_volume}" "${standby_volume}" "${pitr_volume}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

now_millis() {
  node --input-type=module -e 'process.stdout.write(String(Date.now()))'
}

wait_for_sql() {
  local container="$1"
  local attempts=90
  until docker exec --env "PGPASSWORD=${postgres_password}" "${container}" \
    psql --username postgres --dbname agat --tuples-only --no-align --command 'SELECT 1' \
    >/dev/null 2>&1; do
    attempts=$((attempts - 1))
    if (( attempts == 0 )); then
      docker logs --tail 80 "${container}" >&2 || true
      printf 'PostgreSQL container %s не стал ready\n' "${container}" >&2
      return 1
    fi
    sleep 1
  done
}

query() {
  local container="$1"
  local sql="$2"
  docker exec --env "PGPASSWORD=${postgres_password}" "${container}" \
    psql --username postgres --dbname agat --tuples-only --no-align --set ON_ERROR_STOP=1 --command "${sql}"
}

wait_for_value() {
  local container="$1"
  local sql="$2"
  local expected="$3"
  local attempts=90
  local actual=""
  while (( attempts > 0 )); do
    actual="$(query "${container}" "${sql}" 2>/dev/null || true)"
    if [[ "${actual}" == "${expected}" ]]; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  printf 'Ожидалось %s, получено %s в %s\n' "${expected}" "${actual}" "${container}" >&2
  return 1
}

docker network create "${network}" >/dev/null
docker volume create "${archive_volume}" >/dev/null
docker volume create "${backup_volume}" >/dev/null
docker volume create "${standby_volume}" >/dev/null
docker volume create "${pitr_volume}" >/dev/null

docker run --rm --volume "${archive_volume}:/archive" "${postgres_image}" \
  sh -ec 'chown postgres:postgres /archive' >/dev/null
docker run --rm --volume "${backup_volume}:/backup" "${postgres_image}" \
  sh -ec 'chown postgres:postgres /backup' >/dev/null

docker run --detach \
  --name "${primary}" \
  --network "${network}" \
  --env "POSTGRES_DB=agat" \
  --env "POSTGRES_PASSWORD=${postgres_password}" \
  --env "POSTGRES_INITDB_ARGS=--data-checksums" \
  --volume "${archive_volume}:/archive" \
  "${postgres_image}" \
  postgres \
  -c wal_level=replica \
  -c max_wal_senders=10 \
  -c max_replication_slots=10 \
  -c archive_mode=on \
  -c archive_timeout=1s \
  -c "archive_command=test ! -f /archive/%f && cp %p /archive/%f" \
  -c synchronous_commit=on \
  -c full_page_writes=on \
  >/dev/null
wait_for_sql "${primary}"

docker exec "${primary}" sh -ec \
  'printf "%s\n" "host replication agat_replication all scram-sha-256" >> "${PGDATA}/pg_hba.conf"'
query "${primary}" 'SELECT pg_reload_conf()' >/dev/null
docker exec --interactive \
  --env "PGPASSWORD=${postgres_password}" \
  "${primary}" \
  psql --username postgres --dbname agat --set ON_ERROR_STOP=1 \
  --set "replication_password=${replication_password}" <<'SQL' >/dev/null
SELECT format(
  'CREATE ROLE agat_replication WITH LOGIN REPLICATION PASSWORD %L',
  :'replication_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agat_replication') \gexec

CREATE TABLE agat_dr_canaries (
  id text PRIMARY KEY,
  issued_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO agat_dr_canaries(id) VALUES ('base-backup-seed');
SQL

docker run --rm \
  --network "${network}" \
  --user postgres \
  --env "PGPASSWORD=${replication_password}" \
  --volume "${backup_volume}:/backup" \
  "${postgres_image}" \
  pg_basebackup \
  --host "${primary}" \
  --username agat_replication \
  --pgdata /backup \
  --format plain \
  --wal-method stream \
  --checkpoint fast \
  >/dev/null

docker run --rm \
  --volume "${backup_volume}:/source:ro" \
  --volume "${standby_volume}:/target" \
  "${postgres_image}" \
  sh -ec 'cp -a /source/. /target/ && chown -R postgres:postgres /target' \
  >/dev/null
docker run --rm \
  --env "PRIMARY_HOST=${primary}" \
  --env "REPLICATION_PASSWORD=${replication_password}" \
  --volume "${standby_volume}:/target" \
  "${postgres_image}" \
  sh -ec '
    printf "\nprimary_conninfo = '\''host=%s port=5432 user=agat_replication password=%s application_name=agat_dr_standby'\''\n" \
      "${PRIMARY_HOST}" "${REPLICATION_PASSWORD}" >> /target/postgresql.auto.conf
    touch /target/standby.signal
    chown -R postgres:postgres /target
  ' \
  >/dev/null

docker run --detach \
  --name "${standby}" \
  --network "${network}" \
  --volume "${standby_volume}:/var/lib/postgresql/data" \
  "${postgres_image}" postgres >/dev/null
wait_for_sql "${standby}"
wait_for_value "${standby}" 'SELECT pg_is_in_recovery()' 't'
wait_for_value "${primary}" \
  "SELECT COUNT(*)::text FROM pg_stat_replication WHERE application_name = 'agat_dr_standby' AND state = 'streaming'" \
  '1'

query "${primary}" "INSERT INTO agat_dr_canaries(id) VALUES ('failover-before') RETURNING id" >/dev/null
wait_for_value "${standby}" "SELECT COUNT(*)::text FROM agat_dr_canaries WHERE id = 'failover-before'" '1'

restore_before_ms="$(now_millis)"
query "${primary}" "INSERT INTO agat_dr_canaries(id) VALUES ('restore-before') RETURNING id" >/dev/null
query "${primary}" "SELECT pg_create_restore_point('${restore_point}')" >/dev/null
restore_target_ms="$(now_millis)"
sleep 1
query "${primary}" "INSERT INTO agat_dr_canaries(id) VALUES ('restore-after') RETURNING id" >/dev/null
restore_after_ms="$(now_millis)"
query "${primary}" 'SELECT pg_switch_wal()' >/dev/null
wait_for_value "${primary}" \
  "SELECT (last_archived_wal IS NOT NULL AND failed_count = 0)::text FROM pg_stat_archiver" \
  'true'
wait_for_value "${standby}" "SELECT COUNT(*)::text FROM agat_dr_canaries WHERE id = 'restore-after'" '1'

failover_started_ms="$(now_millis)"
docker stop --time 2 "${primary}" >/dev/null
query "${standby}" 'SELECT pg_promote(true, 60)' >/dev/null
wait_for_value "${standby}" 'SELECT pg_is_in_recovery()' 'f'
failover_completed_ms="$(now_millis)"
wait_for_value "${standby}" "SELECT COUNT(*)::text FROM agat_dr_canaries WHERE id = 'failover-before'" '1'
query "${standby}" "INSERT INTO agat_dr_canaries(id) VALUES ('failover-after') RETURNING id" >/dev/null
failover_rto_seconds="$(( (failover_completed_ms - failover_started_ms + 999) / 1000 ))"
if (( failover_rto_seconds > maximum_failover_rto_seconds )); then
  printf 'Failover RTO %ss превышает objective %ss\n' \
    "${failover_rto_seconds}" "${maximum_failover_rto_seconds}" >&2
  exit 1
fi

docker run --rm \
  --volume "${backup_volume}:/source:ro" \
  --volume "${pitr_volume}:/target" \
  "${postgres_image}" \
  sh -ec 'cp -a /source/. /target/ && chown -R postgres:postgres /target' \
  >/dev/null
docker run --rm \
  --env "RESTORE_POINT=${restore_point}" \
  --volume "${pitr_volume}:/target" \
  "${postgres_image}" \
  sh -ec '
    printf "\nrestore_command = '\''cp /archive/%%f %%p'\''\nrecovery_target_name = '\''%s'\''\nrecovery_target_action = '\''promote'\''\nrecovery_target_timeline = '\''current'\''\narchive_mode = '\''off'\''\n" \
      "${RESTORE_POINT}" >> /target/postgresql.auto.conf
    touch /target/recovery.signal
    rm -f /target/standby.signal
    chown -R postgres:postgres /target
  ' \
  >/dev/null

restore_started_ms="$(now_millis)"
docker run --detach \
  --name "${pitr}" \
  --network "${network}" \
  --volume "${pitr_volume}:/var/lib/postgresql/data" \
  --volume "${archive_volume}:/archive:ro" \
  "${postgres_image}" postgres >/dev/null
wait_for_sql "${pitr}"
wait_for_value "${pitr}" 'SELECT pg_is_in_recovery()' 'f'
restore_completed_ms="$(now_millis)"
wait_for_value "${pitr}" "SELECT COUNT(*)::text FROM agat_dr_canaries WHERE id = 'restore-before'" '1'
wait_for_value "${pitr}" "SELECT COUNT(*)::text FROM agat_dr_canaries WHERE id = 'restore-after'" '0'
query "${pitr}" "INSERT INTO agat_dr_canaries(id) VALUES ('restore-verified') RETURNING id" >/dev/null

restore_rto_seconds="$(( (restore_completed_ms - restore_started_ms + 999) / 1000 ))"
restore_rpo_seconds="$(( (restore_target_ms - restore_before_ms + 999) / 1000 ))"
test "${restore_target_ms}" -ge "${restore_before_ms}"
test "${restore_target_ms}" -lt "${restore_after_ms}"

printf '{"schemaVersion":1,"drillId":"%s","postgresImage":"%s","streamingStandby":true,"failover":{"promoted":true,"preCanaryPresent":true,"postCanaryWritable":true,"rtoSeconds":%s},"pitr":{"namedRestorePoint":"%s","beforePresent":true,"afterAbsent":true,"writable":true,"rpoSeconds":%s,"rtoSeconds":%s},"success":true}\n' \
  "${drill_id}" \
  "${postgres_image}" \
  "${failover_rto_seconds}" \
  "${restore_point}" \
  "${restore_rpo_seconds}" \
  "${restore_rto_seconds}"
