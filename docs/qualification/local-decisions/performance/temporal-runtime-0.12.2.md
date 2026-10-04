# Полный Temporal/PostgreSQL/RAG с runtime 0.12.2

04.10.2026. **Оба embedding transport прошли полный real-model процесс**
с профилем 0.12.2 после [двухчасовой непрерывной нагрузки](./continuous-soak-0.12.2.md).
Контролируемый SIGKILL shadow сохраняет primary fallback; restart возвращает
доступный shadow на том же профиле. Автоматическая маршрутизация выключена,
предметная qualification остаётся `not_assessed`.

## Закреплённый профиль

Измеренный commit — `b911c9240d883387559f17f07c3146588ca35a4e`, merge
[PR #113](https://github.com/Solutions-of-Security/Agat/pull/113).
Frozen plan закрепляет SHA **205 файлов**, resident decider-2b,
Qwen3 8B и embeddinggemma, pinned manifests и MLX dependencies.
Полный [профиль 0.12.2](./profiles/runtime-0.12.2.json) имеет SHA
`81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`,
как в двухчасовом gate: 2048 input tokens, cache 128 МиБ,
isolated inference deadline 5000 мс, прежняя shadow policy и отсутствие
fitted calibration. Параметры не подстраивались по результату.

Использованы отдельные Python 3.13.12 окружения, Node 24.14.0, собственные
model servers, PostgreSQL 17.6-alpine и Temporal 1.8.1. Сырые phase reports,
histories, shadow journals, process logs и ресурсные samples сохранены
в игнорируемом `docs/private`. Пользовательский исходный checkout с его
предыдущими изменениями сохранён.

## Проверенный результат

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Shadow inference / unavailable fallback | 2 / 1 | 2 / 1 |
| SIGKILL decider, primary fallback и restart | pass | pass |
| Потерянный tick, restart Temporal worker, тот же Workflow Run ID | pass | pass |
| Stage/shadow snapshots и source provenance | pass | pass |
| Tenant RLS и ограничение release registry | pass | pass |
| Native replay внутри harness | pass | pass |
| Отдельный native replay после cleanup | pass | pass |

Всего — **6 primary calls, 10 embedding items, 4 shadow inference,
2 unavailable observations, 3 отдельных warmup и 2 restart decider**.
Все 12 source citations проверены. Shadow наблюдения сохраняют primary
маршрут и не инициируют автоматических действий.

[Независимый verifier](../../../../scripts/verify-temporal-real-rag.py)
пересчитал исходники измеренного commit, runtime implementation fingerprint,
полный профиль, request/result bindings, policy, recovery timing,
RLS, histories и resource observations. После cleanup выполнен
[отдельный native replay](../../../../scripts/replay-temporal-real-rag.ts)
двух histories с точно измеренным workflow bundle. Оба прошли.
Разделение integration tests и history replay соответствует
[официальному руководству Temporal](https://docs.temporal.io/develop/typescript/best-practices/testing-suite).

[Сравнение с прежним baseline](./evidence/2026-10-04/temporal-runtime-0.12.2/baseline-comparison.json)
подтвердило все шесть prompt/output SHA и token counts, а также векторы
пяти различных embedding inputs. Это сравнение существующего development
процесса, а не оценка полезности на новых бизнес-данных.

Полный launcher занял **154,861 с**, включая загрузку моделей и cleanup.
Четыре собственных контейнера удалены, модели выгружены; cleanup errors
и оставшихся собственных PID нет. После launcher отдельно проверено
отсутствие всех 90 наблюдавшихся PID и четырёх контейнеров при доступном
Docker daemon. Anonymous volumes в этом полном опыте отдельно не
инвентаризировались; их удаление проверено ранее в
[live storage regressions](./temporary-postgres-volumes.md).
Длительность одного опыта на общей машине не устанавливает ускорение
относительно прежних запусков и не является SLO.

[Публичная сводка](./evidence/2026-10-04/temporal-runtime-0.12.2/result-summary.json)
содержит counts, commit, профиль и SHA приватных artifacts. Идентификаторы
процессов, workflow, контейнеров, полные inputs и resource counters
не публикуются. Raw evidence и диагностические process logs сохранены
приватно для дальнейшего анализа.

## Следующий этап

Один успешный полный повтор подтверждает интеграцию профиля 0.12.2 в
существующий development процесс. Прежний внеплановый exit 75 при совместно
загруженных моделях не воспроизвёлся; его причина этим успехом не
устанавливается. Независимые бизнес-данные, человеческие reviews,
калибровка, holdout, согласованные SLO и ограниченная маршрутизация
после qualification остаются открытыми.

Следующий инженерный этап — нативный launchd restart с тем же полным
профилем 0.12.2, сохранёнными startup/retirement logs и независимой проверкой
удаления временного job и собственных PID. Затем — подключение наблюдения
к реальному native endpoint и проверка operational recovery.
