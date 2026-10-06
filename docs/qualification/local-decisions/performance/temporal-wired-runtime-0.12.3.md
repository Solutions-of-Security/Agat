# Temporal PostgreSQL и RAG с wired runtime 0.12.3

06.10.2026 MSK. **Оба embedding transport прошли реальный Temporal/PostgreSQL/RAG
процесс с wired budget 4096 МиБ, fallback, восстановлением модели и worker.**
После cleanup отдельно воспроизведены обе Temporal histories. Профиль совпадает
с завершённым [двухчасовым shared soak](./wired-shared-soak-7200-0.12.3.md).
Постоянная resident-служба 0.12.2 сохранила свои PID, profile и свежий scrape.

## Закреплённый профиль и окружение

Measured commit — `308f2280a98c1a7da4cbaa6091992f73dbaac913`, merged main
после [PR #134](https://github.com/Solutions-of-Security/Agat/pull/134).
Frozen plan закрепляет **206 source files**, модели decider-2b, Qwen3 8B
и embeddinggemma, fixture, policy и MLX dependency pins.
[Profile SHA](./profiles/runtime-0.12.3-wired-4096.json) —
`776fba7fa1244e13292ac3605a53a6d6c6cf557901c8d863cab9489d6d2f47ce`:
2048 input tokens, cache 128 МиБ, wired limit 4096 МиБ и isolated inference
deadline **5000 мс**. Initial startup и оба recovery используют одинаковый argv.
Автоматическая маршрутизация выключена, qualification — `not_assessed`.

Для опыта использована отдельная официальная [Ollama 0.35.1](https://github.com/ollama/ollama/releases/tag/v0.35.1).
SHA архива Darwin проверен по release metadata:
`3137dbf28948ee844e0fb3e584d9b5de6879d73d9f0cb7eff3ad64930601d307`.
Все извлечённые binary/library bytes сверены после cleanup. Установленная
Ollama 0.40.0 не заменялась. PostgreSQL 17.6-alpine и Temporal 1.8.1
использовали собственные контейнеры с loopback-портами; Node 24.14.0
и Python 3.13.12 соответствуют закреплённому тестовому окружению.

## Проверенный результат

| Проверка | isolated | session |
|---|---:|---:|
| Primary calls / embedding items | 3 / 5 | 3 / 5 |
| Shadow inference / unavailable fallback | 2 / 1 | 2 / 1 |
| SIGKILL decider, primary fallback и restart | pass | pass |
| Потерянный tick, restart worker, прежний Workflow Run ID | pass | pass |
| Stage/shadow snapshots и source provenance | pass | pass |
| Tenant RLS и запрет release registry | pass | pass |
| History events | 63 | 63 |
| Native replay внутри harness и после cleanup | pass | pass |

Всего выполнены **6 primary calls, 10 embedding items, 4 shadow inference,
2 unavailable fallback, 3 отдельных warmup и 2 restart decider**.
Все 12 source citations проверены. Полный launcher, включая загрузку моделей
и cleanup, занял **131,581 с**. Это длительность одного development опыта
на общей машине, а не production SLO.

[Offline verifier](../../../../scripts/verify-temporal-real-rag.py) независимо
восстановил profile из archived runtime bytes и wired plan, проверил model
identity, policy, request/result bindings, recovery, RLS и histories.
Все 79 resource observations прошли проверку; сырые счётчики остаются приватными.
[Отдельный replay](../../../../scripts/replay-temporal-real-rag.ts) после cleanup
подтвердил обе истории с точно измеренным workflow bundle.
Разделение integration execution и history replay соответствует
[руководству Temporal](https://docs.temporal.io/develop/typescript/best-practices/testing-suite).

Независимое сравнение с committed development baseline 28.09 подтвердило
все шесть prompt/output SHA и token counts, векторы пяти различных embedding
inputs, а также четыре shadow signatures, distributions и token counts.
Точные совпадения относятся к прежней fixture и не оценивают независимые
бизнес-задачи. После опыта повторно проверены все bytes resident decider-модели
и неизменность 206 измеряемых source files.

## Cleanup и дальнейшая работа

Обе модели Ollama выгружены, четыре собственных контейнера удалены.
Нативная инвентаризация после launcher подтвердила отсутствие всех **81**
наблюдавшихся собственных PID и четырёх container names. Cleanup errors нет.
Действующие resident runtime 0.12.2 и Prometheus сохранили PID и health/profile;
после опыта проверен свежий scrape. Anonymous volumes для этого опыта отдельно
не инвентаризировались; их удаление и сохранение named data проверили 17
cleanup-тестов с включёнными live Docker fixtures перед запуском.

[Публичная сводка](./evidence/2026-10-06/temporal-wired-runtime-0.12.3/result-summary.json)
сохраняет counts, profile/source identity и SHA приватных artifacts.
Raw phases, histories, shadow journals, process/model logs, наблюдения,
независимые audits и измеренный source archive хранятся в игнорируемом
`docs/private`. В исходном пользовательском checkout сохранён проверенный
приватный архив evidence с CRC и file SHA/size inventory.

Перед опытом прошли typecheck, 100 Temporal checks с четырьмя opt-in skips,
полный `docs:check` с 603 Python / 12 Node checks и отдельно 17 cleanup checks
с live Docker fixtures. Модельный gate и оба независимых audits — pass.

Следующий этап — постоянный rollout проверенного wired resident package 0.12.3
со свежим scrape и сохранённым rollback. Boot/login acceptance, независимые
предметные данные и human reviews, calibration/holdout и owner/SLO остаются
открытыми. Успешный gate не устанавливает причину прежних timeout.
