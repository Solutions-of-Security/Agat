# Выход embedding helper после принудительного завершения worker

Дата: 2026-09-27. Область: обычные Python workers на macOS/Linux, `isolated` и `session`.

[Graceful drain](./embedding-worker-drain.md) не покрывал SIGKILL. Воспроизведён дефект: после принудительного завершения worker helper продолжал ждать model HTTP, сохраняя соединение, хотя владелец результата уже отсутствовал. Все четыре исходных PostgreSQL-сценария — ожидание headers/body × isolated/session — провалились на незакрытом соединении через 5 секунд, до настроенного HTTP deadline 30 секунд. Cleanup теста отдельно завершил только наблюдавшиеся собственные helpers. [Baseline log](./evidence/2026-09-27/embedding-parent-exit/baseline.log).

## Изменение

При запуске helper worker на Unix передаёт свой PID отдельным внутренним аргументом. До чтения IPC helper сверяет ожидаемого родителя с `os.getppid()`: если worker уже завершился во время запуска интерпретатора, запрос не начинается. Далее небольшой daemon-поток проверяет привязку с интервалом ожидания 50 мс. После смены родителя завершается **только текущий helper**, включая его sockets и pipes, даже если urllib или запись ответа ожидают I/O. Никакой `/complete`, `/fail` или retry от этого механизма не отправляется.

Ожидаемый PID берётся до `Popen`, а не из текущего PPID после импорта: иначе уже осиротевший helper мог бы принять init за своего владельца. Проверка установлена в обеих production entry points. Токен и source text по-прежнему передаются через private stdin, не в argv. Размеры сообщений, deadlines, pool capacity и default `isolated` сохранены.

Основания выбора:

- [Python `os.getppid`](https://docs.python.org/3/library/os.html#os.getppid) описывает смену родителя на Unix после его выхода. На Windows исходный PID сохраняется и может быть переиспользован, поэтому этот механизм там **не включается**. Проверка поведения Windows в целом не заявляется.
- [Python `subprocess`](https://docs.python.org/3/library/subprocess.html#subprocess.Popen) предупреждает о deadlock с `preexec_fn` в многопоточном приложении. Здесь worker не выполняет Python callback между fork и exec; guard запускается уже в отдельном интерпретаторе.
- [Python `os._exit`](https://docs.python.org/3/library/os.html#os._exit) завершает процесс без Python cleanup/flush. Это намеренно ограничено helper после подтверждённой потери владельца: у него нет shared state, а ждать заблокированный stdout или urllib больше некому. Exit status осиротевшего процесса собирает внешний init/subreaper, не погибший worker.

50 мс — период ожидания проверки, **не hard upper bound** завершения при произвольной нагрузке или зависании ОС. Backend inference может продолжаться после TCP disconnect; такая гарантия здесь не добавляется.

## Независимое воспроизведение

Тест использует настоящий coordinator main, PostgreSQL 17.6 и обычный `worker_loop`. Probe наблюдает PID реально созданного subprocess через `Popen`, затем SIGKILL посылается только worker. В тесте не заменены HTTP transport, signal handler или lifecycle-функции. Model endpoint удерживает headers либо незавершённый JSON body.

После исправления исходные четыре сценария прошли:

| Ожидание | Transport | SIGKILL → model HTTP close, мс |
|---|---|---:|
| Headers | isolated | 45,0 |
| Body | isolated | 65,5 |
| Headers | session | 42,1 |
| Body | session | 44,1 |

[Финальный targeted log](./evidence/2026-09-27/embedding-parent-exit/postgres.log) содержит 4/4 pass за 7,352 с. Дополнительно проверено, что helper больше не исполняется: PID отсутствует или имеет terminal zombie state до внешнего reap. Это не подменяется одним наблюдением TCP close.

Незавершённый durable job остаётся у прежнего lease, без вектора и terminal-события; погибший worker ничего не подтверждает. Затем тест истекает только этот lease, вызывает настоящий maintenance и запускает другого зарегистрированного worker. Новая аренда сохраняет единственный replacement vector `[0,1]` и ровно один ready-event. Модель вызывается два раза: прерванная попытка и успешная повторная аренда. Мгновенная выдача нового lease в момент SIGKILL не обещается.

## Проверки

- Новые unit tests: невалидные аргументы parent binding и отсутствие значения PID, отказ до request I/O, настоящий SIGKILL владельца при непрерывном body для обоих transport, отсутствие ложного PPID-liveness механизма на Windows.
- Полный worker на macOS/Python 3.14.3: **100 pass + 1 skip** (LangGraph отсутствует на host). В [собранном Linux-образе](./evidence/2026-09-27/embedding-parent-exit/linux.log), Python 3.13.15: **101/101 pass**. Импортированы `/opt/agat` runtime-модули и проверено точное совпадение их bytes с измеряемыми исходниками; новые subprocess unit tests также берут эту runtime-директорию.
- Image: `agat-worker:embedding-parent-exit-20260927`, `sha256:927050befe221bf4b1b3c47ddffb494cc540b313acf3452dac24de5b0b7fc619`. Контейнер теста запущен с `--init --network none`; внешний init собирает exit status умышленно осиротевших тестовых процессов. Это не rollout и не изменение deployment manifest.
- Два существующих профайлера обновлены для внутреннего аргумента owner PID: путь helper больше не обязан быть последним элементом argv. Два новых теста с реальным HTTP сначала воспроизвели пропуск процесса, затем подтвердили правильный учёт и закрытие. Исторические evidence проверяются по прежним commits и не переписаны.
- Регрессии настоящего worker/PostgreSQL: model HTTP bounds/deadlines, graceful SIGTERM и renewal, parent SIGKILL, definitive renewal rejection, lost COMMIT acknowledgement во всех уже поддержанных режимах. **26/26 pass**, 231,737 с; итог и полный журнал — в [checks.json](./evidence/2026-09-27/embedding-parent-exit/checks.json).

```bash
python3 -m unittest discover -s workers -p 'test_embedding_parent_exit.py' -v
bash scripts/test-fleet-ha-postgres.sh \
  --test-name-pattern='helper exits when its worker is killed'
# Все сценарии настоящего embedding worker:
bash scripts/test-fleet-ha-postgres.sh --test-name-pattern='real Python embedding'
```

Следующий gate — измерить накладные расходы guard на зафиксированных model inputs и проверить восстановление нескольких независимых владельцев при остановке одного из них. Исторические performance evidence относятся к своим закреплённым commits; новые результаты туда не подставляются. Windows parent-death lifecycle, deployment grace policy, завершение backend inference и предметная qualification остаются отдельными границами.
