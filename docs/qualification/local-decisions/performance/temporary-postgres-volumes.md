# Удаление временного storage тестовых RAG-контейнеров

28.09.2026. Продолжение [Docker cleanup](./container-cleanup-failures.md).
Установленный `postgres:17.6-alpine` объявляет `/var/lib/postgresql/data` как
Docker volume. Отдельный опыт с этим image без запуска сервера подтвердил:
после `docker rm --force` созданный анонимный volume остаётся. После измерения
контейнер и его volume удалены по записанным собственным идентификаторам.

## Исправление

[Python fallback](../../../../scripts/run-temporal-real-rag.py) и EXIT traps
обоих launcher — [PostgreSQL](../../../../scripts/test-temporal-postgres-rag.sh)
и [Temporal](../../../../scripts/test-temporal-rag.sh) — удаляют собственные
контейнеры с `--volumes`. Это охватывает обычное завершение и раннюю ошибку startup.
Обход нескольких контейнеров и сохранение ошибок cleanup остаются прежними.

Выбор следует [семантике Docker volumes](https://docs.docker.com/engine/storage/volumes/):
данные volume живут независимо от контейнера, включая автоматически созданные
анонимные volumes. [Опция `docker rm --volumes`](https://docs.docker.com/reference/cli/docker/container/rm/)
удаляет связанные анонимные volumes и сохраняет явно именованные volumes.
Инициализационный bind mount не удаляется. Поиск и удаление старых либо чужих
volumes в это изменение не входят.

## Воспроизведение и проверки

Три новых [live regression tests](../../../../scripts/test/test_temporal_volume_cleanup.py)
провалились на прежнем коде: контейнер исчез, а disposable volume сохранился.
Fixture закрыла собственные контейнеры и volumes даже после failed assertions.

Проверка Python вызывает настоящий fallback. Проверки обоих shell launcher
исполняют настоящий Bash и его EXIT trap: fixture заменяет сборку и запуск
сервисов небольшим BusyBox-контейнером с анонимным и именованным volumes,
затем вводит ошибку при получении порта. Ожидаемый startup exit code `73`
сохраняется, контейнер и анонимные данные удаляются. Именованный volume и
записанный в нём marker остаются; проверка читает marker через mount только
для чтения. После проверки fixture удаляет лишь созданные ею ресурсы.

После исправления все 74 целевые Temporal/profile/verifier/cleanup tests прошли,
включая прежние три Docker fault scenarios и три новые volume checks.
В обязательном CI job `test` live discovery расширен на оба cleanup-набора.
Это проверяет жизненный цикл временного storage, но не является доказательством
причины прежнего ENOSPC и не очищает накопленный storage других запусков.

Отдельная перепроверка исправленного Python fallback на самом PostgreSQL image
также подтвердила удаление его анонимного data volume; сервер БД для неё не запускался.
Полный docs check: 12 Node и 430 Python tests — pass; четыре opt-in Docker tests
пропущены в обычном discovery и отдельно пройдены выше. Проверены process catalog,
2123 локальные ссылки в 231 Markdown-файле, Bash syntax и изменённый CI workflow
через `actionlint`. [Checks, image ID и SHA исходников/журналов](./evidence/2026-09-28/temporary-postgres-volumes/checks.json)
сохранены отдельно; сырые журналы находятся в `docs/private`.

Следующий gate — полный повтор реального Temporal/PostgreSQL/RAG с тем же
профилем runtime 0.12.1 после всех трёх cleanup-исправлений, offline verifier
и независимый native replay. Предметная qualification и production SLO открыты.
