# План реального shadow-пилота

08.10.2026 MSK. [CLI подготовки](../../../../../scripts/prepare-decision-shadow-pilot.py)
закрепляет scope и targets до начала наблюдения. [Шаблон](./pilot-config.blank.json)
сохраняет пустые owners, workflow, разрешённые данные и даты. Значения 99% / 95%,
5000 ms threshold и 10000 ms caller timeout взяты из
[предложения owner/SLO](./resident-service-acceptance.md); согласование не выполнено.

Scope — все сохранённые экземпляры одной версии процесса в одном проекте,
созданные в UTC-интервале `[startAt, endAt)`. Failed, cancelled, pending и replay
не исключаются из набора запусков. Дальнейший census отделяет replay от новых
caller intents. Это cohort по времени создания процесса, а не скользящее окно
времени HTTP attempts и не все клиентские обращения до создания run.

Формат дат: `2026-10-09T00:00:00.000Z`; интервал положительный, максимум семь
дней. Начало должно быть позже подготовки plan. Нельзя задним числом считать
scope заранее закреплённым. До сбора полного cohort `populationCoverageVerified=false`.
Readiness `ready_for_review` означает заполненный технический план, не подпись
owners. CLI всегда сохраняет `agreementVerified=false`, `sloAccepted=false`,
`routingEnabled=false`, `qualification=not_assessed`.

Конфигурацию скопировать в `docs/private/pilot/config.json`, заполнить явными
IDs и ссылкой на разрешённое использование данных. До подготовки отдельно
зафиксировать SHA файла config и bytes профиля. Пример команды:

```bash
python3 scripts/prepare-decision-shadow-pilot.py \
  --config docs/private/pilot/config.json \
  --config-sha256 '<independent-config-file-sha256>' \
  --profile docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json \
  --profile-file-sha256 786902a051c862a805197adfb7def29f4482df914f828a61768e64a07bca0e6e \
  --output docs/private/pilot/plan.json
```

Default identity — `coordinator_json_bytes`: exact UTF-8 bytes, включая пробелы,
как в coordinator config. Semantic fingerprint сохраняется отдельно; режим
`runtime_fingerprint` выбирается явно. Изменение файла требует нового plan.
SHA защищает целостность, не является цифровой подписью человека.

Source SHA сверяются с HEAD до и после подготовки. Private output создаётся
один раз, mode 0600, input bounds 64 KiB config / 1 MiB profile. Exit 0 —
заполненный план для review; exit 2 — draft с перечисленными missing fields;
exit 1 — ошибка pins/schema/sources/path. При ошибке предварительных условий
план не создаётся. Шаблон не содержит реальных owner IDs или клиентских данных.

После этого нужен server census всего cohort, content-free caller exports
из одного database snapshot и сравнение exact run IDs с plan. Произвольный
набор trace-файлов остаётся `provided_traces_only`; высокий success ratio
на таком наборе не закрывает population gap.

[Экспортер cohort](./shadow-pilot-cohort.md) реализует полный bounded census
из одного SQLite/PostgreSQL snapshot. Contract **v2** использует числовой
`processVersion`, соответствующий настоящей модели Agat. Ранний v1 от PR 151
имел `processVersionId`, хотя отдельного version ID в базе нет. V1 артефакты
сохранены как история вместе с источниками `e7a5e59`; они не переписываются.
Для новой подготовки нужен v2 config/plan, v1 явно отклоняется. Это исправление
технического контракта, не назначение workflow/owners или принятие SLO.

Методика: [Google SRE Implementing SLOs](https://sre.google/workbook/implementing-slos/)
рекомендует явно определить пользовательские события, good/total и согласовать
targets; [Example SLO Document](https://sre.google/workbook/slo-document/) фиксирует
границы, targets и правила обработки budget. Эти источники обосновывают структуру;
наши числа остаются предложением по ограниченному локальному evidence.
