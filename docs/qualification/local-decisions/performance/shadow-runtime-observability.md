# Локальная диагностика ресурсов shadow runtime

Дата: 28.09.2026. Добавлен opt-in `--shadow-resources` к [real Temporal/RAG launcher](../../../../scripts/run-temporal-real-rag.py). Он работает вместе с `--shadow-recovery`, сохраняя профиль, deadline, primary fallback и протокол контролируемого restart. Runtime routing и production defaults не меняются.

[Sampler](../../../../scripts/lib/shadow_resource_sample.py) читает RSS, footprint и native CPU counters только собственных shadow/Ollama/workload поддеревьев. Отдельно сохраняются агрегированные системные VM/swap counters. Command lines, executable identifiers и данные чужих процессов не записываются. Исчезновение процесса между inventory и native read учитывается явно; permission failure делает наблюдение неполным.

По [исходникам Apple](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c) `kern.memorystatus_vm_pressure_level` возвращает dispatch levels, отличающиеся от внутренней шкалы ядра. Parser и verifier проверяют единицы, полноту полей и монотонность CPU counters для пары PID/startTicks. Снимки приблизительно раз в секунду не являются непрерывным peak profiler; системные показатели не приписываются только workload. RSS, footprint и system swap нельзя складывать в одну оценку памяти.

Resource artifacts сохраняются только под игнорируемым `docs/private/`. Launcher отвергает другой output directory до чтения manifest и загрузки моделей; CLI verifier сохраняет сводку ресурсов в той же приватной области. Эта документация и публичные fixtures не содержат измерений локального компьютера. Исходные диагностические измерения сохранены локально вместе с git bundle исходников и evidence.

Запуск с уже проверенными resident Python/manifest и установленными pinned Ollama-моделями:

```sh
python3 scripts/run-temporal-real-rag.py \
  --evidence-dir docs/private/shadow-run-1 \
  --shadow-python /path/to/mlx-env/bin/python \
  --shadow-manifest /path/to/verified-manifest.json \
  --shadow-recovery --shadow-resources
python3 scripts/verify-temporal-real-rag.py docs/private/shadow-run-1 \
  --output docs/private/shadow-run-1/verification.json
```

[Семь синтетических verifier tests](../../../../scripts/test/test_shadow_resource_verifier.py) проверяют units, process scope/roots, CPU monotonicity, cleanup timeline, исчезновение процесса и запрет публичного output path. Четыре [sampler tests](../../../../scripts/test/test_shadow_resource_sample.py) проверяют парсеры и доступ только к owned descendants. Прежние 42 real Temporal/RAG evidence tests продолжают проверять v1–v3. Синтетические counters явно обозначены как тестовые и не выдаются за измеренные ресурсы.

[Series verifier](../../../../scripts/verify-shadow-runtime-diagnostics.py) позволяет локально перепроверить заранее объявленную тройку v4 запусков, исходный plan/calibration, все шесть histories и равенство outputs/vectors. Неполный или failed run не становится успешным агрегатом. Отсутствие повторного отказа не устанавливает причину прежнего exit 75 и не является production SLO или предметной qualification.

[Checks](./evidence/2026-09-28/shadow-runtime-observability/checks.json) содержит результаты публичных регрессионных проверок и SHA их логов. При локальном переносе evidence `--original-evidence-path` задаёт прежний путь под `docs` для сверки plan/calibration с исходным commit; SHA-проверки не отключаются.

Следующий шаг — ограниченная причина backend retirement в service log перед exit 75. Проверить timeout, cancellation и смерть inference child процессными тестами; не писать request, текст backend exception или путь модели. Изменение memory policy или inference deadline требует отдельного основания.
