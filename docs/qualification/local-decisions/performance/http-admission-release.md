# Освобождение inference admission до HTTP response write

03.10.2026. Runtime `0.12.2` исправляет гонку, обнаруженную после [неуспешного длинного прогона](./continuous-soak-failure.md). Завершённый inference больше не удерживает слот во время отправки HTTP-ответа. Автоматическая маршрутизация остаётся выключенной, предметная qualification не оценивалась.

## Изменение и границы

В `0.12.1` клиент мог прочитать весь body первого ответа и отправить следующий запрос до освобождения `inference_lock` предыдущим handler. Второй запрос получал HTTP 503 `busy`, хотя модель уже закончила работу. В `0.12.2` [server](../../../../decision_runtime/server.py) сначала читает и проверяет body, выполняет inference и завершает opted-in cancellation watcher, затем освобождает lock в `finally` и записывает ответ. Ошибки чтения и разбора body проходят ту же границу освобождения.

Слот по-прежнему занят во время чтения body и inference. Реальное перекрытие вычислений отвергается без retry; HTTP status mapping, timeout, метрики и retirement сохраняют прежний контракт. HTTP handlers завершаются перед закрытием backend. Cancel event принадлежит одному запросу и не передаётся следующему.

[Новый профиль](./profiles/runtime-0.12.2.json) экспортирован командой `profile` на resident decider с pinned MLX dependencies, 2048 input tokens, cache 128 МиБ и isolated deadline 5000 мс. Fingerprint: `81663153638983bd72afd5a31d8871f38c7db064e28a37636a36b780cf0b6cef`. Исторический профиль `0.12.1` сохранён; его evidence не квалифицирует новую версию.

## Проверки

Три новые regression tests задерживают первый response writer после передачи всех bytes. До fix все три получают неожиданный `busy`; после fix последовательный запрос проходит для успешного ответа, invalid JSON и opted-in cancellation. Fixture backend вызван ровно два раза для двух успешных запросов; cancel events разные и не установлены. Отдельный controlled probe подтвердил переход HTTP 200 → 503 к HTTP 200 → 200 при полностью прочитанном первом body.

Целевой набор server/cancellation/metrics/isolation: 35 tests pass. Полный runtime: 125 tests, три прежних opt-in skips; остальные прошли. Проверка настоящей конкурентности по-прежнему подтверждает `busy` при занятой модели.

Результат короткого реального MLX smoke фиксируется отдельной source-bound сводкой после запуска на committed source. Он проверяет интеграцию нового профиля и cleanup; двухчасовой gate требует собственного полного повторного измерения.

## Следующий gate

Повторить полный 7200-second [continuous soak](./continuous-soak.md) с профилем `0.12.2`, затем выполнить Temporal/RAG на этом же профиле. После текущего этапа работа остановлена по указанию пользователя; полный повтор не запускался. Прежний внеплановый exit 75 при совместной загрузке моделей остаётся отдельным открытым вопросом; этот fix не объявляется его объяснением.

## Основание

Граница inference/transport выбрана по чтению pinned source и воспроизводимому probe. [Python HTTP server](https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler.wfile) описывает output stream, а [Python threading locks](https://docs.python.org/3/library/threading.html#lock-objects) — немедленный отказ nonblocking acquire. Эти источники подтверждают семантику API; сам дефект подтверждён тестом конкретного server. Источники проверены 03.10.2026.
