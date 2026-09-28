# Причина завершения decision runtime

28.09.2026. Runtime `0.12.1` сохраняет причину отказа перед штатным выходом сервиса с кодом 75. Проверены реальные отдельные HTTP/IPC-процессы с тестовым backend и четыре foreground-процесса с закреплёнными весами MLX. Это диагностика контролируемых отказов; причина прежних внеплановых завершений при совместной загрузке моделей остаётся неизвестной.

## Поведение

При `serve --exit-on-backend-unavailable` CLI сначала выходит из контекста HTTP-сервера, который ожидает уже принятые обработчики. Затем `backend.close()` завершает и собирает дочерний процесс. После этого [logger](../../../../decision_runtime/lifecycle.py) записывает одну JSON-строку в stderr с немедленным flush. Возврат 75, внешний restart, fallback и правила отмены сохраняются.

| Поле | Содержание |
|---|---|
| `schemaVersion` | `agat.decision.retirement.v1` |
| `eventName` | `decision.backend_retired` |
| `runtimeVersion`, `profileSha256` | Версия и fingerprint обслуживавшего запросы runtime |
| `exitCode` | 75 |
| `reason` | `inference_timeout`, `inference_cancelled`, `backend_error` или `backend_unavailable` |
| `childPid`, `childExitCode` | Числовые сведения о собственном вычислителе; `null`, если недоступны |

Неизвестная причина заменяется на `backend_unavailable`; неизвестные поля отбрасываются. Смерть ребёнка в простое попадает в эту категорию, а код отрицательного сигнала помогает отличить её от штатного завершения. Это наблюдаемый механизм отказа, а не доказательство первопричины или OOM.

Содержимое запроса, его ID/fingerprint, путь модели и сообщение исключения backend в событие не входят. Размер проверяется тестами: менее 512 байт. SIGTERM с выходом 130, ошибки входа, `context_too_long` и прежний режим без флага retirement-событие не создают. При отказе stderr запись выполняется best effort; inference не повторяется. SIGKILL самого HTTP-процесса или сбой до готовности не гарантируют такую запись. `childExitCode: null` не является подтверждением завершения ребёнка.

Порядок shutdown согласован с [Python socketserver](https://docs.python.org/3/library/socketserver.html): `server_close` ждёт не daemon-обработчики; `shutdown` вызывается из другого потока. Семантика кода ребёнка соответствует [multiprocessing.Process.exitcode](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Process.exitcode). Фиксированное имя события и отдельные атрибуты согласуются с моделью [OpenTelemetry Logs](https://opentelemetry.io/docs/specs/otel/logs/data-model/); этот формат не объявляется OTLP-экспортом.

## Реальная проверка MLX

[Ограниченный probe](../../../../scripts/check-decision-retirement.py) использовал один уже опубликованный синтетический [Choice-вход](../request.example.json), decider-2b revision `b37f7e1ba3fbc9238004cf531fabbee2619973fd`, 2048 токенов и allocator cache 128 MiB. До inference зафиксированы 23 исходных файла из commit `9841706f05443ad25417d5e311b8fa258edecd74`. После опыта проверена неизменность их SHA-256.

| Сценарий | Проверенный результат |
|---|---|
| SIGKILL собственному MLX-ребёнку в простое | Без нового HTTP-запроса получены `backend_unavailable`, child exit −9 и service exit 75 |
| Явный restart на том же endpoint | Тот же профиль, распределение, typed result и token counts; при SIGTERM — exit 130 и пустой stderr |
| Отдельный deadline 100 мс | Полный HTTP 504, затем `inference_timeout` и exit 75 |
| Opt-in half-close во время inference | Полный ответ `inference_cancelled`, затем одноимённое событие и exit 75 |
| Завершение опыта | Все четыре родителя и восемь собственных дочерних процессов остановлены |

Рабочий пятисекундный профиль: `aeda3e1818101bd91f920201bb2adfa1c10463894a456924a79d17d4c12a9c25`. Профиль контролируемого timeout: `17af2b151c7c942171ce504704a480b5cb470590e2959ab844dfb879dc168a42`. Implementation SHA: `b6096c513c13cd070b8c64e300ba9a385b6d1eb70fb7a9358a95ae6de5e2b944`.

Runtime-исходники измеренного commit совпадают с итоговой реализацией. Последующее уточнение probe касается только отчёта о неудачном запуске: сохраняются partial result и stderr; при неполном inventory завершение всех процессов не объявляется доказанным.

Сырые профили, PID, ответы и stderr сохранены локально в игнорируемом `docs/private/decision-exit-diagnostics/`. Публичный [протокол проверок](./evidence/2026-09-28/decision-exit-diagnostics/checks.json) содержит только сводные результаты и SHA, без измерений ресурсов хоста. Скрипт запрещает output вне `docs/private` до загрузки модели и сохраняет журналы даже при неудачном опыте.

## Проверки и применимость

Девять новых [runtime tests](../../../../decision_runtime/tests/test_exit_diagnostics.py) проверяют реальный CLI, полный HTTP-ответ, timeout, opt-in cancellation, аварийный выход 45, исключение backend, смерть ребёнка в простое, штатный shutdown, очистку, allowlist полей, порядок drain/close/log и недоступный stderr. Пять [probe tests](../../../../scripts/test/test_decision_retirement.py) отклоняют чужой профиль/PID, неверные или лишние поля, неполную запись, публичный output и проверяют сохранение отказа.

Полный decision-набор: 119 pass и три отдельные opt-in MLX-проверки skip; реальный MLX проверен описанным probe. Worker: 143/143 pass при повторном полном прогоне. Первоначальный прогон во время MLX-опыта завершился одним отказом существующего теста idle embedding helpers; точный повтор отдельно прошёл. Тест требует exit 0 всех 32 helpers, хотя runtime после 250 мс допускает принудительное завершение; код возврата проблемного ребёнка первый вывод не сохранил, поэтому причина отказа не установлена. Worker-код в этом этапе не менялся; исходный отказ и повтор сохранены локально. Docs-набор: 12 Node tests, 406 Python tests, 2098 локальных ссылок и каталог процессов — pass. Удалённый CI проверяется перед merge.

Версия и implementation fingerprint изменились. Старый профиль `4bd6…` и связанные calibration/qualification/evidence не переносятся на `0.12.1` автоматически. Нужна новая применимая проверка полного Temporal/RAG процесса с новым профилем. Контролируемый probe не доказывает качество модели, устойчивость под совместной нагрузкой, native service restart или SLO; маршрутизация остаётся отключённой.

Следующее продолжение — только по запросу пользователя. Текущий этап заканчивается после CI и merge в `main`.
