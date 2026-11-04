# Неуспешный длинный прогон и гонка HTTP admission

03.10.2026. [Двухчасовой план](./continuous-soak.md) на runtime `0.12.1` **не выполнен**: нагрузка остановилась после одного `busy`. [Sanitized evidence](./evidence/2026-10-03/continuous-soak-failure/result-summary.json) фиксирует отказ; raw logs, PID и системные ресурсы остаются в `docs/private`.

## Фактический результат

Измеренный commit: `dd8db6c5fd194f20ade8f739f5597d81d7f09efe`. Профиль: `aeda3e1818101bd91f920201bb2adfa1c10463894a456924a79d17d4c12a9c25`. Один runtime, concurrency 1, без retry/restart; 15 approved development inputs. План — 7200 секунд; фактически измерено **2 314 407,717 мс** (около 38,57 минуты), 10 031 попытка: 10 030 завершённых решений и один unavailable `busy`. Три warmup считаются отдельно.

| Блок | Измерения | Запросы | Исход |
|---|---:|---:|---|
| 1 | 900 051,323 мс | 4080 | duration_complete |
| 2 | 900 318,978 мс | 3911 | duration_complete |
| 3 | 514 037,416 мс | 2040 | request_failed: один busy |

Последний отказ получен за 3,362 мс после завершённого результата предыдущего запроса. На последнем health/resource sample профиль и все три runtime PID оставались доступными; fingerprints завершённых решений не изменились. Launcher сохранил третий частичный блок и журнал, завершил все наблюдаемые собственные процессы; cleanup errors нет. Дополнительный native/system observer также завершился без ошибок. Его системные счётчики относятся ко всей машине и не доказывают причину отказа.

Полный offline verifier отказал этому запуску; verification artifact не создан. Экспорт публичной успешной сводки также допускает только полностью проверенные прогоны. Частичный прогон не переименован в success, его длительность и request budget не уменьшены после просмотра результата.

## Детерминированное воспроизведение

В измеренном HTTP server из commit `dd8db6c` `inference_lock` освобождался в `finally` после `self.reply(...)`. Клиент может получить весь body до возвращения серверного `wfile.write` и выполнения этого `finally`. Следующий запрос в этот момент видит занятый lock, хотя предыдущее вычисление уже закончено. [Исправление в 0.12.2](./http-admission-release.md) освобождает слот до записи ответа.

Контролируемый probe использует собственный loopback server и fixture backend. Handler первого ответа задержан **после** передачи всех bytes; первый клиент полностью читает body, затем отправляется второй запрос. Получено: первый HTTP 200, второй HTTP 503 `busy`, backend вызван только один раз. Это воспроизводит гонку границы inference/transport независимо от MLX и нагрузки ОС. Отдельный probe JSON сохранён приватно; публичная evidence содержит исход и SHA измеренного server.

Это подтверждённый дефект освобождения admission, но не доказательство причины прежнего внепланового exit 75 при совместной загрузке моделей. В текущем отказе backend оставался доступным до controlled teardown. Отсутствие подробного access log не позволяет восстановить каждое сетевое событие естественного отказа; детерминированный probe подтверждает сам найденный механизм.

## Следующий этап

Освобождать inference lock после вычисления и завершения opted-in cancellation watcher, **до** записи HTTP-ответа. Настоящая конкуренция во время inference должна по-прежнему возвращать busy; invalid/read-timeout ветки должны освобождать lock; retirement должен дождаться ответов. Нужны regression tests с задержкой response writer и повторная проверка cancellation/isolation/metrics.

Изменение serving source требует новой runtime version и полного fingerprint. Исторический профиль `0.12.1` сохраняется; его qualification/evidence не переносятся на исправленную версию. После fix — реальные короткие проверки и новый полный 7200-second план, затем Temporal/RAG с явно закреплённым новым профилем. Прикладная qualification и production SLO остаются открытыми.

## Источники методики

[Python HTTP server](https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler.wfile) описывает thread-per-request обработку и output stream; [Python threading locks](https://docs.python.org/3/library/threading.html#lock-objects) — немедленный отказ nonblocking acquire при занятом lock. Причина в конкретном server установлена чтением pinned source и controlled probe, а не выводится из документации. Источники проверены 03.10.2026.
