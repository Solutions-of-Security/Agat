# Освобождение inference admission до HTTP response write

03.10.2026. Runtime `0.12.2` исправляет гонку, обнаруженную после [неуспешного длинного прогона](./continuous-soak-failure.md). Завершённый inference больше не удерживает слот во время отправки HTTP-ответа. Автоматическая маршрутизация остаётся выключенной, предметная qualification не оценивалась.

## Изменение и границы

В `0.12.1` клиент мог прочитать весь body первого ответа и отправить следующий запрос до освобождения `inference_lock` предыдущим handler. Второй запрос получал HTTP 503 `busy`, хотя модель уже закончила работу. В `0.12.2` [server](../../../../decision_runtime/server.py) сначала читает и проверяет body, выполняет inference и завершает opted-in cancellation watcher, затем освобождает lock в `finally` и записывает ответ. Ошибки чтения и разбора body проходят ту же границу освобождения.

Слот по-прежнему занят во время чтения body и inference. Реальное перекрытие вычислений отвергается без retry; HTTP status mapping, timeout, метрики и retirement сохраняют прежний контракт. HTTP handlers завершаются перед закрытием backend. Cancel event принадлежит одному запросу и не передаётся следующему.

[Новый профиль](./profiles/runtime-0.12.2.json) экспортирован командой `profile` на resident decider с pinned MLX dependencies, 2048 input tokens, cache 128 МиБ и isolated deadline 5000 мс. Fingerprint: `81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`. Исторический профиль `0.12.1` сохранён; его evidence не квалифицирует новую версию.

## Проверки

Три новые regression tests задерживают первый response writer после передачи всех bytes. До fix все три получают неожиданный `busy`; после fix последовательный запрос проходит для успешного ответа, invalid JSON и opted-in cancellation. Fixture backend вызван ровно два раза для двух успешных запросов; cancel events разные и не установлены. Отдельный controlled probe подтвердил переход HTTP 200 → 503 к HTTP 200 → 200 при полностью прочитанном первом body.

Целевой набор server/cancellation/metrics/isolation: 35 tests pass. Полный runtime: 125 tests, три прежних opt-in skips; остальные прошли. Проверка настоящей конкурентности по-прежнему подтверждает `busy` при занятой модели.

Полный docs/scripts набор после обновления profile-binding test: 12 Node и 466 Python tests, четыре прежних opt-in skips; остальные прошли. Семь целевых profile tests проверяют текущий экспорт и сохранённую историческую привязку. Подмена версии дополнительно проверяется на изменение исходника, чтобы такой negative test не стал пустой операцией после следующего обновления. [Sanitized checks](./evidence/2026-10-03/http-admission-release/checks.json) содержат before/after исходы и SHA приватных logs; исходные tracebacks и системные данные не публикуются.

Реальный MLX smoke на commit `ebf49a6959f49b607b009821af8e84e379c89459` прошёл два блока по 20 секунд: 209 measured calls и три отдельных warmup, 40 285,180 мс измерений, ошибок и изменений решений нет. Pooled p95 — 278,486 мс. [Source-bound сводка](./evidence/2026-10-03/http-admission-release/smoke-summary.json) экспортирована после независимого verifier pass; профиль и идентичность процессов стабильны, cleanup errors и оставшихся owned PID нет. Фактическое завершение всех собственных PID проверено отдельно после launcher.

Короткий smoke подтверждает интеграцию нового профиля и cleanup. Он не заменяет полный двухчасовой gate и не доказывает SLO или предметное качество на новых данных.

## Следующий gate

Повторить полный 7200-second [continuous soak](./continuous-soak.md) с профилем `0.12.2`, затем выполнить Temporal/RAG на этом же профиле. После текущего этапа работа остановлена по указанию пользователя; полный повтор не запускался. Прежний внеплановый exit 75 при совместной загрузке моделей остаётся отдельным открытым вопросом; этот fix не объявляется его объяснением.

## Основание

Граница inference/transport выбрана по чтению pinned source и воспроизводимому probe. [Python HTTP server](https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler.wfile) описывает output stream, а [Python threading locks](https://docs.python.org/3/library/threading.html#lock-objects) — немедленный отказ nonblocking acquire. Эти источники подтверждают семантику API; сам дефект подтверждён тестом конкретного server. Источники проверены 03.10.2026.
