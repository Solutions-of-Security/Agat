# Локальная доступность весов перед Temporal/RAG

03.10.2026. Продолжение [сохранения приватных process logs](./private-process-logs.md).
При подготовке полного повтора runtime 0.12.1 существующий файл весов decider
имел macOS-флаг `SF_DATALESS`. Его чтение и SHA-проверка заняли 578,339 с;
после завершения SHA совпал с закреплённым checkpoint, флаг исчез. До запуска
моделей обнаружены также cloud-only metadata-файлы и Python-модули в Documents.
Эти наблюдения относятся к подготовке среды, а не к inference latency.

## Изменение

[Launcher](../../../../scripts/run-temporal-real-rag.py) проверяет доступность
manifest, snapshot и каждого объявленного файла до checksum verification
и admission процессов. `Path.stat()` следует ссылкам cache; `SF_DATALESS`
приводит к ошибке с требованием восстановить pinned weights в локальном store.
Проверка не открывает отклонённый файл, не запускает модели и не создаёт
измерительный plan. Последующая штатная проверка всех SHA остаётся обязательной.

Основание — документированная семантика
[SF_DATALESS](https://docs.python.org/3/library/stat.html#stat.SF_DATALESS)
(macOS, Python 3.13+). Нулевой `st_blocks` сам по себе не означает cloud-файл:
сжатые и sparse-файлы разрешены. На платформах без `st_flags` выполняется обычная
проверка manifest и SHA. Проверка metadata не гарантирует доступность файловой
системы после неё и не устанавливает общий deadline для чтения диска.

При восстановлении сохраняются полная revision и SHA каждого файла. Это
соответствует поддерживаемому
[скачиванию конкретной revision Hugging Face](https://huggingface.co/docs/huggingface_hub/guides/download#from-specific-version).
Serving runtime, implementation fingerprint, policy, inference/startup deadlines
и committed профиль 0.12.1 не изменены.

## Проверки

[Шесть новых тестов](../../../../scripts/test/test_temporal_model_residency.py)
проверяют отклонение каждого cloud-only файла без его открытия, admission до
хеширования и subprocess, resident symlink cache, compressed/zero-block файлы,
платформу без flags, отсутствующий файл и выход имени за snapshot. Контрольный
запуск с отключённой новой проверкой дошёл до запрещённого hashing; с проверкой
проходит. Файлы тестов явно синтетические, cloud hydration ими не запускается.

Вместе со всей Temporal cleanup/recovery матрицей и четырьмя live Docker tests
прошли **86/86** проверок. Исходная сборка coordinator/web/Temporal прошла;
исходный docs check — 12 Node и 440 Python tests (четыре Docker tests проверены
отдельно live), 2130 ссылок и каталог 13 процессов. Финальные checks и SHA
исходников записаны в [сводке](./evidence/2026-10-03/model-residency/checks.json).
Полные журналы остаются в игнорируемом `docs/private`.

Следующий gate — полный Temporal/RAG с неизменным serving-профилем 0.12.1,
контролируемым отказом/restart decider, обоими transport и независимым replay.
Предметная qualification и SLO этим исправлением не подтверждены.
