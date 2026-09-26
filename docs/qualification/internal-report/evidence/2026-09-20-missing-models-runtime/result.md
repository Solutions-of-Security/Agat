# Квалификация внутреннего отчёта

Результат: **FAIL**. Начало: 2026-09-20T19:42:12.910Z.

| Проверка | Статус | Код причины |
|---|---|---|
| Безопасная конфигурация и версии зависимостей | PASS | — |
| Локальный Ollama | PASS | — |
| Установленная локальная модель Qwen3 | FAIL | MODEL_NOT_INSTALLED |
| Установленная embeddinggemma | FAIL | MODEL_NOT_INSTALLED |
| Реальный запрос embeddings | FAIL | EMBEDDING_MODEL_UNAVAILABLE |
| Temporal Server и namespace | FAIL | TEMPORAL_UNAVAILABLE |
| Сборка coordinator и Temporal worker | NOT_RUN | — |
| Изолированный runtime и реальные pollers | NOT_RUN | — |
| Установка пакета и блокировка неготового запуска | NOT_RUN | — |
| Индексация синтетических источников | NOT_RUN | — |
| Три этапа Qwen3 и проверяемые цитаты | NOT_RUN | — |
| Контрольные числа, формулы и ограничения | NOT_RUN | — |
| Согласование через API и защита итогового артефакта | NOT_RUN | — |
| SIGKILL coordinator/worker и продолжение того же workflow | NOT_RUN | — |
| Авторизованное скачивание и проверка SHA-256 | NOT_RUN | — |
| История реального Temporal и завершение workflow | NOT_RUN | — |
| Неизменность моделей и входных файлов | NOT_RUN | — |
| Завершение собственных процессов и удаление временного состояния | PASS | — |

NOT_RUN означает, что проверка не выполнена; это не успешная квалификация. Согласование автоматизировано только для синтетических данных и не заменяет оценку человеком.

Доказательства: [result.json](./result.json). SHA-256 JSON: `2eb7993b8ceb0728d121aaae0badf8d82edcb13a73a42e830ec7d9fda092373e`.
