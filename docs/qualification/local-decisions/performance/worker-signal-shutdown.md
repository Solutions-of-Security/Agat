# Завершение worker при SIGTERM и SIGINT

08.10.2026 MSK. CI evaluator PR 154 остановился на idle embedding worker
restart/shutdown case: 112/113 PostgreSQL checks прошли. Первый worker завершился
с exit 0 без active requests/live threads; timeout возник при restart/cleanup.
Старый harness не выводил successor diagnostic и мог скрыть первоначальную
ошибку исключением из `finally`. Исходный log сохранён в `docs/private`.

Локальный повтор двух прежних idle tests прошёл. Анализ обнаружил отдельно
воспроизводимый дефект: handler вызывал `Event.set()`, захватывающий nonreentrant
Condition. При сигнале во время владения тем же lock в main thread handler
ждёт самого себя. [Python signal](https://docs.python.org/3/library/signal.html)
предупреждает о deadlocks при synchronization primitives внутри handlers.
Без stack trace зависшего CI process точный механизм того единственного
отказа не утверждается; подтверждённая гонка исправлена отдельно.

Handler теперь устанавливает только локальный boolean flag. Main loop
проверяет его перед admission и перед переходом от пустой primary lease к
embedding lease. Poll wait разбит на интервалы до 0.2 s, сохраняя прежний
полный период между HTTP polls. Event.set выполняется в обычном main flow;
уже назначенные futures проходят прежний drain. Активные HTTP calls не
отменяются этим флагом, их timeout/lease renewal сохраняются. Это worker
lifecycle fix, не изменение модельного scoring/profile или customer SLO.
Resident не перезапускался.

Idle harness теперь включает successor diagnostic и выполняет все cleanup
steps даже при отказе одного. Cleanup errors не заменяют исходную ошибку;
если основная проверка прошла, cleanup errors сами дают failure. Таймауты
не увеличены и assertions не исключены.

[Три регрессии](../../../../workers/test_worker_signal_shutdown.py) отправляют
настоящие POSIX signals в собственный child. SIGTERM/SIGINT вводятся на границе
удерживаемого Event lock; третий случай прерывает пустой primary poll и проверяет
отсутствие новой embedding admission. До fix: два timeout/deadlock и одна
failed assertion. После: 3/3 pass без live threads; timed-out children reaped.
Полные worker checks: 152 tests, 149 pass / 3 optional skips. Повторный
PostgreSQL/CI результат фиксируется после завершения обязательных jobs.
