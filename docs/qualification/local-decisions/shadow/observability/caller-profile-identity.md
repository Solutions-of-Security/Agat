# Идентичность настроенного профиля при offline caller SLI

07.10.2026. [Actual Python recovery gate](./caller-worker-postgres.md) выявил
несовпадение идентичности: coordinator сохраняет SHA-256 точных UTF-8 bytes
`profileJson`, а прежний SLI CLI проверял runtime fingerprint разобранного
JSON. У configured fixture есть whitespace и literal `1.0`; identities
различаются. Настоящий прежний CLI вернул exit 1 с `Expected profile content
SHA differs`. Этот отказ сохранён с committed sources и file pins.

CLI теперь принимает `--profile-identity coordinator_json_bytes`. Он
проверяет SHA-256 всего файла, строго разбирает bounded UTF-8 JSON и
связывает observation, stage inventory и caller ledger с configured SHA.
Helper принимает сами bytes, проверяет их соответствие разобранному
profile и вычисляет SHA самостоятельно. Boolean и number остаются разными
значениями. Duplicate fields, non-finite numbers, UTF-16, BOM, oversized
inputs и подмена profile отвергаются.

Режим по умолчанию — `runtime_fingerprint`; его алгоритм сохранён.
Report содержит явный `profileIdentity`, выбранный `profileSha256` и
отдельный `profileFingerprintSha256`. Coordinator и historical hashes
сохранены. [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) объясняет
потребность в устойчивой сериализации для hashing; существующий runtime
fingerprint здесь не заявляется реализацией JCS. Configured bytes и runtime
fingerprint — два явно выбранных способа идентификации.

Сохраните `profileJson` точным UTF-8 byte stream, включая whitespace и
наличие или отсутствие trailing newline. Укажите независимые SHA файла,
configured profile и каждого trace. Например, для диагностического gate:

```bash
python3 scripts/summarize-decision-shadow-sli.py \
  --profile-identity coordinator_json_bytes \
  --profile docs/private/my-sli/configured-profile.json \
  --profile-file-sha256 "$AGAT_PROFILE_FILE_SHA256" \
  --profile-sha256 "$AGAT_CONFIGURED_PROFILE_SHA256" \
  --trace docs/private/my-sli/trace.json \
  --trace-sha256 "$AGAT_TRACE_FILE_SHA256" \
  --latency-threshold-ms 1000 --traffic-kind diagnostic_fixture \
  --output docs/private/my-sli/new-sli.json
```

## Доказательства

35 targeted tests, включая девять новых regressions, прошли. Docs checks:
727 Python tests (четыре opt-in skips), 12 Node tests, 2420 local link
targets и process catalog.
Восемь actual CLI commands проверили configured/default identities,
неверный SHA, добавленный newline и прежние traces. Независимый audit
прошёл восемь checks: 14 committed CLI/transport/validation sources,
два test contributors, четыре raw SQL snapshots и 35 неизменных frozen
resident/session sources.

На восьми настоящих завершённых Python lease traces из PostgreSQL gate
независимый SQL census подтвердил восемь assignments/intents, шесть
returns/backend HTTP calls и два unknown returns. Configured mode имеет
ноль profile mismatches и шесть известных caller durations. Controlled
busy results дают interval 0–25% по intents; два unknown сохраняются
unknown. CLI возвращает exit 2 (`insufficient_data`), а не приёмку SLO.
Десять historical traces сохранили прежние observation counts, latency и
ratios; отсутствие нового stage inventory остаётся явным coverage gap.

[Публичная сводка](../evidence/2026-10-07/caller-profile-identity/result-summary.json)
содержит counts, hashes и repository source paths. Private ZIP: 74 files,
четыре Git states, 163 368 870 bytes, SHA-256
`92abcb1c4e02147a1c90788af7d696563fdcf40319d906b211270f83d4b5f330`.
CRC, SHA/size каждого файла и идентичная копия в исходном workspace проверены.

Последующий [actual Temporal/PostgreSQL/RAG gate](../../performance/temporal-caller-runtime-0.12.3.md)
с caller telemetry и отдельным pinned SLI анализом прошёл. Этот identity gate
не является MLX performance measurement,
customer population или human qualification. Owner/SLO и actual boot/login
остаются открытыми; routing выключен.
