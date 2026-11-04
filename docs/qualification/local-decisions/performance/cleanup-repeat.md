# Полный Temporal/RAG после cleanup-исправлений

03.10.2026. **Оба embedding transport прошли полный real-model процесс**
с неизменным runtime 0.12.1 после исправлений inventory, Docker cleanup,
временного PostgreSQL storage и сохранения приватных process logs.
Закрывает повтор, ранее остановившийся на ENOSPC в
[протоколе process logs](./private-process-logs.md).

## Профиль и подготовка

Измеренный commit — `3deca95cfa4adaf1695340749d098b6833b6a14e`, включённый в
`main` через [PR #108](https://github.com/Solutions-of-Security/Agat/pull/108).
Frozen plan закрепляет 205 файлов. Резидентный экспорт MLX после восстановления
локальных файлов точно совпал с [committed профилем 0.12.1](./profiles/runtime-0.12.1.json):
SHA `aeda3e1818101bd91f920201bb2adfa1c10463894a456924a79d17d4c12a9c25`.
Pinned revision и все SHA decider, manifests Qwen3/embeddinggemma, policy,
2048 токена, cache 128 MiB, inference deadline 5000 мс и startup deadline
не менялись. Обнаруженные cloud-файлы разобраны в
[проверке резидентности](./model-residency.md); их восстановление не включено
в длительность workload.

Использованы Node 24.14.0, отдельные закреплённые MLX/worker окружения и
собственные model servers, PostgreSQL и Temporal. Основной пользовательский
checkout с незакоммиченными изменениями сохранён. Ресурсные samples,
process logs, histories и полные phase reports находятся в игнорируемом
`docs/private/2026-10-03/cleanup-repeat/` изолированного checkout.

## Результат и перепроверка

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Shadow inference / unavailable fallback | 2 / 1 | 2 / 1 |
| SIGKILL decider, primary fallback и restart | pass | pass |
| Retry tick, Temporal worker restart, тот же Workflow Run ID | pass | pass |
| Принятые stage/shadow snapshots и source provenance | pass | pass |
| Tenant RLS и ограничение release registry | pass | pass |
| Native replay внутри harness | pass | pass |
| Независимый native replay после cleanup | pass | pass |

[Независимый verifier](../../../../scripts/verify-temporal-real-rag.py)
пересчитал source SHA из измеренного Git commit, implementation fingerprint
runtime, полный профиль, входы/ответы, времена recovery, policy validation
и ресурсные observations. Проверены 12 source citations, три отдельных warmup
и два restart decider. [Сравнение с прежним baseline](./evidence/2026-10-03/cleanup-repeat/baseline-comparison.json)
подтвердило все шесть prompt/output SHA, token counts и векторы пяти входов.

Полный launcher занял **179,464 с**. Четыре собственных контейнера удалены,
модели выгружены; наблюдавшихся собственных PID и cleanup errors не осталось.
Это длительность одного опыта на общей машине, без причинного сравнения скорости.
Итоговый launcher report и приватные process logs сохранены; повтор ENOSPC
в этом прогоне не наблюдался. Проверка удаления анонимных volumes выполнена
отдельными live regression tests в PR #108; их ID в этом полном опыте отдельно
не записывались.

[Публичная сводка](./evidence/2026-10-03/cleanup-repeat/result-summary.json)
содержит counts, исходный commit, профиль и SHA приватных артефактов без
идентификаторов процессов и измерений ресурсов хоста. Реплей после cleanup
выполнен [штатным инструментом](../../../../scripts/replay-temporal-real-rag.ts)
на том же измеренном workflow bundle. Разделение интеграционного испытания
и history replay соответствует
[руководству Temporal](https://docs.temporal.io/develop/typescript/best-practices/testing-suite).

Граница результата — один успешный полный повтор после cleanup-исправлений.
Прежние неудачные опыты сохранены, их причины этим успехом не устанавливаются.
Независимые бизнес-данные, человеческая qualification, многочасовая стабильность
и производственные SLO остаются открытыми. Следующий инженерный этап —
измерить непрерывную работу pinned runtime в течение нескольких часов:
текущий endurance probe ограничен 1800 секундами и 10 000 запросами.
