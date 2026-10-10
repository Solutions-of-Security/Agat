# Как сделать локальную модель решений для Агат

Дата исходного плана: **21 сентября 2026 года**. Статус: частично реализован; актуальный прогресс ниже. Продолжение [анализа применимости System One](./system-one-automation-assessment-2026-09-21.md).

Обновление после согласия пользователя на реализацию, **26.09.2026**: работает [локальный MLX runtime 0.12.0](./local-decision-runtime.md), реализованы [разметка и калибровка](./qualification/local-decisions/calibration/README.md), выполнено [development-сравнение утверждённой разметки v2](./qualification/local-decisions/development/README.md) и [shadow-интеграция с worker/coordinator](./qualification/local-decisions/shadow/README.md). Ниже сохранены предпосылки исходного плана; текущий прогресс зафиксирован отдельно.

Границы исходного анализа от 21.09.2026: локальное выполнение, архитектура модели, данные, обучение, калибровка, ресурсы и интеграция. На момент анализа рассматривались открытые исходники и model cards; модели ещё не скачивались и не запускались, оборудование не профилировалось. Исходные оценки не являются доказательством качества кандидатов.

## Прогресс реализации на 26.09.2026

| Блок | Выполнено | Остаток |
|---|---|---|
| Локальный baseline и API | Два закреплённых checkpoint, MLX, Choice/Boolean/Score, CLI/HTTP, fingerprint и `ok/abstain/error`; все три типа в worker, [версионированный числовой Score-контракт](./qualification/local-decisions/shadow/score-contract.md) Python/TypeScript; операторский лимит allocator cache включён в профиль; [offline-батч 2/4](./qualification/local-decisions/performance/batching.md) и [общий prefix/cache](./qualification/local-decisions/performance/prefix-cache.md) измерены, перенос в serving отклонён из-за изменения распределений | Отдельные профили и применимые calibration/eval для изменённых вычислительных путей; дальнейшие оптимизации по измерениям |
| Данные и контроль качества | Blind review packages, групповые splits, [фиксация полного профиля до измерений](./qualification/local-decisions/calibration/frozen-profile.md), temperature scaling и holdout gates; черновик v3: 48 случаев, 15 источников, 11 групп при сохранении v2/seed | Реальные независимые заявки, включая access, calibration-группы и два человеческих review; предметные критерии владельца; [расчёт требуемых групп](./qualification/local-decisions/source-review/sampling-plan.md) сохранён |
| Предметная development-диагностика | Проверенный экспорт 15 approved-assistant примеров, сравнение decider/Qwen Base и [генеративного Qwen3 8B](./qualification/local-decisions/baselines/README.md), ошибки и порядок вариантов; [эвристика по заголовкам](./qualification/local-decisions/baselines/rules.md) дала 2/15 меток и 1/15 принятых решений, её покрытие недостаточно; девять holdout-примеров не запускались | Расширение независимых calibration-групп и access-класса |
| Расчёт отчётных показателей | [Арифметический baseline](./qualification/local-decisions/performance/arithmetic.md), [CSV с закреплённым SHA](./qualification/local-decisions/performance/source-records.md), [отдельные заявки с проверкой дублей](./qualification/local-decisions/performance/request-records.md) и [новые снимки локального файла](./qualification/local-decisions/performance/source-snapshots.md); [подготовленный процесс](./qualification/local-decisions/performance/source-process.md) показывает отчёт владельцу и после подтверждения сохраняет Markdown/CSV/JSON; 47 Python-тестов, оба HTTP-сценария и desktop/mobile — pass | Реальные источники и бизнес-коннектор, фактическая проверка совокупности, единиц и смысла ID, полнота; большие отчёты вне лимита template |
| Обучение и qualification | Технический pipeline проверен; синтетический smoke — `not_qualified`, новое сравнение — `diagnostic_only` | Выбор обучения по достаточным development-данным; новый calibration и независимый holdout |
| Shadow-интеграция | Opt-in capability/profile в lease, coordinator validation и хранение, постоянный primary fallback, deadline и отказ поздних записей, retry/restart/safe replay; реальный MLX HTTP smoke `integration_pass`; PostgreSQL 17.6, два coordinator, restart и RLS — 6/6 pass; просмотр вопроса, распределения, порогов и профиля в UI, desktop/mobile — 2/2 pass; [опциональный процесс с серверным deadline](./qualification/local-decisions/shadow/isolation.md); [exit 75, сохранение ответа и foreground recovery](./qualification/local-decisions/shadow/service-recovery.md), генератор launchd; [opt-in отмена изолированного inference при уходе клиента](./qualification/local-decisions/shadow/cancellation.md), реальные MLX/worker/coordinator и сохранение primary; [отмена через измерительный прокси](./qualification/local-decisions/shadow/proxy-cancellation.md) также доведена до реального MLX; [readiness, метрики и примеры alerts](./qualification/local-decisions/shadow/observability/README.md), официальный parser и promtool — pass | [Нативный restart 0.12.2 подтверждён](./qualification/local-decisions/shadow/native-launchd-0.12.2.md) вне Documents, [настоящий Prometheus scrape/recovery прошёл](./qualification/local-decisions/shadow/observability/native-prometheus.md); [resident package прошёл native gate](./qualification/local-decisions/shadow/observability/resident-deployment.md), [два постоянных user jobs установлены и проверены](./qualification/local-decisions/shadow/observability/resident-rollout.md), [480 совместных calls на 0.12.2 прошли](./qualification/local-decisions/performance/resident-shared-load-0.12.2.md); [полный 7200-секундный wired shared gate 0.12.3 пройден](./qualification/local-decisions/performance/wired-shared-soak-7200-0.12.3.md); [native wired Temporal/PostgreSQL/RAG gate пройден](./qualification/local-decisions/performance/temporal-wired-runtime-0.12.3.md); [постоянный wired rollout 0.12.3 пройден](./qualification/local-decisions/shadow/observability/resident-wired-rollout-0.12.3.md); [bounded crash-loop wired gate пройден](./qualification/local-decisions/shadow/observability/resident-crash-loop.md); [actual boot/login 09.10 подтверждён](./qualification/local-decisions/shadow/observability/resident-actual-boot-login.md); согласование owner/SLO; ограниченная маршрутизация после qualification |
| Производительность | [180 локальных HTTP-попыток](./qualification/local-decisions/performance/README.md), конкурентность 1/2/4, задержки и busy отдельно; [8 процессов / 1200 вызовов](./qualification/local-decisions/performance/resources.md), cache 128/512 МиБ, снижение sampled active+cache с 7.933 до 3.635 ГиБ; [240 совместных вызовов с Qwen3 8B](./qualification/local-decisions/performance/shared-load.md), рост задержек при перекрытии; [81 синтетический вызов до 4096 токенов](./qualification/local-decisions/performance/context.md), точная граница отказа без усечения и память; [3086 HTTP-вызовов за 600 секунд](./qualification/local-decisions/performance/endurance.md), без ошибок/смены ответов, p95 289.099 мс и RSS по процессам | Длительная нагрузка на новых независимых документах и конфликтах, RAG на больших коллекциях, внешние бизнес-коннекторы и производственные SLO |

[Трёхэтапный workflow с реальной Qwen3 8B](./qualification/local-decisions/performance/workflow.md) прошёл через worker/coordinator: 12 процессов, 36 primary-вызовов и 18 shadow-решений при фактической конкурентности до 2. Предыдущий неполный опыт сохранил primary после server timeout. Арифметическая проверка выявила неверные средние и доли во всех повторных итогах; выполнение workflow не является приёмкой качества.

[Следующий workflow с RAG](./qualification/local-decisions/performance/rag-workflow.md) завершил 12 процессов, 44 embedding-вызова, 36 primary и 18 shadow на runtime 0.12.0. Два исходных документа проиндексированы, SHA источников и запросов перепроверены, фактическая конкурентность — 2. В 6/12 итогов арифметические ошибки; групповые ссылки не распознаются renderer на момент опыта. Ограниченная локальная RAG-нагрузка выполнена; реальные независимые источники, большие коллекции, Temporal и бизнес-коннекторы остаются открытыми.

Выявленный дефект групповых ссылок исправлен и перепроверен: `[K1, K2]` и `[K1/K2]` ведут к отдельным источникам, неизвестные/неоднозначные маркеры остаются текстом; 54 web-теста, build и desktop/mobile browser — pass. Исправление отображения не квалифицирует арифметику или выводы отчёта.

[Парный повтор RAG](./qualification/local-decisions/performance/rag-paired.md) убрал названия фаз из model-visible metadata и заранее закрепил четыре блока ABBA. Все наборы primary-prompts и выходов совпали; 12/12 повторных итогов содержат верные расчёты, 18/18 shadow-вызовов успешны. Это одна учебная задача и четыре нерандомизированных блока; причинный overhead и независимое качество не оценены. Прежние ошибочные итоги сохранены.

[Проверка отказов RAG](./qualification/local-decisions/performance/retrieval-failures.md) выявила и исправила молчаливый пустой ответ при несовместимой размерности запроса. Проверены модель/размерность индекса, отказ до primary/shadow при ошибке embeddings, отсутствие частичного retrieval и восстановление после корректного запроса. Полная регрессия coordinator — 219/219 pass; реальные большие коллекции и PostgreSQL-нагрузка для этого изменения не измерялись.

[Проверка границы локального поиска](./qualification/local-decisions/performance/retrieval-capacity.md) выявила потерю старого точного совпадения при 5001 кандидате и неправильный topK из-за раннего округления близких score. Введён явный отказ за пределом 5000 и округление после выбора результатов; 223 coordinator-теста — pass. Индексируемый поиск на больших коллекциях остаётся отдельным этапом.

[Порционное чтение RAG](./qualification/local-decisions/performance/retrieval-streaming.md) устранило воспроизведённый отказ PostgreSQL-моста на 500 допустимых 4096-мерных векторах: вместо одного ответа 39 МБ используется курсор с порциями по 64 строки и ограниченный topK. PostgreSQL Fleet/HA — 7/7 pass, включая snapshot при изменении другой репликой, RLS, закрытие курсора и rollback. Поддерживаемое число кандидатов не увеличено; это шаг к масштабированию, без нового индекса или измерения recall.

Обновление **27.09.2026**: [опыт на 66 документах и 2429 реальных embeddings](./qualification/local-decisions/performance/retrieval-corpus.md) выявил чрезмерные аллокации response buffer SQL-моста. После исправления наблюдаемый пик RSS PostgreSQL-процесса снизился с 3,21 ГиБ до 540 МиБ, векторы и все hits совпадают. Независимо проверены 160/160 поисков до/после; coordinator — 227/227, Fleet/HA — 8/8. Масштабирование сверх 5000, конкурентная нагрузка и независимые бизнес-источники остаются открытыми.

[Проверка pgvector 0.8.6](./qualification/local-decisions/performance/pgvector-compatibility.md) на отдельном PostgreSQL 17.11 завершила 13 проверок: exact поиск по 6001 разрешённому синтетическому вектору, RLS и числовые/размерностные ограничения. Прямая замена отклонена: float32 теряет различия допустимых векторов, HNSW не покрывает 4096D и на трёх запросах дал recall от 0% до 90%. Следующий совместимый шаг — точный scorer в существующем SQL-worker и замер эффекта; ANN и увеличение рабочего лимита не включены.

Этот шаг реализован: [scorer внутри PostgreSQL-worker](./qualification/local-decisions/performance/retrieval-worker-ranking.md) возвращает только выбранные metadata; все 80 списков hits совпали с предыдущим опытом. PostgreSQL warm p50 снизился с 423 до 342 мс, p95 — с 469 до 428 мс, пик RSS — с 540 до 338 МиБ. Coordinator — 231/231, Fleet/HA — 9/9, включая snapshot при конкурентной записи. В этом опыте проверялся прежний предел 5000.

[Операторский бюджет 1–10000](./qualification/local-decisions/performance/retrieval-search-budget.md) реализован с прежним default 5000, строгой проверкой конфигурации и сохранением применённого предела в trace. На Node 24 проверены 2429 и 9716 кандидатов: 160/160 поисков совпали с независимым oracle, исходные embeddings одинаковы. Большой профиль — четыре копии реальных документов, а не новые независимые источники. Coordinator — 234/234, PostgreSQL — 11/11, включая 10000 × 4096D через мост 1 МиБ. Следующий инженерный этап — конкурентная HTTP-нагрузка и задержки служебных запросов; default и правила допуска автоматических решений не меняются.

[Конкурентный HTTP-опыт](./qualification/local-decisions/performance/retrieval-http-responsiveness.md) завершён дважды: 84/84 измеряемых поиска корректны. При четырёх запросах health ожидал до 1,6 с SQLite и 5,4–6,9 с PostgreSQL; невыпущенные health probes сохранены отдельно. Повтор подтвердил 9716 готовых кандидатов непосредственно в БД. Следующий шаг — изолированный исполнитель полного retrieval, асинхронное ожидание и bounded admission с проверками транзакций, credentials и фонового обслуживания. Простая замена scorer не устранила синхронное ожидание HTTP.

[Проверка срока stage lease](./qualification/local-decisions/performance/retrieval-lease-validity.md) закрыла воспроизведённый допуск истёкшей аренды до maintenance: coordinator — 235/235, PostgreSQL — 12/12. При переназначении старый lease отклоняется, новый начинает источники с `K1`. Следующий этап изоляции должен дополнительно проверять ожидание в очереди, отзыв credentials и конкуренцию с обслуживанием.

[Изолированный исполнитель retrieval](./qualification/local-decisions/performance/retrieval-isolated-executor.md) реализован как явная зависимость HTTP-сервера: ограниченная очередь, повторная проверка token после ожидания, полный sync transaction внутри worker и отказ без replay при deadline. Пять целевых SQLite/HTTP-сценариев и PostgreSQL 13/13 прошли. Парный опыт с maintenance подтвердил 84/84 поиска: при конкуренции 4 health max PostgreSQL составил 8597 → 140 мс, SQLite — 2592 → 515 мс, но скорость самих поисков не улучшилась устойчиво. Обычный coordinator этот путь пока не включает; следующие gates — connection budget, readiness и конфликтующие изменения lease/run.

[Проверка lease после ожидания и ranking](./qualification/local-decisions/performance/retrieval-lease-locking.md) воспроизвела истечение при SQL-lock и конкурентную отмену stage. Повторная проверка перенесена перед сохранением, stage/run блокируются после чтения векторов; отдельный сценарий подтверждает продление lease во время чтения индекса. Coordinator 243/243 и PostgreSQL 17/17 прошли; подтверждены rollback и восстановление после нового lease.

[Операторский PostgreSQL opt-in](./qualification/local-decisions/performance/retrieval-postgres-opt-in.md) подключает исполнитель в обычном main, сохраняет общий connection budget и показывает health 503 после его остановки. Очередь/deadline строго ограничены, SQLite opt-in отклоняется, default остаётся sync. Coordinator 246/246 и PostgreSQL 18/18 прошли, включая реальные pools, HTTP-поиск, deadline и штатное закрытие собранного coordinator. Следующий этап — перегрузка и восстановление полного процесса с проверкой replay и фонового обслуживания.

[Shutdown с заполненной очередью](./qualification/local-decisions/performance/retrieval-shutdown-drain.md) воспроизвёл задержку ожидающего запроса при SIGTERM. Admission закрывается в начале остановки: ожидающий получает 503 до снятия SQL-lock, активный сохраняет `K1`, явный запрос после restart получает `K2` без скрытого replay. Во время blocked ranking работают HTTP heartbeat, продление lease и main maintenance. Следующая проверка — завершение долгоживущих SSE-подписок при shutdown.

[Shutdown с UI/A2A-подписками](./qualification/local-decisions/performance/coordinator-stream-shutdown.md) воспроизвёл удержание процесса открытыми HTTP-ответами. Shutdown signal завершает streams и A2A-ожидание, сохраняя tasks; проверены restart, точное возобновление event IDs и отсутствие store reads после закрытия. Следующий этап — concurrent HTTP-профиль через обычный main с реальными настройками и обслуживанием.

[Парный HTTP-профиль обычного main](./qualification/local-decisions/performance/retrieval-main-http.md) завершился на одном commit: 42/42 измеряемых поиска и 2/2 warmup корректны. При конкурентности 4 health max PostgreSQL составил 8563 → 78 мс, невыпущенные probes — 232 → 0; максимум отдельного maintenance isolated достиг 203 мс. Обе пары процессов завершились; изоляция не дала одинакового ускорения самого поиска. Девять тестов replay проверяют реальные evidence и отказы при подмене контракта. Следующий шаг — SQL-lock contention с одновременными служебными HTTP-запросами.

[Подготовленный процесс точного CSV-отчёта](./qualification/local-decisions/performance/source-process.md) проверен через HTTP и desktop/mobile UI: обязательное согласование, запрет его обхода, три файла с исходными SHA и отсутствие файлов после отклонения. Исправлено обрезание длинного заголовка на mobile. Это отдельный процесс для закреплённого снимка; в пользовательский deployment он автоматически не устанавливался.

[Адаптер отдельных заявок](./qualification/local-decisions/performance/request-records.md) считает уникальные пары «период + ID», исключает одинаковые повторы и отклоняет конфликтующие часы или признаки. Все исходные строки сохраняются; 25 перестановочных наборов сверены с целочисленным расчётом. Синтетический пример и процесс проверены, но полнота реальной выгрузки и правильность выбранного ключа этим не подтверждены.

Development-сравнение не закрывает qualification: decider получил 14/15 верных меток, но допустил ошибочное принятое решение при обратном порядке. [Синтетическая диагностика длинного текста](./qualification/local-decisions/performance/robustness.md) также выявила ошибочное принятое решение под влиянием команды в исходном тексте (0/18 правильных меток в injection-сценарии); генеративный Qwen3 8B на тех же задачах также дал 0/18 и 18 ошибочных не-abstain ответов. Пороги, метки и исходный seed по результатам не менялись. Дообучение и автоматическая маршрутизация не включены.

[Deadline при конкуренции SQL и служебного HTTP](./qualification/local-decisions/performance/retrieval-control-lock-deadline.md) закрывает воспроизведённую задержку таймера основного потока. PostgreSQL получает остаток срока на каждый statement, включая FETCH; проверены rollback, отсутствие частичных записей, закрытие admission и restart без replay. Coordinator 247/247, PostgreSQL 23/23 и typecheck прошли. Следующий этап — неизвестный исход COMMIT и повтор main HTTP-профиля после добавленных SQL-команд.

[Потеря подтверждения COMMIT](./qualification/local-decisions/performance/retrieval-commit-acknowledgement.md) воспроизведена через ограниченный loopback proxy после подтверждённого сохранения `K1`. Исправлены socket errors выданного из pool клиента и сохранение неизвестного исхода COMMIT; проверены 503/504, отказ очереди, restart без replay и замена соединения в sync. Coordinator 247/247 и PostgreSQL 26/26 прошли. Следующий этап — повтор парного main HTTP-профиля после SQL-изменений.

[Повтор main HTTP после SQL deadline](./qualification/local-decisions/performance/retrieval-deadline-http.md) завершился на одном закреплённом commit: ещё 42/42 поиска и 2/2 warmup прошли независимый replay. При конкурентности 4 health max составил 6755 → 34 мс, пропуски probes — 172 → 0, но search p50 вырос на всех уровнях. Обе пары процессов и контейнеров штатно закрыты; default sync и границы qualification сохранены. Следующая техническая проверка — истечение lease во время поздней записи retrieval/trace, с обязательным rollback при подтверждении дефекта.

[Срок lease во время поздней записи](./qualification/local-decisions/performance/retrieval-lease-write-fence.md) проверяется отдельно от ranking: baseline сохранял retrieval и событие после expiry при ожидании INSERT. Финальная проверка времени в той же транзакции откатывает обе записи; новый lease получает `K1`. Coordinator 248/248, PostgreSQL 28/28 и typecheck прошли. Следующий инженерный gate — настройка shadow-профиля в визуальном редакторе с сохранением существующего серверного контракта и primary fallback.

[Редактор shadow-проверки](./qualification/local-decisions/shadow/editor.md) добавляет Choice/Boolean/Score в настройки агентного шага: отдельный черновик формы, точное сохранение строки профиля, варианты и их порядок, публикация версии и primary fallback; frontend 61/61, browser 12/12, typecheck и сборки — pass. Следующий этап — неблокирующие сведения о shadow при проверке готовности опубликованного сценария; реальная доступность runtime и предметная qualification остаются независимыми gates.

[Shadow в preflight](./qualification/local-decisions/shadow/preflight.md) показывает состояние флага и совместимость текущих primary-кандидатов, включая выбранный router worker; не добавляет блокировок запуска и не меняет dispatch. Целевые 22/22, coordinator 255/255, PostgreSQL между репликами и после restart — pass. Следующая техническая проверка — истечение lease и конкурирующие изменения во время записи shadow-наблюдения; qualification остаётся открытой.

[Запись shadow при истечении lease](./qualification/local-decisions/shadow/lease-expiry.md) исправлена после воспроизведения на SQLite и PostgreSQL: время проверяется после row lock и после записи события, поздние новые данные откатываются, прежнее наблюдение сохраняется без повторного подтверждения просроченного lease. Целевые 19/19, PostgreSQL 4/4, coordinator 258/258 и typecheck — pass. Следующий gate — независимая проверка срока lease при подтверждении завершения primary; предметная qualification остаётся открытой.

[Допуск terminal-ответов primary](./qualification/local-decisions/performance/terminal-lease-admission.md) теперь проверяет свежий срок под stage lock перед записями; поздний `/fail` больше не стирает lease нового владельца. Целевые 54/54 и PostgreSQL 6/6 подтверждают отказ просроченным ответам, сохранение переназначения, допуск своевременного renewal и восстановление с артефактами. Следующий gate — конкурирующее фоновое освобождение lease несколькими coordinator.

[Конкурирующий maintenance stage lease](./qualification/local-decisions/performance/lease-maintenance-locks.md) теперь выбирает просроченные строки под `FOR UPDATE SKIP LOCKED` и атомарно сохраняет SQL-переходы с событиями. Реальные два coordinator больше не стирают renewal или успешное завершение и не дублируют истечение: PostgreSQL 3/3, целевые 38/38 — pass. Следующий gate — независимая проверка жизненного цикла embedding lease.

[Lifecycle embedding lease](./qualification/local-decisions/performance/embedding-lease-lifecycle.md) теперь проверяет срок и владельца после job lock, откатывает поздние SQL-записи complete/fail и атомарно освобождает задания. SQLite 31/31 и реальный PostgreSQL 13/13 подтверждают сохранность индекса, timely renewal, переназначение и recovery. Следующий gate — SQL-lock интервал stage renewal; размерность при конкуренции разных jobs одной коллекции остаётся отдельной проверкой.

[Продление после SQL-lock](./qualification/local-decisions/performance/lease-renewal-deadlines.md) проверяет stage owner и expiry после ожидания строки и откатывает поздний UPDATE для stage/embedding. До изменения три PostgreSQL-сценария возвращали 204 после expiry; SQLite также воспроизвёл принятие повреждённой даты. Целевые 33/33, PostgreSQL 10/10, полный coordinator 284/284 и PostgreSQL 58/58 — pass. Следующий gate — конкурирующий выбор размерности embedding-коллекции.

[Конкурирующая размерность embedding](./qualification/local-decisions/performance/embedding-dimension-concurrency.md) воспроизведена двумя coordinator: baseline сохранял [2, 3] в одной коллекции при двух ответах 200. Completion теперь блокирует коллекцию перед job и читает размерность после ожидания; несовместимый batch получает 400 без частичных записей, независимые коллекции продолжают параллельно. PostgreSQL 9/9, полный coordinator 284/284 и PostgreSQL 62/62 — pass. Следующий gate — HTTP-профиль completion/renewal и служебных запросов после изменений SQL.

[HTTP-профиль embedding и renewal](./qualification/local-decisions/performance/embedding-http.md) выполнен на закреплённом commit через два обычных coordinator: replay подтвердил 420 completion + 4 warmup и 13568 векторов; все отправленные health/renewal успешны, пропусков probes нет. При конкурентности 4 completion p50 shared/independent — 140/92 мс, health max — 199/124 мс. Следующий gate — неизвестный исход embedding COMMIT и восстановление после потери подтверждения; qualification модели и производственные SLO остаются открытыми.

[Потеря embedding COMMIT acknowledgement](./qualification/local-decisions/performance/embedding-commit-acknowledgement.md) проверяет терминальную и частичную партии, разрыв и удержанный ответ, точные векторы/события и продолжение с новым lease после restart. Существующая обработка неизвестного COMMIT сохраняет индекс; целевые PostgreSQL 6/6 и полный набор 65/65 прошли. Следующий gate — тот же отказ через настоящий Python worker с проверкой освобождения исполнения и продолжения следующих партий.

[Настоящий Python embedding worker после потерянного ответа](./qualification/local-decisions/performance/embedding-worker-recovery.md) проходит разрыв и удержанный COMMIT reply: продолжает следующие две lease, сохраняет 34 точных вектора и три события на сценарий, завершает все renewal threads без повторного complete. Полный PostgreSQL-набор — 67/67. Применяется штатный dry-run inference; качество модели не утверждается. Следующий gate — окончательный отказ renewal во время вычисления и прекращение устаревшего ответа.

[Окончательный отказ embedding renewal](./qualification/local-decisions/performance/embedding-worker-lease-cancellation.md) воспроизвёл два устаревших POST после реального 404 во время model HTTP. Worker теперь прекращает renewer и отбрасывает поздний результат/ошибку; временные отказы не отменяют lease. Восемь целевых unit-тестов и полный PostgreSQL-набор 68/68 прошли; серверные проверки владения сохраняются. Следующий gate — ограниченное чтение HTTP-ответов embedding endpoint; прерывание уже идущего inference остаётся отдельной границей.

[Ограничение model HTTP-ответов embedding](./qualification/local-decisions/performance/embedding-http-response-limits.md) закрывает неограниченное чтение успешного JSON и ненужного хвоста HTTPError: 8 МиБ / 4096 байт, корректный 32×4096 batch сохраняется. Целевые HTTP 7/7 и реальные worker/PostgreSQL 5/5 подтверждают отказ, новую аренду и единственный вектор успешной попытки. Следующий gate — полный срок model HTTP и отмена во время медленного ответа.

[Общий срок embedding HTTP](./qualification/local-decisions/performance/embedding-http-deadline.md) охватывает DNS/connect/redirect/body через disposable urllib subprocess. При deadline и окончательной потере lease helper завершается и reaped до освобождения слота, proxy/TLS сохраняются. Реальные worker/PostgreSQL — 8/8; потеря renewal освобождает слот до возврата model response. Следующий gate — измерить цену нового транспорта и ресурсы серии отмен при batch/concurrency 1/32 и 1/4.

[Профиль изолированного embedding-транспорта](./qualification/local-decisions/performance/embedding-transport-profile.md) завершил 312 loopback-запросов с batch 1/32, dimensions 768/4096 и concurrency 1/4. Независимо проверены все данные и 180 reaped helpers; 48 отмен/таймаутов не оставили FD или процессов. Прибавка p50 — 63,588–78,447 мс; это измеренная цена изоляции, без заявления ускорения или production SLO. Следующий gate — доля overhead на настоящей установленной локальной embedding-модели.

[Парное измерение на настоящей embeddinggemma](./qualification/local-decisions/performance/embedding-model-transport.md) завершило 68 вызовов на закреплённых weights и Ollama 0.34.2: все векторы совпали точно, 34 helper завершены. Добавленная p50-задержка — 98–106 мс для batch 1 и 54–151 мс для batch 32 (11,4–15,3% для последнего). Следующий gate — ограниченный прототип заранее запущенного helper с проверкой изоляции запросов и lifecycle; production defaults не меняются.

[Прототип постоянного embedding helper](./qualification/local-decisions/performance/embedding-session-prototype.md) сохраняет request ownership, deadline и reap; 16 lifecycle-проверок на macOS/Linux и независимый replay 256 loopback-вызовов. Для одиночного вектора 768 p50 уменьшилась с 70,256 до 1,003 мс, но постоянные helper требуют отдельного бюджета памяти. Обычный worker не переключён. Следующие gates — реальные model vectors/latency и длительная серия с восстановлением процессов.

[Постоянный helper на настоящей модели](./qualification/local-decisions/performance/embedding-session-model.md) завершил 132 вызова: все полные vectors совпали, 88 процессов reaped. Hot p50 одиночного запроса 105,637 → 17,603 мс; batch 32 при concurrency 4 улучшился только на 3,2%, а один batch-tail ухудшился. Зафиксированы readiness и постоянная память. Следующий gate — длительная серия работы и восстановления тех же сессий; worker defaults сохранены.

[Двухминутная серия постоянных сессий](./qualification/local-decisions/performance/embedding-session-endurance.md) прошла 480 success + 10 faults, все replacement/reap и независимый replay. Контрольный helper сохранил PID, FD 6 → 14 → 6. Отмена под нагрузкой возвращалась до 113 мс, поэтому polling interval не заявляется как hard deadline cleanup. Следующий gate — явный opt-in в worker с ограниченным pool, проверкой input budget и настоящего lease/recovery-пути.

[Session opt-in обычного worker](./qualification/local-decisions/performance/embedding-worker-session-opt-in.md) добавляет lazy pool не более concurrency, общий deadline очереди/HTTP и явный close после drain. Default isolated сохранён. Проверены реальные model HTTP-faults, renewal rejection и потерянный COMMIT acknowledgement через PostgreSQL в обоих режимах; 34 вектора и три события сохраняются без model replay, helper закрывается после drain. Host worker 96 pass + 1 skip, Linux 97/97. Следующий gate — полный RAG workload и память обычного worker.

[Полный RAG-профиль обычного worker](./qualification/local-decisions/performance/embedding-worker-rag.md) завершил 12 процессов, 36 primary-этапов и 44 embedding-запроса с одинаковыми prompts/outputs и сохранённым provenance. Все 26 helpers закрыты, FD 4 → 4. Session снизил embedding p50 примерно со 159 до 69 мс, но не показал устойчивого ускорения полной фазы; постоянные helpers занимали до 58,4 МиБ sampled RSS. Default isolated сохранён. Следующий gate — остановка worker во время активного embedding и проверка drain/lease/result.

[Остановка worker во время embedding](./qualification/local-decisions/performance/embedding-worker-drain.md) подтверждена четырьмя реальными PostgreSQL-сценариями в обоих режимах: SIGTERM закрывает admission, сохраняет активный renewal через 45 с и завершает текущий result/failure. После restart обрабатываются только pending leases, без повторной записи готового вектора; helper и renewer закрыты. Production изменений не потребовалось. Следующий gate — принудительное завершение родителя и проверка helper/lease recovery.

[Выход helper после SIGKILL worker](./qualification/local-decisions/performance/embedding-parent-exit.md) исправляет подтверждённое сохранение model HTTP без живого владельца в обоих transport. Unix helper проверяет переданный до spawn PID и самостоятельно завершает свои I/O после смены родителя; четыре исходных PostgreSQL-сценария проходят, lease восстанавливается через maintenance без stale terminal POST. Host worker 100 pass + 1 skip, собранный Linux image 101/101. Следующий gate — расходы guard и независимость соседних владельцев; default isolated сохранён.

[Расходы parent guard и соседние владельцы](./qualification/local-decisions/performance/embedding-parent-guard.md) проверены на закреплённых до/после версиях: 264 полных model-ответа совпали точно, 176 helpers закрыты, FD восстановлены. В четырёх сочетаниях transport SIGKILL одного владельца не мешает второму получить правильный ответ; Linux 102/102. Короткий ABBA не доказывает нулевой overhead: при batch 32/concurrency 4 p50 вырос на 69–94 мс. Следующий gate — постоянные CPU/память и close при 1/4/32 простаивающих session helpers; default isolated сохранён.

[Idle budget session helpers](./qualification/local-decisions/performance/embedding-idle-budget.md) измерен native counters с проверкой Mach timebase: 148 helpers и 296 повторных HTTP-запросов, все ресурсы закрыты. У 32 слотов guard потребляет 1,339–1,351% одного ядра в простое; сумма RSS около 934 MiB, footprint около 561 MiB. Следующий gate — прототип освобождения простаивающих сессий с безопасными гонками request/close и повторным прогревом; production defaults сохранены.

[Прототип idle retirement](./qualification/local-decisions/performance/embedding-idle-prototype.md) освобождает только незанятые helper, сохраняет deadline/cancellation admission и выполняет join maintenance при close. 13 real HTTP/process тестов прошли на macOS и Linux; два burst по 32 слота дали 64 точных ответа с возвратом helper count/FD между ними. Следующий gate — модельный burst → idle → burst с полными vectors и ценой повторного startup; production worker не переключён.

[Модельный burst после idle](./qualification/local-decisions/performance/embedding-idle-model.md) завершил 258 точных ответов и reap всех 62 helpers. Idle retirement освобождает 29 MiB на слот (117–120 MiB при четырёх), но первые запросы второго burst имеют наблюдаемую p50 на 54–83 мс выше; тёплые вызовы разобраны отдельно. Lifecycle 14/14 на macOS/Linux, независимый replay и 11 mutation-тестов прошли. Следующий gate — явная настройка idle timeout обычного session worker с default disabled и проверкой настоящего lease/recovery-пути.

[Idle opt-in обычного worker](./qualification/local-decisions/performance/embedding-worker-idle-opt-in.md) добавляет строго проверяемый срок хранения helper, выключенный по умолчанию. 20 целевых lifecycle/config проверок и пять настоящих PostgreSQL-сценариев подтверждают reap между lease, SIGTERM drain, отмену после renewal 404 и сохранение 34 векторов при потерянном COMMIT reply. Следующий gate — настоящий model burst через этот production opt-in; выбор производственного timeout остаётся у оператора.

[Production idle opt-in на настоящей модели](./qualification/local-decisions/performance/embedding-worker-idle-model.md) завершил 32 worker-вызова, сохранил 528 точных vectors через SQLite/coordinator и закрыл восемь worker/12 helpers. После простоя helper RSS падает до нуля; первый одиночный вызов наблюдался в диапазоне 203–231 мс вместо 42–71 мс у keep. Выборка мала, SLO не заявлен. Следующий gate — передача трёх transport/deadline/idle настроек через Compose и общий Kubernetes ConfigMap без изменения defaults.

[Настройки deployment](./qualification/local-decisions/performance/embedding-deployment-settings.md) теперь проходят из `.env` Compose и environment `k8s:up` в обычные/управляемые worker. Defaults сохранены; 50 round trips через production parser и 3 launcher tests прошли. Повторный запуск в Linux image недоступен из-за остановки Docker при ENOSPC, ограничение сохранено отдельно. Документированы перезапуск pod/container и откат. Следующий инженерный gate исходного плана — сквозной RAG через Temporal с retry/replay; бизнес-разметка и qualification не считаются выполненными.

[Сквозной RAG через Temporal](./qualification/local-decisions/performance/temporal-rag-recovery.md) проверяет оба embedding transport: реальный Activity retry после потерянного tick reply, SIGKILL/restart Temporal worker, durable timer и JSON history replay. В каждом сценарии сохранены 3 primary-ответа, 3 shadow-наблюдения и 6 источников без повторных model calls при replay. Добавлен обязательный CI job и две реальные replay fixtures. Используются учебные model responses и SQLite; qualification не заявлена. Следующий gate — потерянное подтверждение создания процесса по расписанию и идемпотентность scheduled-start.

[Идемпотентный scheduled-start](./qualification/local-decisions/performance/temporal-scheduled-start.md) устраняет подтверждённое создание двух instance при повторе Activity. Ключ Run/Activity ID, транзакционная receipt в schema 28, конфликт payload и FORCE RLS проверяются на SQLite/PostgreSQL; настоящий Temporal повторяет потерянный HTTP reply с одним instance и одним child. Следующий gate — конкуренция startup reconciliation coordinator с parent workflow за запуск того же child; предметная qualification остаётся открытой.

[Владелец scheduled child](./qualification/local-decisions/performance/temporal-scheduled-ownership.md) устраняет воспроизведённый конфликт: startup reconciliation запускал standalone workflow до parent и завершал parent ошибкой. Schema 29 сохраняет владельца запуска, backfill receipts ограничен проектом. Настоящий Temporal прошёл два SIGKILL/restart coordinator до и после child start; обычный запуск продолжает восстанавливаться. SQLite/PostgreSQL upgrade, offline import, четыре интеграционных сценария и семь replay histories прошли. Следующий gate — отмена scheduled parent/child и согласованное terminal state в application database.

[Отмена Temporal и application state](./qualification/local-decisions/performance/temporal-cancellation.md) устраняет подтверждённое расхождение после отмены scheduled parent: Temporal завершал child, а база сохраняла активное ожидание. Идемпотентная cleanup Activity синхронизирует state и дожидается компенсаций; проверены потерянный ответ, SIGKILL/restart worker, PostgreSQL concurrency/RLS и replay старых cancellation histories. Следующий gate — отмена parent во время scheduled-start, когда commit уже мог произойти, но child ещё не создан.

[Отмена scheduled-start до появления child](./qualification/local-decisions/performance/temporal-scheduled-cancellation.md) закрывает воспроизведённые orphan instances при неизвестном create reply и позднем запросе после отмены. Schema 30 сохраняет cancellation intent под ключом creation Activity; PostgreSQL сериализует его с create. Проверены потеря COMMIT acknowledgement, rollback, retention, RLS, три живые границы отмены, компенсация до child и legacy replay. Следующий gate — исчерпание обычного retry budget scheduled-start после возможного commit; qualification остаётся открытой.

[Scheduled-start при длительном outage](./qualification/local-decisions/performance/temporal-scheduled-retry.md) устраняет воспроизведённый отказ после 8 попыток с оставленным активным instance. Новая Activity повторяет transient ошибки без общего лимита, сохраняя bounds отдельного attempt, idempotency key и cancellation intent. На 13-й попытке через 135 секунд восстановлен тот же instance и завершён child; 10 живых сценариев и 20 replay histories прошли. Следующий gate — длительный outage обычного process tick и согласование child/application state; qualification остаётся открытой.

[Process tick при длительном outage](./qualification/local-decisions/performance/temporal-process-retry.md) устраняет такой же подтверждённый отказ child после 8 попыток при активном application state. Новый tick пережил 125-секундный outage и SIGKILL worker, восстановился на 13-й попытке без смены Run ID и завершил instance. Отмена во время retry согласована с application cleanup; 12 live сценариев и 28 replay histories прошли. Следующий gate — совместный RAG/Temporal/PostgreSQL runtime после штатной migration/admission; предметная qualification остаётся открытой.

[RAG/Temporal/PostgreSQL](./qualification/local-decisions/performance/temporal-postgres-rag.md) выявил и исправил отказ HTTP start: tenant preflight пытался читать глобальный release registry. Сервер получает project-bound readiness snapshot до tenant-транзакции; grants сохранены, lease повторно проверяет worker. Оба embedding transport прошли реальные retry/restart/replay, сохранив по три primary-ответа и шесть source references; чужой проект не видит run/retrieval/chunks. Добавлен обязательный CI suite. Следующий gate — отмена полного RAG при активном primary HTTP request в PostgreSQL runtime; предметная qualification остаётся открытой.

[Отмена полного RAG](./qualification/local-decisions/performance/temporal-rag-cancellation.md) подтверждена на SQLite/PostgreSQL для обоих embedding transports. После Temporal worker restart и потерянного cleanup reply процесс отменяется до выпуска второго ответа модели; worker получает отказ завершённой lease. Первый output и provenance сохранены, второй output/новый shadow не приняты, третий stage не создан. Следующий gate — закрытие самого primary HTTP после отказа lease renewal и освобождение worker slot; сохранность state не означает прекращение backend computation.

[Прерывание primary HTTP](./qualification/local-decisions/performance/primary-http-cancellation.md) устраняет воспроизведённое удержание соединения после отказа lease renewal. Управляемый helper получает отмену, завершается и собирается до освобождения слота; общий HTTP deadline и byte limits ограничивают ожидание и чтение. Тот же worker с concurrency 1 завершает новый RAG, пока прежний ответ удержан; настоящий LangGraph, параллельные leases и embeddings regression проверены. Следующий gate — cancellation query embedding при retrieval до primary; backend compute cancellation и предметная qualification остаются открытыми.

[Отмена query embedding](./qualification/local-decisions/performance/rag-query-cancellation.md) закрывает воспроизведённое ожидание до primary после отказа обычной process lease. Оба embedding transport получают cancellation event; между группами и после поиска проверяется отмена. Второй primary/shadow не запускается, первый результат и provenance сохранены; тот же worker завершает следующий RAG с новым helper. Следующий gate — прерывание HTTP ожидания coordinator knowledge search; предметная qualification остаётся открытой.

[Отмена coordinator search HTTP](./qualification/local-decisions/performance/knowledge-http-cancellation.md) устраняет воспроизведённое ожидание ответа после cancellation и отказа renewal. Управляемый helper сохраняет auth, trace и HTTP errors, ограничивает deadline и тело, освобождает слот без нового primary/shadow. Уже записанный retrieval provenance сохранён при потерянном ответе. Следующий gate — SIGKILL Python worker во время primary HTTP и восстановление после истечения lease; предметная qualification остаётся открытой.

[Восстановление после SIGKILL Python worker](./qualification/local-decisions/performance/python-worker-recovery.md) проверяет самостоятельное завершение owned HTTP helpers, настоящий lease expiry и повтор только незавершённого stage. Первый принятый результат сохраняется, старый completion отвергается, обе попытки retrieval остаются проверяемыми. Прошли 22 SQLite Temporal и 12 PostgreSQL RAG сценариев; следующий gate — потерянный acknowledgement уже принятого completion.

[Потеря acknowledgement completion](./qualification/local-decisions/performance/completion-ack-recovery.md) проверяет SIGKILL Python worker после приёма второго output/shadow coordinator. Два принятых stage/shadow сохранены без повторного model call. Прошли 4 SQLite crash-сценария, полная матрица 14 PostgreSQL RAG сценариев и 293 coordinator tests. Следующий gate — потеря самого coordinator после commit completion.

[Restart coordinator после completion](./qualification/local-decisions/performance/coordinator-completion-recovery.md) проверяет сохранность принятого output/shadow/provenance после SIGKILL coordinator при живом Python worker. Оба transport прошли настоящий tick outage, Update и чтение trace новым coordinator без смены Temporal Run ID. Проверены 6 SQLite и 6 PostgreSQL crash-сценариев, 293 coordinator tests. Следующий этап — повтор реальных локальных RAG-измерений после HTTP isolation.

[Явный профиль transport для повторных RAG-измерений](./qualification/local-decisions/performance/rag-transport-profile.md) закрепляет CLI mode в обоих планах и передаёт его worker независимо от inherited environment. SHA новых HTTP helpers включены в frozen sources; verifier не приписывает старым опытам текущий default. Прошли 5 workflow и 5 профильных проверок, 293 coordinator tests, 12 Node + 323 Python documentation tests. Следующий шаг — реальный model run с этим профилем.

[Повтор реального RAG после HTTP isolation](./qualification/local-decisions/performance/rag-http-isolation.md) завершил 12 workflows / 36 primary / 44 embedding items / 18 accepted shadow calls с прежним model profile и одинаковыми наборами prompts/outputs. Арифметика всех 12 повторных итогов верна; модели выгружены, 71 наблюдавшийся собственный PID отсутствует. Workload 164,092 с не даёт причинной оценки overhead относительно прежних 112,036 с. Первый startup timeout на dataless-файлах сохранён; окружение и pinned weights восстановлены вне Documents без смены версий. Следующий gate — времена запросов и sampled RSS всех трёх типов HTTP helpers.

[Все HTTP helpers в реальном RAG](./qualification/local-decisions/performance/http-helper-observability.md) наблюдает 116 запросов и 98 закрытых/reaped процессов: embedding, primary и knowledge. Четыре ABBA-блока сохранили все 36 ответов и одинаковые векторы; независимый replay и 31 mutation/replay test прошли. Session уменьшает число embedding helpers с 11 до 2 на блок, с дополнительными примерно 44 МиБ sampled RSS; причинное ускорение всего workflow не заявляется. Следующий шаг — ранее незавершённая Linux deployment-проверка после Docker ENOSPC.

[Linux deployment после ENOSPC](./qualification/local-decisions/performance/linux-worker-deployment.md) закрывает прежнее ограничение среды: 50/50 round trips и 143/143 worker tests прошли на закреплённом image. Настоящий Python PID 1 в isolated/session/idle режимах дождался активного HTTP после SIGTERM и сохранил обе lease, exit 0; временные Docker resources удалены. Следующий шаг — совместный workload реальных Qwen3/embeddinggemma, Temporal и PostgreSQL; qualification не заявляется.

[Реальные модели с Temporal/PostgreSQL](./qualification/local-decisions/performance/temporal-real-rag.md) завершили оба embedding transport: 6 primary calls и 10 embedding items, одинаковые prompts/outputs/vectors. Потерянный tick reply и SIGKILL Temporal worker сохранили Run ID, принятый stage и все citations; RLS изоляция и два независимых native replay прошли. Все четыре контейнера удалены, модели выгружены. Сохранён первый отказ из-за общей тестовой БД, затем весь опыт повторён с отдельными БД. Следующий gate — настоящий локальный shadow decider в том же сценарии; предметная qualification остаётся открытой.

[Реальный shadow в Temporal/PostgreSQL](./qualification/local-decisions/performance/temporal-real-shadow.md) прошёл оба transport с явно прогретым pinned decider: 6 primary, 10 embedding items и 6 accepted shadow observations. Snapshot первого наблюдения, primary fallback и все входы/ответы сохранены через restart; два native replay и 22 verifier tests прошли. Первый запуск без отдельного warmup завершился backend-unavailable и сохранён как отказ; точный typed reason тогда не записывался, наблюдаемость исправлена. Следующий gate — реальный отказ/restart decider и fallback полного процесса; cold-start причина и предметная qualification открыты.

[Реальный отказ/restart shadow runtime](./qualification/local-decisions/performance/temporal-shadow-runtime-recovery.md) прошёл оба transport: SIGKILL decider сохраняет второй primary с unavailable fallback, restart восстанавливает третий shadow; четыре inference, два unavailable и три отдельных warmup явно разделены. Все primary outputs/prompts/vectors совпали с baseline, snapshots и native replay сохранены; 42 verifier tests прошли. Первый внеплановый exit 75 сохранён как отказ; добавлен немедленный журнал typed responses. Следующий gate — повторяемая диагностика доступности decider при совместно загруженных моделях; причина внепланового отказа и предметная qualification открыты.

[Локальная диагностика ресурсов shadow](./qualification/local-decisions/performance/shadow-runtime-observability.md) добавляет opt-in sampling собственных процессов и системных counters с отдельной проверкой units, scope, cleanup и native CPU timebase. Resource artifacts ограничены игнорируемым `docs/private/`; публичные тесты используют явно синтетические counters. Исходные измерения сохранены локально. Причина прежнего exit 75 не установлена; следующий шаг — content-free причина backend retirement в service log с контролируемыми timeout/cancellation/child-death тестами.

[Диагностика exit 75](./qualification/local-decisions/performance/decision-exit-diagnostics.md) в runtime `0.12.1` сохраняет ограниченное retirement-событие после drain HTTP и cleanup backend. Проверены реальные CLI/процессы и четыре MLX-runtime: смерть ребёнка в простое, одинаковое решение после restart, полный timeout/cancellation response и три корректные причины завершения; все собственные процессы остановлены. Новый профиль не наследует старую qualification. Следующий gate — полный Temporal/RAG с явно закреплённым новым профилем.

[Полный Temporal/RAG с runtime 0.12.1](./qualification/local-decisions/performance/temporal-runtime-0.12.1.md) прошёл оба transport: 6 primary calls, 10 embedding items, 4 shadow inference и 2 unavailable fallback после контролируемого SIGKILL; restart, RLS, snapshots и два независимых native replay — pass. Явный экспорт профиля закреплён в Git, verifier пересчитывает runtime fingerprint из измеренного commit; старый профиль остаётся историческим. Сохранены первые startup/embedding timeout без заявления об установленной причине; сырые журналы и ресурсные samples остаются в `docs/private`. Следующий этап — гарантированная остановка собственных runtime при ошибке получения process inventory; предметная qualification и SLO остаются открытыми.

[Cleanup при отказе inventory](./qualification/local-decisions/performance/shadow-cleanup-inventory.md) исправлен после трёх воспроизведённых сценариев: ошибка `ps` больше не пропускает остановку собственного runtime, оставшиеся roots закрываются после ошибки stop, живой процесс допускает повтор cleanup. Контроллер сохраняет признак отказа; внешний launcher не выдаёт неизвестный inventory за отсутствие процессов. 62 целевые проверки прошли, включая реальные process trees и исторические evidence. Следующий gate — cleanup всех собственных Docker-контейнеров при отказе listing или отдельного удаления.

[Cleanup после ошибок Docker](./qualification/local-decisions/performance/container-cleanup-failures.md) исправлен после трёх воспроизведённых отказов. Launcher удаляет ранее известные собственные контейнеры при недоступном listing, продолжает после ошибки отдельного удаления и всегда пытается проверить итог. Live fault injection с девятью временными контейнерами подтвердил изоляцию контрольного контейнера и сохранение ошибок после retry; 71 целевая проверка прошла. Следующий gate — срок жизни временного PostgreSQL storage после удаления контейнера.

[Временный PostgreSQL storage](./qualification/local-decisions/performance/temporary-postgres-volumes.md) больше не остаётся после удаления собственного тестового контейнера: Python fallback и оба shell EXIT traps используют удаление анонимных volumes. Утечка воспроизведена на закреплённом PostgreSQL image и в трёх live regression-сценариях; после исправления 74 целевые проверки прошли, именованные данные и marker сохранены. Следующий gate — полный повтор реального Temporal/RAG с неизменным профилем runtime 0.12.1 после cleanup-исправлений.

[Сохранение приватных process logs](./qualification/local-decisions/performance/private-process-logs.md) исправлено после ENOSPC в полном повторе: позднее копирование из временного каталога потеряло process logs и не позволило записать итоговый report. Теперь приватные файлы открываются до запуска моделей; шесть fixture tests проверяют ENOSPC, SHA/permissions, отсутствие перезаписи и process admission без журналов. Неуспешный реальный запуск сохранён как отказ; повтор gate ожидает доступного Docker и дискового пространства.

[Локальная доступность весов до admission](./qualification/local-decisions/performance/model-residency.md), 03.10: обнаруженное ожидание cloud-only checkpoint при подготовке полного повтора устранено явным отказом по macOS `SF_DATALESS` до чтения весов и запуска процессов. Шесть новых regression tests и вся cleanup/recovery матрица с live Docker — 86/86 pass; resident symlink cache, compressed файлы и платформы без flags поддерживаются. Serving-профиль 0.12.1 не меняется. Следующий gate — прежний полный real-model повтор после cleanup-исправлений; предметная qualification и SLO открыты.

[Полный повтор после cleanup-исправлений](./qualification/local-decisions/performance/cleanup-repeat.md), 03.10: оба embedding transport прошли Temporal/PostgreSQL с runtime 0.12.1, реальными Qwen3/embeddings/decider, SIGKILL/restart и fallback. Независимый verifier, два replay после cleanup и сравнение прежнего baseline подтвердили 6 primary calls, 10 embedding items, 4 shadow inference, 2 unavailable fallback и все prompts/outputs/vectors. Четыре собственных контейнера удалены, PID/cleanup errors нет; raw artifacts остаются приватными. Следующий этап — многочасовая непрерывная нагрузка pinned runtime; предметная qualification и SLO открыты.

[Инструмент непрерывной нагрузки](./qualification/local-decisions/performance/continuous-soak.md), 03.10: один owned runtime во всех ограниченных блоках, общий контроль PID/profile/ответов, немедленный приватный journal, сохранение checkpoint и кооперативная отмена; полный план ограничен двумя часами. Offline verifier пересчитывает committed source binding, summaries, длительность, journal и cleanup. 23 regression tests, реальный двухблочный smoke (209 measured calls) и SIGTERM с сохранением частичного блока прошли; отменённый прогон verifier отвергает. Serving-профиль 0.12.1 не меняется. Следующий gate — полный закреплённый двухчасовой прогон; предметная qualification и SLO открыты.

[Неуспешный длинный прогон](./qualification/local-decisions/performance/continuous-soak-failure.md), 03.10: 10 030 решений и один busy после 38,57 минуты; два полных блока и третий частичный сохранены, runtime/profile/решения стабильны, cleanup завершён. Полный verifier отвергает неполный план. Детерминированно воспроизведена гонка: первый body прочитан, inference завершён, но следующий запрос получает busy до освобождения lock после response write. Добавлен source-bound публичный summary exporter с pooled percentiles; 466 Python/12 Node checks прошли. Следующий gate — освобождение inference admission до записи ответа, новый serving профиль и повтор полного плана; прежний exit 75 при совместной работе моделей и предметная qualification не объявляются объяснёнными.

[Освобождение HTTP admission в 0.12.2](./qualification/local-decisions/performance/http-admission-release.md), 03.10: inference lock освобождается до response write после завершения вычисления и request-scoped cancellation watcher. Три regression tests воспроизводили busy до fix и прошли после; настоящий конкурентный inference по-прежнему получает busy. Экспортирован новый pinned профиль; 35 целевых и полный runtime набор (125 tests, три opt-in skips) прошли. Короткий реальный smoke — 209 measured calls, три warmup, два блока по 20 секунд; независимый verifier и cleanup прошли. Merge этого этапа завершает работу по указанию пользователя. Следующий gate — полный 7200-second повтор на 0.12.2, затем Temporal/RAG; предметная qualification и SLO открыты.

[Полный двухчасовой soak runtime 0.12.2](./qualification/local-decisions/performance/continuous-soak-0.12.2.md), 04.10: один runtime без restart/retry завершил восемь блоков, 25 433 measured calls за 7201,446 с и три отдельных warmup. Ошибок и изменений решений нет; pooled p95 — 410,966 мс. Независимый source-bound verifier сверил все блоки и observation journal; профиль и process identity стабильны, все три собственных PID остановлены. Публичная сводка сохраняет counts/fingerprints, raw evidence остаётся приватным. Следующий gate — полный Temporal/PostgreSQL/RAG с тем же профилем 0.12.2 и независимым native replay; исходный внеплановый exit 75 под совместной модельной нагрузкой, предметная qualification и производственные SLO остаются открытыми.

[Полный Temporal/PostgreSQL/RAG на runtime 0.12.2](./qualification/local-decisions/performance/temporal-runtime-0.12.2.md), 04.10: оба embedding transport прошли с тем же профилем, что двухчасовой soak. Проверены 6 primary calls, 10 embedding items, 4 shadow inference, 2 unavailable fallback, 3 warmup и 2 restart; RLS, snapshots, 12 source citations, независимый verifier и два replay после cleanup прошли. Prompts/outputs/token counts и пять векторов совпали с прежним baseline. Все 90 наблюдавшихся PID остановлены, четыре собственных контейнера удалены; raw resource/process evidence остаётся приватным. Следующий этап — native launchd restart с точным профилем и сохранённой диагностикой, затем реальное подключение наблюдения. Прежний внеплановый exit 75, предметная qualification и производственные SLO остаются открытыми.

[Нативное восстановление launchd на 0.12.2](./qualification/local-decisions/shadow/native-launchd-0.12.2.md), 04.10: временный GUI LaunchAgent на реальных весах вышел 75 после SIGKILL inference ребёнка, автоматически восстановился за 29,883 с и сохранил точный профиль/распределение. Plist, retirement event, source bindings и числовые результаты перепроверены; три временных jobs и все 15 наблюдавшихся PID отсутствуют. Два failed reports сохранены; harness исправлен для символических exit codes и асинхронного удаления регистрации, добавлены 17 fixture tests. Следующий этап — настоящий native scraper и проверка наблюдения при recovery. Постоянная установка, исходный внеплановый exit 75, предметная qualification и SLO остаются открытыми.

[Настоящий native Prometheus scrape/recovery](./qualification/local-decisions/shadow/observability/native-prometheus.md), 04.10: pinned Darwin/arm64 LTS 3.13.4 наблюдал runtime 0.12.2 через loopback. Четыре snapshots по 46 рядов подтвердили counters 0 → 1 → 0 → 1, новый server start time, target down, up 1 → 0 → 1 и pending/cleared alert. SHA архива/бинарников, восемь committed sources, raw API-ответы, model results и cleanup перепроверены; temporary job и все семь PID отсутствуют. 34 целевых и полный набор 497 Python/12 Node checks прошли. Следующий этап — постоянное наблюдение из стабильного resident deployment; владелец реакций, SLO, исходный внеплановый exit 75 и предметная qualification остаются открытыми.

[Resident deployment package](./qualification/local-decisions/shadow/observability/resident-deployment.md), 04.10: новый bundle в Application Support закрепил 27 source, 36 copied и 9 generated files, fresh offline venv с 34 pinned wheels/dependencies, resident модель и Prometheus 3.13.4. Реальный launchd probe загрузил package вне Documents/TMP, восстановился за 20,427 с и сохранил профиль 0.12.2, оба решения и token counts прежнего baseline. Scrape/reset/up/pending alert и все artifact SHA перепроверены; временный job и семь PID отсутствуют. 30 целевых и полный набор 506 Python/12 Node checks прошли. Следующий этап после CI — постоянная регистрация собственных служб и реальное наблюдение через подготовленный конфиг. Boot/login, владелец реакций, SLO, исходный внеплановый exit 75 и предметная qualification остаются открытыми.

[Управление resident службами](./qualification/local-decisions/shadow/observability/resident-deployment.md), 04.10: committed manager связывает check/install/status/stop с точным bundle, package verification и profile 0.12.2; проверяет фактический plist path, свежий scrape и прирост computed counter. Прерванный bootstrap и остановка при отказавшем HTTP сохраняют ownership/PID inventory; чужие файлы/jobs не изменяются. 21 новый fixture и 51 целевой test прошли, реальный read-only preflight подтвердил свободные labels/plists/ports и прежние SHA. Постоянные jobs не устанавливались. Следующий этап после CI — фактические install/status/stop/reinstall и локальное наблюдение; затем совместная модельная нагрузка для анализа прежнего внепланового exit 75. Предметная qualification и SLO остаются открытыми.

[Повторный preflight после stop](./qualification/local-decisions/shadow/observability/resident-port-reuse.md), 05.10 MSK: первый реальный install/status с профилем 0.12.2, свежим scrape и диагностическим решением прошёл; stop удалил оба jobs/plists и завершил четыре PID. Повторный preflight сохранил отказ `Address already in use`. Настоящий TCP regression воспроизвёл механизм TIME_WAIT и подтвердил исправление SO_REUSEADDR при сохранении запрета live listener. 52 целевых и полный набор 528 Python/12 Node checks прошли; новый source-bound preflight и независимый verifier проверили package и отсутствие jobs. Исходный full rollout сохранил failed report, jobs после него отсутствовали. Следующий этап после CI — полный повтор install/status/stop/reinstall, затем совместная модельная нагрузка; предметная qualification и SLO открыты.

[Постоянный resident rollout](./qualification/local-decisions/shadow/observability/resident-rollout.md), 05.10 MSK: полный семишаговый повтор check/install/status/stop/check/install/status прошёл на merged main. Два диагностических решения и token counts совпали с native baseline, оба новых scrape подтверждены counter 0 → 1. Первая PID set из четырёх процессов завершилась; два собственных LaunchAgents и четыре новых PID работают. Прежний failed report и marker сохранены, новый stopped marker архивирован; independent verifier сверил source/package/model/profile, raw API, job paths, permissions и обе PID sets. Следующий этап — bounded Qwen/decider нагрузка с supervisor/retirement/metrics evidence. Boot/login, owner/SLO и предметная qualification остаются открытыми.

[Resident совместная нагрузка 0.12.2](./qualification/local-decisions/performance/resident-shared-load-0.12.2.md), 05.10 MSK: два полных прогона на merged main выполнили 480 measured calls и восемь warmup, 60 overlapping HTTP pairs; ошибок и изменений решений нет. Independent verifier пересчитал все строки, summaries, signatures, 95 process/metric samples, raw counter 1 → 245 и отсутствие retirement events. Parent/inference child/Prometheus PID стабильны; четыре временных PID остановлены, два постоянных jobs работают. Decider max wall times — 1870,782 / 2040,562 мс; p95 не скрывает эти пики. Следующий gate — длительный bounded совместный soak с сохранением первого отказа. Причина прежнего exit 75, independent business data/reviews, calibration/holdout и owner/SLO остаются открытыми.

[Tooling длительного совместного soak](./qualification/local-decisions/performance/shared-soak.md), 05.10 MSK: controller/CLI сохраняют каждый completed pair и block checkpoint, проверяют committed source/input и profile до inference, прекращают нагрузку после первого отказа. Offline verifier независимо пересчитывает duration без warmup, фазовые prefixes, chronology/overlap, counts/distributions и journal. 42 целевых regression/mutation tests и полный набор 564 Python / 12 Node checks прошли. Настоящий 180-секундный smoke завершил 295 measured calls, 30 overlapping pairs и counter 245 → 399 без ошибок; 86 native samples подтвердили стабильные resident процессы, все три наблюдавшихся временных PID завершены. Последний partial time-budget block сохранён и проверен. Полный совместный 7200-секундный gate не запускался; по указанию пользователя продолжение остановлено после этапа tooling/smoke. Qualification, причина исторического exit 75 и owner/SLO остаются открытыми.

[Первая полная совместная попытка и диагностика warmup timeout](./qualification/local-decisions/performance/shared-soak-warmup-failure.md), 05.10 MSK: после возобновления работы сохранён исходный план 7200 секунд / 128 блоков; первая primary warmup завершилась, decider вернул `inference_timeout` до measured нагрузки. Независимая проверка подтвердила zero measured calls/time, journal/source bindings и отказ полного verifier. Поздний retirement event связал текущий exit 75 с timeout, launchd восстановил точный профиль 0.12.2. Контроллер больше не подменяет первый request fault последующей недоступностью health; шесть сценариев воспроизведены до fix и прошли после, целевой набор — 44/44. Следующий gate — новый полный закреплённый повтор после recovery с отдельным системным наблюдением; причина исторического exit 75, предметная qualification и owner/SLO остаются открытыми.

[Согласованная граница shared cases](./qualification/local-decisions/performance/shared-soak-case-cap.md), 05.10 MSK: объявленные 30 cases / два rounds ранее отклонялись paired probe из-за старого cap 240 calls. Дефект воспроизведён на 16 и 30 cases; общие constants теперь допускают конечный максимум 480 при прежней concurrency и time budget. Full controller fixture и независимый verifier подтвердили 480 measured / четыре warmup calls / 60 overlapping pairs; 31 cases отклоняются до модели. Целевой набор — 47/47. Новые inputs являются unit fixtures; serving profile, прежние реальные 15-case evidence и предметная qualification не меняются.

[Воспроизводимая shared-soak сводка](./qualification/local-decisions/performance/shared-soak-summary.md), 05.10 MSK: новый CLI повторяет offline verifier перед public allowlist export, закрепляет analysis commit/SHA и считает pooled quantiles по исходным measured rows, отдельно от warmup. Шесть новых tests и полный целевой набор 53/53 прошли. Существующий реальный smoke независимо экспортирован повторно: 180 844,854 measured milliseconds, 295 calls, восемь warmup и 30 overlapping pairs; новых model calls нет. Это проверка анализа и прежнего короткого опыта; полный 7200-секундный gate, предметная qualification и owner/SLO этим этапом не закрываются.

[Полный resident shared-soak повтор 0.12.2](./qualification/local-decisions/performance/resident-shared-soak-failure-0.12.2.md), 05.10 MSK: исходный gate 7200 секунд failed после 3 773 806,379 measured milliseconds. 34 блока прошли, в блоке 35 decider вернул timeout; всего 8176 attempts, 8175 успешных, 140 успешных отдельных warmup и 1020 overlapping pairs. Два независимых audits перепроверили исходы, фазы/порядок, journal, sources, duration, retirement 75/-15 и recovery с тем же профилем; успешный префикс не принят за полный gate. Системные memory observations не доказывают причину. Следующий инженерный этап — opt-in per-process wired-memory budget с отдельным serving profile, guard tests, коротким native опытом и затем новым полным совместным gate при прежнем deadline; предметная qualification и owner/SLO остаются открытыми.

[Wired-memory budget runtime 0.12.3](./qualification/local-decisions/performance/wired-memory-budget.md), 05.10 MSK: новый explicit CLI/LaunchAgent/probe opt-in проверяет OS/device capacity до загрузки модели и сохраняет configured bytes в serving identity; deadline не меняется. Native pilot экспортировал default и wired 4096 MiB profiles и выполнил 30/30 решений с точными baseline signatures/tokens; p50/p95/max HTTP wall — 174,480 / 269,040 / 270,083 мс, первый запрос включён. Независимый audit подтвердил исходы, источники и возврат resident 0.12.2 при прежнем Prometheus PID, девять остановленных PID отсутствуют. Следующий этап — новый resident recipe, короткий paired gate и затем полный 7200-секундный shared soak; этот опыт не доказывает устранение timeout или предметную qualification.

[Selected resident recipe 0.12.3](./qualification/local-decisions/performance/resident-profile-selection.md), 05.10 MSK: builder/manager закрепляют canonical public profile source, fingerprint и matching wired argv, сохраняя управление legacy 0.12.2. Шесть новых fixture tests, 61 целевой native test и полный набор 584 Python / 12 Node checks прошли. Новый отдельный wired-4096 bundle подготовлен из committed sources с 34 exact offline dependencies; независимый verifier сверил все bytes/pins/recipes, прежние jobs сохранили PID и свежий scrape. Новый package пока не запускался и не зарегистрирован. Следующий gate — native launch/fault/recovery с настоящим scraper и cleanup, затем rollout после CI и совместные Qwen/decider gates; qualification и owner/SLO остаются открытыми.

[Native gate wired resident package 0.12.3](./qualification/local-decisions/performance/resident-wired-package-0.12.3.md), 05.10 MSK: новый venv/package запущен дважды на временном собственном label. Два решения/logits/tokens совпали с baseline; SIGKILL собственного inference child дал exit 75 за 427,302 мс и новый ready за 23719,925 мс. Настоящий scraper подтвердил counters reset, up/down/recovery и alert cycle; два audits перепроверили raw evidence и отсутствие семи временных PID. Старый resident 0.12.2 восстановлен с прежним Prometheus PID. Новый release не зарегистрирован; следующий gate — совместная Qwen/decider нагрузка, rollout после CI и полный shared soak, без переноса qualification или SLO.

[Совместный wired package опыт 0.12.3](./qualification/local-decisions/performance/wired-package-shared-load-0.12.3.md), 05.10 MSK: corrected collector завершил два повтора, 480/480 measured calls и 60 фактически overlapping pairs. Независимый audit сверил 364 journal records, signatures/tokens прежнего baseline, pooled timings, 93 observations, raw counter 0 → 244 и отсутствие шести временных PID/job. Старый resident восстановлен с тем же Prometheus PID. Первоначальная попытка вычислила load, но failed из-за неправильного вызова cleanup helper; её protocol сохранён без изменений, cleanup отдельно подтверждён, весь опыт повторён. Primary API memory observation отличается от исторического запуска при том же digest/context; причинность не заявляется. Следующий этап — полный 7200-секундный shared gate; rollout ждёт CI, qualification и owner/SLO открыты.

[Timeout collector полного wired soak](./qualification/local-decisions/performance/wired-soak-collector-timeout-0.12.3.md), 06.10 MSK: исходный 7200-секундный gate failed после 291 894,189 measured milliseconds из-за двухсекундного timeout metadata ps. Все 656 measured и 12 warmup calls вычислены; 88 overlapping pairs и 484 exact journal records independently audited. Full verifier отвергает исходный launcher; successful prefix не квалифицирован. Все шесть временных PID/job отсутствуют, старый resident 0.12.2 восстановлен с прежним Prometheus PID. Immutable evidence и measured source сохранены в проверенном private архиве. Inspection helper допускает отдельный bounded explicit timeout при прежнем default 2 с, ownership/role guards и fail-closed errors; deadline inference 5000 мс не изменён. Следующий шаг — новый полный gate с закреплённым inspection budget, без переноса qualification или permanent rollout.

[Wired profile в Temporal/RAG launcher](./qualification/local-decisions/performance/temporal-wired-profile.md), 06.10 MSK: explicit --shadow-wired-limit-mib теперь требует matching committed public profile, сохраняет typed budget в plan и передаёт тот же argv initial/recovery. Config/profile/plan mismatch отклоняется до Popen; default/zero различаются, deadline 5000 мс не изменён. Archived-source verifier независимо восстанавливает optional wired identity и отвергает rehashed corruption, private reference и попытку изменить historical evidence. Девять regression scenarios, включая scalar aliases false/0/0.0, воспроизведены до соответствующих fixes; 16 profile и полный целевой набор 58 checks прошли. Следующий gate — новый реальный Temporal/PostgreSQL/RAG с wired profile, fallback/recovery и native replay после окончания отдельного full shared soak; qualification и permanent rollout этим tooling этапом не заявляются.

[Управляемая остановка Temporal launcher](./qualification/local-decisions/performance/temporal-launcher-cancellation.md), 06.10 MSK: подтверждённые SIGTERM/SIGINT exits без failed report исправлены cooperative checkpoints после регистрации owned handles. Четыре настоящих OS-сценария и check восстановления handlers прошли; повторный сигнал не прерывает teardown и сохраняет первую причину. Целевой Temporal набор — 100 tests с четырьмя explicit-Docker skips. GPU/profile, inference deadline и Workflow Commands не меняются; полный wired shared gate продолжает исходный frozen опыт в отдельном worktree. Следующий этап — реальный wired Temporal/PostgreSQL/RAG с fallback, recovery и independent native replay; qualification и owner/SLO открыты.

[Полный wired shared soak 0.12.3](./qualification/local-decisions/performance/wired-shared-soak-7200-0.12.3.md), 06.10 MSK: новый исходный gate 7200 секунд / 128 blocks завершён с `duration_complete` и 7 226 881,737 measured milliseconds. Сохранены 67 полных и два time-budget blocks, 16 353 measured calls и 276 warmup, все computed без request failures. Полный offline verifier и отдельный native audit перепроверили committed source/profile/dataset, все rows и 12 405 journal records, 2040 overlapping pairs, counter 0 → 8304, 3321 native observations и baseline signatures/tokens. Все шесть временных PID/job отсутствуют; старый resident 0.12.2 восстановлен с точным profile/fresh scrape и прежним Prometheus PID. Raw evidence, audits и полный measured Git source сохранены в private архиве с проверкой CRC и каждого file SHA/size; public summaries закрепляют analysis commit/SHA. Новый 0.12.3 не зарегистрирован; этот опыт не доказывает причину прошлых timeouts, correctness на независимых задачах или production SLO. **По указанию пользователя текущий этап закрыт и продолжение остановлено:** реальный wired Temporal/PostgreSQL/RAG с fallback/recovery/native replay, permanent rollout, boot/login, owner/SLO, независимая разметка/calibration/holdout остаются открытыми; routing выключен, `not_assessed`.

[Реальный wired Temporal/PostgreSQL/RAG 0.12.3](./qualification/local-decisions/performance/temporal-wired-runtime-0.12.3.md), 06.10 MSK: после возобновления работы оба embedding transport прошли с точным профилем полного shared soak, 4096 МиБ wired и прежним deadline 5000 мс. Выполнены 6 primary calls, 10 embedding items, 4 shadow inference, 2 unavailable fallback, 3 warmup и 2 recovery. Source-bound verifier, отдельный native replay двух histories и независимый baseline/model/binary/cleanup audit прошли; 206 sources, 12 citations и 79 resource samples проверены. Все 81 наблюдавшийся собственный PID и четыре контейнера отсутствуют, прежний resident 0.12.2 сохранил PID/profile/fresh scrape. Следующий этап — permanent rollout проверенного wired package 0.12.3; boot/login, human reviews, calibration/holdout и owner/SLO остаются открытыми, routing выключен, `not_assessed`.

[Постоянный wired rollout 0.12.3](./qualification/local-decisions/shadow/observability/resident-wired-rollout-0.12.3.md), 07.10 MSK: после merge PR #135 и 15 успешных CI checks прежний resident остановлен с сохранением проверенного rollback package. Девять команд old status/stop и нового check/install/status/stop/reinstall завершились; два native diagnostic results и tokens точно совпали с wired baseline, counters каждого процесса 0 → 1, scrape после начала install. Отдельный audit перепроверил seals/file SHA, 34 committed inputs, 41 различный bundle file, model bytes, marker archives и ownership: все 16 checks прошли, восемь прежних PID отсутствуют, два jobs и четыре новых PID работают. Raw evidence и measured Git source сохранены в проверенном private архиве. Runtime/management code не менялись; постоянный 0.12.3 использует wired 4096 МиБ и deadline 5000 мс, старый 0.12.2 сохранён. Следующие gates — boot/login, bounded crash-loop, owner/SLO и независимые human reviews/calibration/holdout; routing выключен, `not_assessed`.

[Readonly приёмка boot/login](./qualification/local-decisions/shadow/observability/resident-boot-login.md), 07.10 MSK: отдельная CLI сохраняет baseline file SHA и проверяет фактическую смену boot UUID / GUI audit session обоих owned jobs, process birth times и свежий scrape при прежних package/model/profile/registration/plists. Python 3.13.12 arm64 и полный dependency set перепроверяются; источник measurement должен быть committed. Прежняя сессия или обычный reinstall дают `awaiting_event` / exit 2; SID/PID reuse после reboot допускается по новому boot и birth times. 25 целевых fixtures прошли, включая rehashed baseline corruption, numeric aliases, foreign host/UID, изменённые bindings и environment. Полный docs:check — 628 Python / 12 Node checks, четыре explicit Docker opt-in skips; настоящий capture на committed источнике сохранил 35 source hashes, 34 dependency pins и четыре PID. Немедленный verify вернул `awaiting_event` / exit 2; отдельный native audit подтвердил прежний boot/GUI/PID set, registration и counters computed 1 / rejected 0 / failed 0. Baseline и его SHA сохранены в постоянном workspace, raw evidence/source — в проверенном private архиве. Фактический boot/login не подменяется fixtures или прежней сессией; bounded crash-loop, owner/SLO и human reviews/calibration/holdout остаются открытыми.

[Bounded crash-loop wired gate](./qualification/local-decisions/shadow/observability/resident-crash-loop.md), 07.10 MSK: отдельный временный job использует действующий sealed resident package с прежними Python/model/profile/policy/deadline/wired limit. Gate ограничен тремя idle child SIGKILL, четырьмя поколениями и 30-секундным stable window; принимает только generation-bound exit 75, native birth spacing, raw counters/reset/down/alerts и полное owned cleanup при неизменном постоянном resident. 27 целевых fixtures прошли; сырой native process birth сохраняется для отдельного audit. Первый full docs check выявил non-atomic readiness в существующем Temporal signal fixture; atomic publication исправлена, пять OS signal checks прошли, все три orphan fixture PID очищены. После committed checkpoint полный docs check — 655 Python / 12 Node checks; настоящий native gate прошёл: три отказа, четыре старта с интервалами 30/30/30 секунд, четыре точных прежних diagnostic results/tokens и stable window 30,056415 с / 16 observations. Отдельный audit подтвердил все 12 checks, raw state/birth/metrics/alerts/logs, 37 sources и cleanup всех 13 временных PID/job; постоянный resident сохранил четыре PID, bindings, counters и свежий scrape. Boot/login baseline применим, raw/source сохранены в проверенном private архиве. Boot/login, owner/SLO и human reviews/calibration/holdout открыты, routing выключен, `not_assessed`.

[Однократный login collector](./qualification/local-decisions/shadow/observability/resident-login-observer.md), 07.10 MSK: frozen Git snapshot исходного baseline установлен в отдельном Application Support package, независимо от временного repository. Три committed builder sources, 35 исходных measurement files, baseline pin, local Git metadata/worktree и private modes проверены. Отдельный `RunAtLoad=true`, `KeepAlive=false` job записывает boot/login receipts и завершается без inference или изменения resident jobs. 26 целевых collector tests и 13 session regression tests прошли; финальный полный docs check — 682 Python / 12 Node checks. Первый native Background install дал Git inspection timeout; guarded rollback прошёл, immutable failure сохранён, source-bound Standard package подготовлен отдельно. Девять native steps, два installs и stop/reinstall прошли, шесть raw event receipts остаются `awaiting_event` / exit 2. Первый независимый audit ошибочно требовал равенство calendar boottime; Apple XNU source подтверждает его корректировку, corrected audit прошёл все 13 checks. Постоянный resident сохранил четыре PID, bindings/dependencies, fresh scrape и counters 1/0/0 без новых inference. Оба packages, raw/failed evidence, финальные checks и оба measured Git sources сохранены в проверенном private архиве и исходном workspace. Observer зарегистрирован и idle; фактический boot/login, owner/SLO и human reviews/calibration/holdout остаются открытыми.

[Caller timing и SLI](./qualification/local-decisions/shadow/observability/caller-sli.md), 07.10 MSK: negotiated monotonic local HTTP timing сохраняется отдельно от scoring; trace включает input/profile pins и caller deadline. Native fixture прошёл 10 client steps, 12 persisted traces и четыре CLI measurements с expected exit 0/2/2/1, independent audit — 9 checks. Native reopen выявил прежний пустой process trace ID; regression failed до fix, затем rollback exporter regression также failed до fix; финальные 22 targeted и весь coordinator набор 325 tests прошли. Полный npm test/typecheck, 700 Python / 12 Node docs checks прошли; все 39 recorded temporary PID отсутствуют, permanent resident сохранил четыре PID, pins и counters 1/0/0. Четыре попытки и три measured Git sources сохранены в verified private ZIP и исходном workspace. Следующий gate — inventory assigned attempts без observation; provided traces не доказывают полноту production denominator. Владелец сценария, реальный поток и targets ещё не установлены; [owner/SLO proposal](./qualification/local-decisions/shadow/observability/resident-service-acceptance.md) остаётся draft. Boot/login и предметная qualification остаются открытыми.

[Inventory сохранённых shadow stages](./qualification/local-decisions/shadow/observability/stage-inventory.md), 07.10 MSK: readonly trace inventory и caller SLI census различают recorded, terminal missing, assigned pending, unassigned и replay. Прежний blind spot воспроизведён на трёх назначенных SQLite stages; independent read-only snapshot audit прошёл 22 checks. Два native repeats прошли по восемь commands и восемь persisted traces, по два настоящих HTTP client calls; три primary retries до shadow оставили один failed stored stage. Settled caller ratio 2/2 и stage ratio 2/4 различаются корректно; pending, legacy gap и malformed inventory не принимаются как success. Independent native audit прошёл восемь checks, temporary cleanup и неизменность permanent resident перепроверены. Полные npm test/typecheck и docs checks прошли: coordinator — 327 tests, docs tooling — 713 Python / 12 Node checks. Все 170 files private ZIP и идентичная копия в исходном workspace проверены по CRC/SHA/size; оба успешных repeats, ранние failure fixtures и три committed Git sources сохранены. Следующий telemetry gate — история shadow assignments и caller attempts при retry; полнота production population не подтверждена. Owner/SLO, actual boot/login и human qualification остаются открытыми.

[История shadow assignments](./qualification/local-decisions/shadow/observability/assignment-history.md), 07.10 MSK: versioned activity history сохраняет каждую shadow dispatch lease при primary retry, отдельный UUID, bindings и validated observation. History/lease writes transactional; record-once, safe/live replay, missing completion и rollback перепроверены. Первый native повтор прошёл восемь commands, девять current/три legacy traces и три HTTP fixture calls; final — восемь commands, десять current/три legacy traces и четыре calls. Три primary retries без HTTP сохраняют три rows; accepted observation не назначается снова. Real old coordinator writer из 47 source files сохранил primary и bound result без обновления ledger; новый reader корректно выдал legacy_gap. Regression failed до fix; final independent audit прошёл 11 checks, все 11 recorded temporary PID отсутствуют, permanent resident unchanged без новых inference. 32 targeted checks, полный coordinator 335 tests, typecheck и 713 Python / 12 Node docs checks прошли; полный npm test пройден до compatibility fix, затронутый coordinator набор повторён после него. Все 167 files и четыре Git states private ZIP проверены по CRC/SHA/size, копия сохранена в исходном workspace. Следующий telemetry gate — negotiated caller intent/return accounting; HTTP/customer population не подтверждена. Owner/SLO, фактический boot/login и human qualification остаются открытыми.

[Учёт caller intent/return](./qualification/local-decisions/shadow/observability/caller-accounting.md), 07.10 MSK: exact worker capability согласует durable intent перед LocalDecisionClient. Повторный receipt не разрешает второй call; validated return сохраняется вместе с observation. Caller ledger отдельный от прежней assignment-history v1. Native: восемь commands, десять traces, семь worker executions, четыре HTTP backend calls; два SIGKILL после intent/return, lost ACK, duplicate и actual old coordinator/worker. Независимый SQL oracle подтвердил девять assignments, семь intents и один return, четыре missing и один pending; intervals сохраняют unknown outcomes без выдуманных timings. Девять independent checks, все 16 temporary processes отсутствуют, resident unchanged без новых inference. 40 coordinator / 36 SLI / три worker targeted checks, full npm test/typecheck и 718 Python / 12 Node docs checks прошли. Private ZIP 158 files / два Git states проверен по CRC/SHA/size и скопирован в исходный workspace. Следующий gate — PostgreSQL concurrency/ownership/COMMIT uncertainty для caller accounting. HTTP/customer population, owner/SLO, actual boot/login и human qualification не подтверждены.

[PostgreSQL caller transactions](./qualification/local-decisions/shadow/observability/caller-postgres.md), 07.10 MSK: concurrent HTTP begin двух coordinator разрешил один вызов; row/event-lock expiry откатывает изменения. Четыре retained real COMMIT replies воспроизвели unknown HTTP 503 без duplicate intent/return; независимый store сохранил primary, reopen и tenant RLS проверены. Raw SQL выявил readonly blind spot: истёкший lease считался pending до maintenance. Два tests failed до fix; reader теперь учитывает expiry из того же SQL read одним временем наблюдения, не меняя activity/events/timing. 42 targeted и полный coordinator 345 tests, typecheck прошли. Оба native прогона прошли по 8 targeted / 108 Fleet/HA tests; independent audit — 11 checks, 48 raw snapshots, 77 committed / 49 compiled contributors на command. Все 226 recorded temporary processes и четыре containers отсутствуют; permanent resident unchanged без новых inference. Следующий gate — actual Python caller continuation после PostgreSQL unknown COMMIT. Customer denominator, owner/SLO, actual boot/login и human qualification остаются открытыми.

[Actual Python caller recovery](./qualification/local-decisions/shadow/observability/caller-worker-postgres.md), 07.10 MSK: четыре unknown PostgreSQL COMMIT сценария завершили primary через тот же coordinator и следующую lease в том же Python worker. Два native прогона прошли по 12 targeted / 112 Fleet/HA tests. Реальный HTTP 204 выявил default-200 defect в probe; failing-before regression сохранён, исправленный повтор измеряет фактический response code. Independent audit — 11 checks, 80 SQL snapshots, 78 committed / 49 compiled contributors на command. Исправленные commands: восемь workers, 16 leases/intents, 12 returns/actual backend calls, четыре missing returns без выдуманной duration. Все 258 recorded temporary processes и четыре containers отсутствуют; permanent resident unchanged. ZIP 326 files / два Git states проверен по CRC/SHA/size и скопирован в исходный workspace. Найден следующий gate: coordinator закрепляет точные profile JSON bytes, offline SLI пока ожидает runtime fingerprint. Owner/SLO, population, actual boot/login и human qualification остаются открытыми.

[Configured profile identity](./qualification/local-decisions/shadow/observability/caller-profile-identity.md), 07.10 MSK: actual CLI rejection воспроизвёл различие SHA configured JSON bytes и runtime fingerprint. Добавлен явный bytes mode; default algorithm, coordinator и historical hashes сохранены. 35 targeted / девять новых regressions и 727 Python / 12 Node docs tests прошли. Восемь actual CLI commands и восемь independent audit checks подтвердили восемь SQL intents, шесть returns/backend calls, два unknown returns и ноль profile mismatch; interval 0–25% остаётся insufficient_data, без выдуманной latency. Десять historical traces сохранили latency/ratios; legacy coverage gap остаётся видимым. 35 resident/session sources unchanged. ZIP 74 files / четыре Git states проверен по CRC/SHA/size и скопирован в исходный workspace. Следующий gate — latest Temporal/PostgreSQL/RAG с caller telemetry и pinned SLI. Owner/SLO, population, actual boot/login и human qualification остаются открытыми.

[Temporal/PostgreSQL/RAG 0.12.3 с caller accounting](./qualification/local-decisions/performance/temporal-caller-runtime-0.12.3.md), 07.10 MSK: v5 gate требует negotiated timing и raw SQL intent/return binding. Исправленный repeat прошёл isolated/session recovery; прежние model prompts, outputs, tokens, embedding vectors и MLX signatures сохранены. Шесть primary / десять embedding items / шесть shadow calls (четыре inference, два unavailable), три warmups, два restarts, 12 citations и независимый replay 137 events подтверждены. Шесть SQL intents/returns, без unknown или profile mismatch; diagnostic caller p50 114.469 / p95 1799.240 ms и bound/timely ratio 4/6. Первый failed run выявил ошибочный schema namespace в новом probe; evidence и cleanup сохранены, regression сверяется с producer export. 214 committed sources, семь audit checks, все 130 recorded processes и шесть containers отсутствуют. Resident unchanged без новых inference. 734 Python / 12 Node docs checks прошли. ZIP 71 files / три Git states проверен по CRC/SHA/size и скопирован в исходный workspace. Owner/SLO/customer population, actual boot/login и independent human qualification/calibration/holdout остаются открытыми; routing выключен.

[Open arrivals на wired 4096 МиБ](./qualification/local-decisions/performance/arrival-rate-4096.md), 07.10 MSK: два native repeats закрепили точные 256/2048-token inputs и fixed monotonic offsets 0.5/1/2 arrivals/с. Все 264 scheduled arrivals учтены: 216 computed, 24 client capacity drops, 24 HTTP busy; 8 warmups отдельно. На 2048 tokens при 1 arrival/с — 24/24, caller p95 874.733 мс; при 2 arrivals/с оба client slot bounds дают 50% computed. Independent verifier сверил журнал, bound intervals, tokens и runtime counters; audit прошёл 8 checks, все 8 recorded temporary PID отсутствуют. 50 committed sources, 12 targeted и 746 Python / 12 Node docs checks прошли. Resident unchanged, 35 session sources и counters 1/0/0 сохранены. ZIP 49 files / один measured Git state проверен по CRC/SHA/size и скопирован в исходный workspace. Planning envelope получил diagnostic данные; owner/SLO/customer population, actual boot/login и human qualification/calibration/holdout остаются открытыми, routing выключен.

[Fixed arrivals при работе primary](./qualification/local-decisions/performance/arrival-primary-4096.md), 07.10 MSK: два native repeats с Qwen3:8b, context 8192 / 128 decode tokens и отдельным wired 4096 МиБ decider. 144 scheduled / 132 computed, 12 client capacity drops, 8 decision warmups отдельно. На 2048 tokens при 1 decision arrival/с active дал 12/24, caller p95 1116.329 мс; idle — 48/48. Primary вернул 10/24 planned calls, 14 capacity drops; 36 actual HTTP overlap pairs проверены. Audit прошёл 9 checks, все 12 recorded PID отсутствуют; resident, 35 protected sources и counters 1/0/0 сохранены. 53 committed contributors, 21 targeted и 755 Python / 12 Node docs checks; v1 verifier compatibility проверена на прежнем evidence. ZIP 47 files / один measured Git state проверен по CRC/SHA/size и скопирован в исходный workspace. Следующая diagnostic проверка — 0.5 decision arrival/с под тем же primary; owner/SLO/customer population, actual boot/login и human qualification/calibration/holdout остаются открытыми, routing выключен.

[0.5 decision arrivals/с при работе primary](./qualification/local-decisions/performance/arrival-primary-half-4096.md), 07.10 MSK: opt-in v3 rate закреплён перед измерением; default v1/v2 сохраняется и прежние native evidence прошли verifier. Два native repeats дали 72/72 computed без drops/busy/measurement errors; long active 12/12, caller p95/max 1110.362 мс; 8 decision warmups отдельно. Primary вернул 10/24 planned calls, 14 capacity drops, 24 actual HTTP overlap pairs подтверждены. 10 audit checks сверили previous/current runtime/primary/inputs и неизменные computed signatures; все 12 recorded PID отсутствуют, resident и 35 protected sources сохранены без новых inference. 53 committed contributors, 25 targeted и 759 Python / 12 Node docs checks прошли. ZIP 57 files / два measured Git states проверен по CRC/SHA/size и скопирован в исходный workspace. Draft planning envelope для обсуждения уточнён до 0.5 decision arrivals/с / 1 active call. Следующие внешние gates — owners и реальные eligible workloads/SLO, actual boot/login, разрешённые независимые заявки и два human reviewers для calibration/holdout. Routing выключен, qualification not_assessed; инженерные synthetic результаты не заменяют эти gates.

[Контракт реального shadow-пилота](./qualification/local-decisions/shadow/observability/shadow-pilot-plan.md),
08.10 MSK: CLI закрепляет project/process/version, future UTC window до семи дней,
profile bytes/fingerprint и предлагаемые targets. Blank owners/data scope остаются
явными; filled plan — только `ready_for_review`, без принятия SLO или qualification.
Восемь regression checks проверяют pins, prospective window, private output,
source drift и отсутствие ложной готовности. Следующий инженерный шаг — census
полного сохранённого cohort; независимые публичные данные подбираются отдельно.

[Публичные IT-вопросы для разметки](./qualification/local-decisions/source-review/public-support/README.md),
08.10 MSK: bounded сбор официального Stack Exchange API и importer закрепляют
completed UTC window после pinned checkpoint, точные source bytes/URL/SHA,
полноту пагинации, явную CC BY-SA 4.0 и атрибуцию. Известные author/text
зависимости группируются с прежним seed. Два review остаются пустыми, метки
из tags/ответов не выводятся; источники не выдаются за клиентские события
Agat. BANKING77 отклонён как независимый benchmark из-за неизвестного
пересечения с обучением и иной схемы intent. Qualification и routing не включены.
Committed сбор `58bd7a5` вернул 145 вопросов; 129 включены, 16 без явной
лицензии исключены. 955 audit checks восстановили source/input bindings и
122 группы; fixed split даёт 49 development / 46 calibration / 34 holdout
заданий. Девять targeted и 776 Python / 12 Node docs checks прошли.
Human review, эталонные метки и model calls — ноль; преобладает английский язык.

[Census сохранённого shadow cohort](./qualification/local-decisions/shadow/observability/shadow-pilot-cohort.md),
08.10 MSK: authenticated API закрепляет project/process/numeric version и
completed half-open окно до семи дней. Все statuses/replay и caller ledgers
читаются из одного SQLite / PostgreSQL REPEATABLE READ READ ONLY snapshot;
превышение row/byte bounds отказывает целиком. Общий trace helper сохраняет
прежнюю семантику. Pilot v2 исправляет version ID на настоящий numeric version.
12 targeted Node, 18 Python и 324 coordinator checks прошли; реальный отдельный
PostgreSQL 17.6 подтвердил конкурентное обновление/RLS/read-only/rollback.
Следующий шаг — offline binding census к prospective plan и caller SLI;
client population/owners/human qualification не подтверждены, routing выключен.

[Offline сверка shadow-пилота](./qualification/local-decisions/shadow/observability/shadow-pilot-evaluation.md),
08.10 MSK: новый CLI требует independent plan/cohort/profile file SHA,
prospective v2 plan и exact instance/run/stage inventories. Caller ratios
включают errors и unknown returns; все assignment profiles/timeouts сверяются
с plan, quantiles считаются по whole cohort до 1000 runs. Default provided
trace limit 32 сохранён. Draft target comparison не утверждает owners/SLO;
zero/legacy data не проходят как успешный пилот. Нативный SQLite/HTTP fixture
включил четыре intents: ok, timeout, missing и pending; target ratios
`[0.25, 0.75]`, diagnostic_only / insufficient_data, model calls 0.
47 независимых audit, 53 targeted и 786 Python / 12 Node docs checks прошли.
Следующий инженерный шаг — bounded authenticated collector exact cohort bytes
для последующей offline сверки. Следующие внешние gates —
реальный permitted workflow/owners и human review/calibration/holdout;
actual boot/login остаётся открытым. Routing false, qualification not_assessed.

[Authenticated capture shadow cohort](./qualification/local-decisions/shadow/observability/shadow-pilot-collection.md),
08.10 MSK: новый collector получает exact HTTP bytes по pinned completed plan
одним direct GET, без redirects/retries/ambient proxy. Remote HTTPS сохраняет
certificate/hostname checks; loopback допускает HTTP. Отдельный network child
ограничен общим deadline и reaped при timeout. Raw export до 16 MiB, private
immutable receipt, source pins и plan/census binding проверяются до сохранения.
Server build, owners, eligibility и population этим не аттестуются; SLO/routing
false, qualification not_assessed. Семь новых transport/CLI regressions прошли.
Нативная цепочка coordinator → collector → evaluator сохранила четыре intents;
80 audit checks подтвердили pins и ratios. Настоящий self-signed TLS server
отклонён до HTTP request, без keylog/usable export. Model calls 0,
793 Python / 12 Node docs checks прошли; timings/window/owners — fixtures.

CI evaluator обнаружил отказ worker restart/shutdown. [Signal shutdown fix](./qualification/local-decisions/performance/worker-signal-shutdown.md)
устраняет подтверждённый Event-lock deadlock: настоящий SIGTERM/SIGINT
больше не вызывает synchronization внутри handler; admission прекращается,
активные futures drain. Три before/after regressions, 149/152 worker checks
(3 optional skips) и полный native PostgreSQL Fleet/HA 113/113 без skips
прошли; failed CI log сохранён, successor/cleanup diagnostics усилены.
Все CI/CodeQL checks PR 154 прошли, этап слит в main.
[Owner source review](./qualification/local-decisions/shadow/observability/owner-source-evidence.md)
подтвердил public maintainer @TitanUser по pinned CODEOWNERS/Git bytes/API;
это runtime owner candidate, не business appointment или SLO acceptance.

[Evidence archive](./qualification/local-decisions/shadow/observability/pilot-evidence-archive-summary.json):
63 files / четыре закреплённых Git states, 5 073 370 bytes; CRC и SHA каждого
файла проверены после копирования ZIP в `docs/private` исходного workspace.
Архив сохраняет native census/evaluator/collector, TLS refusal, worker signals,
PostgreSQL logs и первичные owner sources; customer traces, model weights и
TLS private key в этот архив не включались. Это инженерный протокол, без
human reference labels, qualification или принятия SLO.

[Терминальная независимая разметка](./qualification/local-decisions/source-review/blind-terminal-review.md),
08.10 MSK: pinned blank/partial review показывает исходный вопрос, атрибуцию и
разрешённые варианты, скрывая split/group metadata и чужие ответы. Каждый
выбор требует обоснование и `y`; skips остаются null. Atomic private checkpoint
после подтверждения, partial resume и input/source SHA binding сохраняют
прежний review v1/finalize contract. 13 новых / 33 совместная targeted check
прошли. Reviewer ID/TTY не аутентифицируют человека; human execution,
independence/qualification остаются непроверенными, model calls 0/routing false.
Native PTY/SIGKILL/resume прошёл 65 audit checks; public 129-case pool получил
только skip/quit, меток 0, инженерные заполненные ответы относятся к отдельным
synthetic fixtures. Финальный docs check: 806 Python / 4 optional skips,
12 Node и links/catalog — pass.

[Контекст публичных development-вопросов](./qualification/local-decisions/performance/public-support-context.md),
08.10 MSK: profiler реконструирует pinned raw API/import corpus и выбирает
весь прежний development split для offline tokenization. Точный prompt wrapper,
профиль 0.12.3 / wired 4096 MiB и tokenizer/package pins сохраняются; длинные
тексты остаются в inventory без усечения. Никакие reference labels или
предсказания не создаются; следующий этап — capacity measurement на этом
закреплённом наборе. Семь новых / 16 совместных targeted checks прошли.
Native tokenizer получил 49 cases / 44 development groups: 46 помещаются
в 2048 tokens, три имеют context_too_long; полный диапазон 188–9253 tokens.
257 independent audit checks и 813 Python / 12 Node docs checks прошли.
Context/profile/source pins сохранены; model calls и calibration/holdout
tokenization — 0, resident counters 1/0/0 без дополнительных inference.

[Полный public development HTTP inventory](./qualification/local-decisions/performance/public-support-http-load.md),
08.10 MSK: отдельный owned 0.12.3 / wired 4096 MiB runtime получил все 49
fixed arrivals при 0.5/с / одном client slot. 46 computed (31 ok, 15 abstain),
три context_too_long, drops/errors измерения 0; два warmup отдельно.
Caller computed p95 637.588 мс, max 719.935 мс, все 46 в 5000 мс.
Полный denominator 46/49 и заранее определённый eligible 46/46 сохранены
отдельно. 701 independent audit checks сверили raw journal, profile/input/
source pins, policy/softmax, quantiles и все 51 physical POST handlers.
Все три owned PID отсутствуют, resident сохранил четыре PID, 35 protected
sources и counters 1/0/0. 26 targeted / 820 Python / 12 Node docs checks
прошли. Primary companion здесь не запускался, background неконтролируемый;
accuracy без human labels не измерена, qualification not_assessed/routing false.
Следующий шаг — воспроизводимый source-bound verifier и public inventory
с явно наблюдаемым primary; real workflow/owners/SLO, human calibration/
holdout и actual boot/login остаются открытыми.

[Offline verifier public HTTP inventory](./qualification/local-decisions/performance/public-support-load-verification.md),
08.10 MSK: independently pinned context/plan/result и полные historical Git
sources проходят strict replay. Typed replies, tokens, input/profile, fixed
offsets, one slot, raw journal, nearest-rank quantiles и physical counters
пересчитываются; unknown transport outcome остаётся insufficient_data, а
старый cleanup receipt не выдаётся за новый live PID check. 13 новых / 39
targeted tests, 833 Python / 12 Node docs checks прошли. Настоящий offline CLI
из bf47ef3 дал pass/exact: прежние 49 scheduled, 46 computed, три rejection,
два warmup и 40/57 historical contributors; 53 verifier sources закреплены.
Новые model calls/labels — 0, SLO/routing false / qualification not_assessed.
Следующий capacity этап — весь public development inventory при явно
наблюдаемом primary workload; внешние предметные и boot/login gates сохранены.

[Public development при активном primary](./qualification/local-decisions/performance/public-support-primary-load.md),
08.10 MSK: opt-in v2 закрепляет прежние 49 полных inputs, профиль 0.12.3/wired
4096 MiB и отдельный pinned Qwen3:8b workload на общей monotonic origin.
При 0.5/s decider: 49 scheduled / 48 admitted / 45 computed, три context
rejection и один client_capacity drop; caller p95 1126.021 мс, max 2284.683 мс.
Primary: 25/49 returned, 24 drops, 49 actual HTTP overlap pairs; warmups
сохранены отдельно. Source-bound verifier pass/exact и 307 independent checks
подтвердили все denominators, прежние 48 response signatures, source/model pins,
fresh owned cleanup и неизменный resident. Девять новых / 42 targeted и 842
Python / 12 Node docs checks прошли. Drop показывает ограничение переноса
synthetic envelope на full public inventory; более редкий arrival требует
отдельного prospective протокола. SLO не принят, human accuracy не измерена,
routing false / not_assessed; реальные owners/workflow и boot/login открыты.

[Prospective quarter-rate public inventory](./qualification/local-decisions/performance/public-support-quarter-rate.md):
отдельный v3 protocol задаёт 49 полных decision inputs при 0.25/s
и 98 pinned primary arrivals при 0.5/s в общем 196-s window. Original context
и исходный 0.5/s capacity result сохранены; schedule adjustment получает
отдельный binding до запуска. Legacy bounds/defaults не расширены, slot
сохраняется через 120-s boundary. Изменение rate — проверяемая гипотеза,
не production capacity/SLO и не результат предметной qualification. Native
run: 49 admitted / 46 computed / три context rejection / zero decision
drops, computed caller p95 2063.062 ms, max 2234.359 ms. Primary 49/98
returned, 49 capacity drops, 49 HTTP overlap pairs; source-bound replay
pass/exact и 386 independent checks, все 49 baseline signatures совпали.
851 Python / 12 Node docs checks прошли. Меньшая rate устранила admission
loss в этом опыте, но не улучшила p95; причинный вывод и устойчивый SLO
по двум последовательным runs недоступны. Resident неизменен, все owned
PIDs отсутствуют; next gate — full public inventory через настоящий worker
и coordinator census, без назначения owners или выдуманных human labels.

[Public inventory через worker/coordinator](./qualification/local-decisions/performance/public-support-workflow.md),
08.10 MSK: serial integration с fixture primary и отдельным owned MLX runtime
подтвердила все 49 completed instances и caller returns. 46 computed,
три context rejection; original input SHA и все 49 primary routes сохранены.
Cohort 49 runs / 98 stored stages / 49 shadow stages, HTTP auth 401/200,
physical 51 handlers с двумя warmup. Source snapshot 177 files, 553 independent
checks, fresh cleanup всех 30 owned PID, resident 4 processes/35 sources/counters
неизменен. 857 Python / 12 Node docs checks прошли. Все 49 числовых responses
совпали с baseline; Node JSON normalization отличает raw signatures и
отражена отдельно. Следующий этап — reusable offline receipt replay,
без GPU/HTTP и без превращения lab workflow в назначенный customer pilot.

[Offline public workflow receipt replay](./qualification/local-decisions/performance/public-workflow-verification.md),
08.10 MSK: independent context/plan/result raw pins, historical source bytes,
полные journals, caller ledger, warmups и counters проходят strict replay.
Native CLI pass/exact: 49 instances / 49 returns / 46 computed / три context
rejection, 177 measured и 97 verifier sources. Модель не перезапускалась;
historical cleanup claim отделён от current live PID verification. Четыре
новых / 10 targeted и 861 Python / 12 Node docs checks прошли. Human labels,
appointed owners/customer workflow, SLO и actual boot/login остаются открытыми.

[Whole public workflow при потере owned decider](./qualification/local-decisions/performance/public-workflow-runtime-loss.md),
08.10 MSK: prospective v2 N=5, все 49 instances сохранили primary route и
durable caller return; 5 computed, затем 44 unavailable/unreachable и
44 measured TCP resets без payload reads. 7 physical model handlers
включают 2 warmup; transport failures не выданы за model responses.
180 sources / 30 temporary PIDs, fresh cleanup и resident unchanged;
868 Python / 12 Node docs checks. Контролируемый SIGTERM между completed
instances не доказывает crash-in-flight/recovery или customer SLO.
Следующий шаг — offline replay этого v2 evidence; human/owner/workflow,
calibration/holdout и actual boot/login gates остаются открытыми.

[Offline public workflow loss replay](./qualification/local-decisions/performance/public-workflow-loss-verification.md),
08.10 MSK: reusable v2 CLI подтвердил полный denominator 49, пять healthy
physical scheduled handlers и 44 durable unavailable/TCP resets. 180
measured / 99 verifier sources; v1 compatibility replay сохранил прежние
49 scheduled / 51 с warmup и 177 sources. Native inference не повторялся;
reported cleanup отделён от live proof. 871 Python / 12 Node docs checks.
Следующий допустимый quality diagnostic — порядок вариантов на frozen
public development inventory; labels/calibration/holdout не подменяются
наблюдениями о стабильности. Owner/customer/SLO и actual boot/login gates
остаются открытыми.

[Whole public option-order preflight](./qualification/local-decisions/performance/public-option-permutation-context.md),
08.10 MSK: полный development inventory 49 cases / 44 groups сохранён в
343 case-blocked variants: пять balanced cyclic positions, distinct reverse
и original repeat. Pinned offline tokenizer измерил каждый full prompt:
322 eligible / 21 whole over-limit, tokens 188–9253; original/repeat parts
точно совпали с прежним context. 102 current sources / 34 dependencies,
1941 independent audit checks; шесть новых tests и full 877 Python / 12 Node
checks прошли. Inference/predictions/labels и calibration/holdout access — 0.
Следующий шаг — native serial diagnostic с уже закреплённым 600 s budget,
двумя warmup и retry 0. Agreement зависимых variants не подменяет accuracy,
назначение owners, customer SLO или предметную qualification.

[Whole public option-order native diagnostic](./qualification/local-decisions/performance/public-option-permutation-diagnostic.md),
08.10 MSK: все 343 variants из 49 cases / 44 groups завершены: 322 computed
(219 ok / 103 abstain), 21 whole context rejection, zero omission. У 18/46
fully computed cases меняется status/reason/selected ID/value между orders,
у 28/46 поля стабильны; 46/46 original repeats совпали, probability delta
повтора 0. Fixed dependent orders не дают causal bias/accuracy выводов.
343 physical scheduled / 345 с warmup, 107 sources, 2653 independent audit
checks; три временных PID отсутствуют, protected resident 4 PID / 35
sources / fresh counters unchanged. Full 885 Python / 12 Node checks.
Следующий шаг — reusable independent raw-pinned replay и разбор различий
accepted outputs / argmax / abstention; human qualification, applicable
calibration/holdout, owner/SLO и actual boot/login остаются открытыми.

[Reusable offline option diagnostic replay](./qualification/local-decisions/performance/public-option-diagnostic-replay.md),
08.10 MSK: independent raw pins и два полных replay сохранили 343 scheduled
physical handlers / 345 с warmup, 107 measured / 101 current sources.
Posthoc разбор 46 fully computed original cases: argmax меняется у 8,
accepted/abstain status — у 11, conflicting accepted values — 0; sets
пересекаются. 26 always accepted same value / 9 always abstained / 11 mixed,
original accepted→abstained 5 / abstained→accepted 6. Полный denominator
49 cases / 44 groups сохранён, три context-rejected cases не получили
выдуманных comparison flags. 201 independent audit checks, 14 joint и
full 891 Python / 12 Node checks; новых model/network/live PID calls нет.
Следующий runtime gate — interrupted in-flight handler и owned recovery
на том же endpoint: прежний loss v2 проверял SIGTERM между completed
instances, без crash/recovery. Human/owner/SLO, applicable calibration/
holdout и actual boot/login остаются открытыми; routing false.

[Public in-flight HTTP crash и owned recovery](./qualification/local-decisions/performance/public-workflow-inflight-recovery.md),
08.10 MSK: prospective index 3 / 1337 tokens, active handler snapshot и
SIGKILL в собственной group; actual exit -9. Same-endpoint replacement
прошёл zero origin и два warmup до suffix. Все 49 instances/returns/primary
routes сохранены: 45 computed (31 ok / 14 abstain), один actual unavailable,
три whole context rejection. Два completed counter epochs 5/47 дают 52
handlers, включая четыре warmup; один attested active handler остаётся
с unknown terminal outcome, без выдуманного GPU interruption или reset.
741 independent audit checks / 186 contributors, 48 noninterrupted
controls совпали по binary64 JSON semantics. Все 57 temporary PID
отсутствуют, resident 4 PID / 35 sources / fresh metrics unchanged.
Восемь новых / full 899 Python / 12 Node checks прошли. Human/owner/SLO,
applicable calibration/holdout и actual boot/login остаются открытыми;
routing false. По просьбе пользователя работа остановится после commit,
push и merge этого этапа; новый этап не начинается.

[Reusable offline crash/recovery replay](./qualification/local-decisions/performance/public-workflow-recovery-verification.md),
09.10 MSK: после нового запроса пользователя работа возобновлена. Общий
CLI принимает v3 и сохраняет v1/v2 semantics, raw input pins, historical
Git contributors и source drift gate. Все три сохранённых native reports
прошли с полным denominator 49; v3 сохранил 48 completed scheduled / один
interrupted unknown handler / четыре warmup, epochs 5/47. Содержательные
поля прежних reports совпали; 105 current verifier contributors и 360
independent audit checks закреплены. Шесть новых / full 905 Python tests,
12 Node checks, links/catalog прошли. Model/network/live PID calls — 0.
Следующий runtime gate — active shadow caller deadline и восстановление
полного процесса на frozen development inventory. Human/owner/SLO,
applicable calibration/holdout и actual boot/login остаются открытыми;
routing false / not_assessed.

[Shadow response boundary checks](./qualification/local-decisions/shadow/response-boundaries.md),
09.10 MSK: в подготовке caller deadline gate воспроизведено принятие HTTP
200 result и быстрых 409/503 reasons после cancellation Event, пока watchdog
ещё не получил CPU. Worker теперь проверяет отмену/deadline после connect,
headers, body и JSON parsing, с cancelled precedence при socket timeout.
Восемь новых socket regressions сохраняют primary output и следующий
здоровый вызов; обычный watchdog repeat подтвердил четыре cancelled
responses. Full 160 worker / 905 Python docs / 12 Node checks, typecheck,
links/catalog прошли; optional skips 3/4 явно сохранены. Следующий gate —
prospective timeout полного public workflow с physical/caller accounting
и здоровым suffix. Human/owner/SLO и calibration/holdout остаются открытыми;
routing false / not_assessed.

[Full workflow caller timeout](./qualification/local-decisions/performance/public-workflow-timeout.md),
09.10 MSK: новый prospective v4 протокол удержал фактический upstream ответ
на input index 3 до caller deadline. Все 49 instances/returns сохранили
primary branch: 45 computed (31 ok / 14 abstain), один unavailable timeout
и три whole context rejection. Physical scheduled 49 (31 ok / 15 abstain /
3 context rejected), включая отдельно доказанный undelivered abstain; две
warmup дают 51 completed handlers в одном epoch, без restart/retry.
Target upstream 583.704 ms / EOF 10000.983 ms / caller 10001.556 ms;
малый scheduling/cleanup overrun не скрыт. Healthy suffix — 45 cases.
Common raw-pinned offline replay и 1460 independent checks подтвердили
186 contributors, exact numeric controls всех 49 результатов, counters и
отсутствие 33 temporary PID; resident четыре PID / 35 sources unchanged.
12 новых / 18 focused / full 917 Python / 12 Node checks, typecheck,
links/catalog прошли. ZIP 89 entries / шесть source snapshots проверен по
CRC/SHA/size в двух копиях и сохранён в исходном workspace `/docs/private`.
Два preparation failure и red regressions сохранены
отдельно: исправлены default option flags и JSON integer/float comparison,
без переименования failed evidence в pass. Следующий gate — actual
coordinator cancellation active shadow lease и durable caller accounting.
Human/owner/SLO, calibration/holdout и actual boot/login остаются открытыми;
routing false / not_assessed.

[Отзыв lease во время shadow HTTP](./qualification/local-decisions/shadow/lease-cancellation.md),
09.10 MSK: actual coordinator cancel отзывал lease, но socket оставался открытым
9991.511 мс. Scoped watcher наблюдает renewal только во время разрешённого
shadow-вызова: wait 500 мс, общий HTTP deadline 1000 мс и bounded body 4096 bytes.
Первый repeat дал EOF 501.889 мс; новая trickling-header regression выявила
оставшийся thread, поэтому финальный путь использует disposable transport
с обязательным reap. Финальный repeat дал EOF 570.463 мс и healthy suffix
на том же worker. Оба endpoints — явные fixtures, новых MLX inference нет.
Cancel auth 401/204, late observation/complete 400 и renewal 404 сохранены;
durable caller return отменённого assignment остаётся null / return_missing,
без выдуманного cancelled observation. Девять новых, 169 worker / 917 Python
/ 12 Node docs tests прошли; 3/4 optional skips сохранены. Independent audit
781 checks подтвердил три Git states, шесть absent PID и resident четыре PID
/ 35 sources unchanged. ZIP 66 entries / пять source snapshots проверен по
CRC/SHA/size в двух копиях и сохранён в исходном workspace `/docs/private`.
Следующий runtime gate — полный inventory 49 cases
с actual coordinator cancellation, owned native runtime и offline replay.
Human/owner/SLO, calibration/holdout и actual boot/login остаются открытыми;
routing false / not_assessed.

[Full workflow coordinator cancellation](./qualification/local-decisions/performance/public-workflow-cancellation.md),
09.10 MSK: prospective v5 сохранил весь development inventory 49 cases / 44
groups и реальные original POST bytes. На index 3 proxy удержал completed
MLX response без headers; authenticated coordinator cancel дал 204, renewal
404, поздние observation/complete/fail — 400. После начала cancel actual EOF
наблюдался через 458.000 ms; upstream 566.381 ms / local caller 1179.725 ms.
Completed instances и durable returns — 48, cancelled instance с unknown
return — один; 45 computed (31 ok / 14 abstain), три whole context rejection.
Physical 49 (31 ok / 15 abstain / 3 context rejected) и две отдельные warmups
дают 51 handler / один epoch; retry/restart нет. Все 45 suffix cases завершены
на том же worker. Late primary output не объявлен completed branch.
Common offline replay и 2907 independent checks сверили 188 measured
contributors, все 49 native signatures с frozen healthy control, 201 actual
lease HTTP records, timing/counters и отсутствие 33 recorded temporary PID.
Resident четыре PID / 35 sources unchanged. Дополнительная red mutation
выявила foreign lease renewal в replay; final verifier закрыл cohort fence
и подтвердил unchanged native v5 и prior v4 без новых model calls.
13 новых / 38 focused / full 930 Python / 12 Node checks и typecheck прошли;
четыре optional docs skips сохранены. Private ZIP с raw evidence и source
snapshots проверен по CRC/SHA/size в двух копиях и сохранён в исходном
workspace `/docs/private`. Измерение доказывает отмену после готового native
ответа. Следующий runtime gate — доставка cancellation во время actual
active native HTTP handler с доказанным upstream disconnect, состоянием
isolated backend и healthy suffix;
GPU interruption нельзя выводить из v5 результата.
Human/owner/SLO, calibration/holdout и actual boot/login остаются открытыми;
routing false / not_assessed.

[Actual resident boot и GUI login](./qualification/local-decisions/shadow/observability/resident-actual-boot-login.md),
09.10 MSK: установленный observer автоматически записал оба verified events,
exit 0, на новом boot UUID после independently pinned baseline. Separate
read-only audit подтвердил четыре live процесса, 35 historical Git sources,
34 dependency pins, прежние package/profile/registration/plists и свежий
successful scrape. Physical counters нового boot — computed/rejected/failed
0/0/0; inference и service mutation — ноль. Original receipts не изменены;
первая audit ошибка структуры metrics сохранена, corrected audit прошёл
861 checks. Обе private ZIP copies проверены по CRC/SHA/size и сохранены
под /docs/private. Actual boot/login gate этого package/host/event закрыт;
recovery duration от boot, multi-day availability и customer SLO не измерены.
Следующий инженерный этап — actual active native HTTP cancellation,
upstream disconnect, isolated backend state и healthy suffix. Human owners,
permitted real workflows, independent labels/calibration/holdout остаются
открытыми; routing false / qualification not_assessed.

[Active native peer cancellation и recovery](./qualification/local-decisions/performance/public-peer-active-cancellation.md),
09.10: свежий native прогон сохранил все 49 development cases / 44 groups;
48 delivered results (31 ok / 14 abstain / 3 whole-context rejections), один
local unavailable.cancelled. Target index 25 отменён после fresh active HTTP
snapshot без response bytes; штатный retirement `inference_cancelled`, exit 75
и child exit -15 подтверждены. Replacement на том же порту/профиле завершил
два новых warmups и все 23 suffix cases; всего 53 physical POST starts / 52 known
completions, interrupted terminal counter unknown, retry 0. Offline replay и
independent stdlib audit сверили 193 contributors, 48 baseline signatures,
physical epochs и отсутствие всех шести temporary PIDs. 23 focused tests passed;
resident package/config/profile и четыре protected PIDs unchanged. Actual
boot/login уже закрыт; следующий runtime gate — actual coordinator cancellation
во время active native HTTP, durable unknown return/late-write rejection и owned
recovery. Owner/customer/SLO, human calibration/holdout остаются открытыми,
routing false / not_assessed; GPU kernel preemption не заявляется.

[Actual coordinator cancellation во время native HTTP](./qualification/local-decisions/performance/public-workflow-active-cancellation.md),
09.10: свежий source-bound v6 прогон всех 49 development cases / 44 groups
с реальным coordinator/worker сохранил 48 delivered results и один cancelled
assignment с durable `return_missing`; local unavailable.cancelled доказан
отклонённым HTTP return. После actual pending intent/401 probe fresh active
snapshot привёл к authenticated cancel 204, renewal 404 и upstream EOF за 595 мс.
Late observation/complete/fail — 400. Native exit 75 / child -15, cleanup всех
трёх original native PIDs, same-port/profile replacement и 23 healthy suffix
cases подтверждены. Всего 53 POST starts / 52 known completions, четыре warmups,
retry 0; interrupted terminal counter unknown. 54 focused tests, TypeScript,
offline replay и independent stdlib audit прошли; 48 baseline signatures,
193 measured source hashes, все 49 lease assignments и отсутствие 13 temporary
PIDs перепроверены. Resident package/config/profile, четыре protected PIDs и
inference counters unchanged. ZIP/source snapshots и original workspace copy
сохранены под /docs/private. Следующий runtime gate — actual worker caller
deadline до native response с owned recovery и полным suffix. Primary fixture,
owners/customer/SLO/human calibration/holdout открыты, routing false /
not_assessed; GPU kernel preemption не заявляется.

[Actual worker deadline во время native HTTP](./qualification/local-decisions/performance/public-workflow-active-deadline.md),
09.10: свежий v7 прогон всех 49 original development cases / 44 groups закрепил
250 мс для target index 25 в единственной published process version; остальные
48 cases сохранили 10000 мс и полные исходные inputs. Actual worker вернул
unavailable.timeout за 250.732 мс после fresh active native snapshot без response
bytes; timeout/primary completion — 200, renewals — 204. Все 49 durable returns
и primary routes завершены, unknown caller returns 0. Upstream EOF привёл к
native exit 75 / child -15; три original native PIDs отсутствовали до replacement,
same-port/profile recovery завершил два warmups за 7106 мс и 23 healthy suffix
cases. Всего 53 POST starts / 52 known completions, retry 0; interrupted terminal
counter unknown. 62 focused tests, TypeScript, raw-pinned offline replay и fresh
v6 control replay прошли. Independent stdlib audit сверил 48 baseline signatures,
195 measured sources, published graph/budgets, 49 lease assignments и отсутствие
10 temporary PIDs. Resident package/config/profile, четыре protected PIDs и
inference counters unchanged; ZIP/source snapshots сохранены под /docs/private.
Следующий engineering gate — весь inventory с real primary и matched control.
Owners/customer/SLO/human calibration/holdout открыты, routing false /
not_assessed; GPU kernel preemption не заявляется.

[Дополнительный paired native admission gate](./qualification/local-decisions/performance/public-workflow-paired-concurrency.md),
10.10: исходные 49 development cases / 44 groups выполнены в 25 bounded pairs
через actual coordinator и одного worker concurrency 2. Все 49 instances,
durable returns и fixture primary routes завершены. Native: 24 computed
(15 ok / 9 abstain), 24 busy, один whole-context rejection; ещё два длинных
входа получили busy до token/context check. Original full inputs сохранены,
retry/restart 0. Одно active metrics witness связало busy с ещё активным
admitted партнёром в паре 0/1; 51 completed physical calls включают два warmups.
63 focused tests, TypeScript, v8 offline replay и fresh v6/v7 control replay
прошли. Independent stdlib audit: 3177 checks, 45347 JSON keys без дубликатов,
25 совпавших typed baseline signatures, 199 actual lease records и все
34 temporary PIDs absent. Resident package/profile/config, четыре PIDs,
20 runtime files и counters unchanged. Private ZIP и source snapshots сохранены
под /docs/private. Это fixture-primary admission evidence; следующий плановый
шаг полного inventory с real primary и matched control сохраняется.
Owners/customer/SLO/human calibration/holdout открыты, routing false /
not_assessed; GPU kernel concurrency и customer capacity не заявляются.

[Полный inventory с real primary и matched control](./qualification/local-decisions/performance/public-workflow-real-primary.md),
10.10: actual coordinator/worker завершили 98 workflow (49 control / 49 shadow)
на всех original development cases / 44 groups. Pinned Ollama 0.35.1 / Qwen3:8b
получил original input ровно один раз, context 32768 / decode 128; все 98 actual
outputs сохранены, все 49 matched primary requests совпали. Native decider
вернул 31 ok / 15 abstain / три whole-context rejections, все 49 negotiated
caller returns известны; 49 signatures совпали с healthy control. Один epoch,
два decision warmups / 51 physical calls, один primary warmup; retry/restart 0.
Primary outputs совпали в 47/49 пар; 83 responses достигли decode limit.
Whole-workflow p50 control/shadow — 4121.235/4465.132 ms, median matched delta
722.171 ms; shared cache/prefill и один проход не устанавливают causal SLO.
Первый failed native attempt сохранил 24 completed workflow, timeout и ошибку
finalization без result receipt; v2 исправил полный input path, prospective
budget и failure/PID journal, затем выполнил все 98 workflow заново. CI early
timer wake воспроизведён до fix, census boundary guard исправлен. 18 tests,
TypeScript и final offline replay прошли. Stdlib audit — 5254 checks /
171430 JSON keys; все 111 owned PIDs absent, protected resident unchanged.
Shared v8 driver получил тот же census guard после controlled early wake;
49 related regressions, оба fresh offline replay и 1002 Python tests
(четыре optional skips) прошли. Original native archive сохранён, CI follow-up
записан отдельным supplement.
Successful/failed raw evidence, source snapshots и original workspace copy
сохранены под /docs/private. Следующий runtime gate — cancellation при actual
worker concurrency 2 с unaffected соседним workflow и owned recovery.
Owners/customer/SLO/human calibration/holdout открыты, routing false /
not_assessed; input truncation и customer capacity не заявляются.

[Actual native cancellation при двух занятых слотах worker](./qualification/local-decisions/performance/public-workflow-two-slot-cancellation.md),
10.10: четыре полных original inputs (indices 24–27), actual coordinator и
Python worker concurrency 2 / sequential global 2. Target отменён во время
активного native HTTP без response bytes; соседний primary TCP-запрос остался
открытым, assignment/worker/attempt 1 сохранились через retirement/recovery.
Peer и suffix завершились без retry: три durable primary outputs, один
cancelled workflow, три known caller returns (один ok / два abstain) и один
unknown `return_missing`. Late target observation/complete/fail — 400;
только target renewal — 404. Native exits 75/130, два epochs / четыре warmups,
восемь starts / семь known physical terminals; interrupted target counter
unknown. Caller 596.878 ms, cancel→EOF 568 ms, peer hold 6717 ms.
Source 201 / context 40 files, три healthy baseline signatures совпали;
14 новых / 77 related tests, strict TypeScript, offline replay и полный
1016-test Python suite (четыре optional skips) прошли.
Independent stdlib audit — 680 checks / 27176 JSON keys; все 10 recorded
temporary PIDs отсутствуют, resident unchanged. Это focused fixture-primary
gate; предыдущий real-primary inventory сохраняется отдельно. Следующий
runtime gate — active worker caller deadline при concurrency 2 с unaffected
in-flight primary peer и owned recovery. Owners/customer/SLO/human
calibration/holdout открыты, routing false / not_assessed.

[Active caller deadline при двух worker slots](./qualification/local-decisions/performance/public-workflow-two-slot-deadline.md),
10.10: те же четыре full original inputs 24–27, actual coordinator/Python
worker concurrency 2, sequential global 2. Две версии одного direct graph
различаются только caller timeout 10000/250 ms; peer primary начат раньше
target и остаётся открытым на той же lease/worker/attempt через native
retirement/recovery. Все четыре workflow и primary outputs сохранены,
четыре known caller returns: target `unavailable/timeout` за 250.736 ms,
три healthy native returns (один ok / два abstain). Run cancellation нет,
required writes 200 / renew 204, fail нет. Native starts 8 / known terminals 7,
четыре warmups, два epochs; interrupted target counter unknown.
Peer hold 6285 ms, native EOF→retirement 369 ms, recovery 5438 ms.
Source 206 / context 40, три baseline signatures совпали; 14 новых /
48 related tests, TypeScript и offline replay прошли. Independent audit
708 checks / 27672 JSON keys; все 11 temporary PIDs absent, resident unchanged.
Полный 1030-test Python suite (четыре optional skips) прошёл;
raw/restorable archive остаётся приватным.
Следующий performance gate — полный paired inventory с real primary и
matched control при worker concurrency 2. Owners/customer/SLO/human
calibration/holdout открыты, routing false / not_assessed.

[Полный paired inventory с real primary и matched control](./qualification/local-decisions/performance/public-workflow-paired-real-primary.md),
10.10: все 49 original development cases / 44 groups прошли 98 actual workflow
(49 control / 49 shadow), worker 2 / sequential global 2. Pinned Ollama
0.35.1 / Qwen3:8b использует два actual runner slots, context 32768 на запрос /
65536 total, decode 128. Все 98 primary outputs и 49 known caller returns
сохранены; 49 matched primary requests совпали, original input подан один раз.
50 bounded batches / 48 actual two-slot pending-primary witnesses;
native primary HTTP overlap min 2407 ms. Native returns — 16 ok / 7 abstain /
два whole-context rejections / 24 busy; каждый busy подтверждён pending peer
той же original пары и active-native gauge 1. Один overlong input получил
busy до context check; все cases включены в census. Все 25 typed signatures
совпали с healthy serial baseline. Exact primary outputs совпали в 45/49 пар,
четыре различия сохранены; 83/98 responses достигли decode limit.
Whole-workflow p50 control/shadow — 6069.950/7619.282 ms, median matched delta
1151.024 ms. Shared cache/prefill и один проход не устанавливают causal SLO
или customer capacity. Native epoch один, два decision warmups / 51 known
physical terminals, один primary warmup отдельно; retry/restart 0.
Source 209 / context 40 files; offline replay, 11 новых / 68 related tests,
strict TypeScript, serial native compatibility и 1041 Python tests (четыре
optional skips) / 12 Node docs tests прошли. Independent stdlib audit —
7066 checks / 203542 JSON keys; все 117 recorded temporary PIDs absent,
resident unchanged. Immutable ZIP — 173 files / 41419474 bytes; CRC/SHA/size
и actual original-workspace copy restored replay прошли, model calls 0.
Raw/restorable archive сохраняется под /docs/private.
Runtime cancellation gate с двумя worker slots и actual real-primary peer
выполнен далее в явно закреплённом queued-primary профиле. Owners/customer/SLO/
human calibration/holdout открыты, routing false / not_assessed.

### 10.10.2026 — two-slot active cancellation с queued actual primary

[Протокол](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-cancellation.md),
[summary](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-cancellation-summary.json)
и [archive receipt](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-cancellation-archive-summary.json)
фиксируют actual native v2 gate: worker/global concurrency=2, actual primary
NUM_PARALLEL=1 / queue=1 / context=32768, прежние Qwen3:8b generation settings.
Whole 49 inputs / 44 groups остаются в sealed plan; focused indices
[24,25,12,27] выбраны до scoring, peer — longest original whole input.
Target actual primary admitted до создания peer, два HTTP requests действительно
пересекались 5955.000 ms. Primary response не удержан.
3 completed / 1 cancelled workflow; 4 actual primary responses / 3 durable
outputs, 3 known callers / 1 unknown return_missing. Peer lease/stage/worker
сохранены через cancel→EOF (566.000 ms), old native retirement
(479.000 ms after EOF) и новый epoch с двумя warmups;
actual peer response получен после recovery. Late observation/complete/fail=400,
revoked renewal=404, target output=null. Healthy native: 2 abstain / 1 whole-context
rejection; все 3 typed signatures совпали с frozen healthy control. Native physical
8 starts / 7 known terminals / 2 epochs / 4 warmups; interrupted target counter
неизвестен, retry=0. Все 14 owned PIDs absent, protected resident unchanged.

Всего 3 native attempts / 1 successful: NUM_PARALLEL=2 attempts 1/2 FAILED и
сохранены со всеми raw bodies/source/PID receipts. Первый выявил fixture wait=30s
при заранее declared primary=180s; после исправления второй peer ответил до
recovery/target caller. V2 one-model-slot профиль закреплён отдельно, первые
attempts не превращены в PASS. Cancellation при двух primary model slots ещё
не квалифицирована. 12 новых / 69 related tests, 1053 Python tests (4 optional
skips), strict TypeScript / 12 Node docs tests прошли; prior actual cancellation,
deadline и paired inventory offline replay PASS, model calls=0. Stdlib audit:
743 checks / 28892 JSON keys. Source 210 / context 40 files. Immutable ZIP —
149 files / 43362674 bytes; every entry CRC/SHA/size и actual original
copy restored replay PASS, model calls 0. Все attempts сохранены; public payloads
содержат только metadata. Следующий runtime gate — worker deadline с этими
двумя worker slots и queued actual primary peer; затем отдельный prospective
fault protocol NUM_PARALLEL=2. Owners/customer/SLO/human labels/holdout открыты,
routing false / not_assessed.

### 10.10.2026 — worker deadline с queued actual primary

[Протокол](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-deadline.md),
[summary](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-deadline-summary.json)
и [archive receipt](qualification/local-decisions/performance/public-workflow-two-slot-real-primary-deadline-archive-summary.json)
фиксируют actual gate: worker/global=2, actual primary NUM_PARALLEL=1 / queue=1,
request context=32768, прежняя Qwen3:8b generation. Whole 49 inputs / 44 groups
сохранены в sealed plan; focused original indices=[24,25,12,27], whole longest
peer=9253 decision / 9013 primary tokens. Target primary
pending до peer creation; actual HTTP overlap=5666.000 ms,
response не удержан. Published process v1 healthy10000 / v2 target250 различается
только timeout. 4 completed workflow / 4 primary outputs / 4 known callers;
one unavailable/timeout=254.246 ms записан в original
target lease, cancellation/revocation/retry=0. Healthy native 2 abstain / 1
context rejection, все 3 typed signatures совпали с frozen baseline.
Native EOF→retirement=338.000 ms, recovery=7172.000 ms;
actual peer response после new epoch/two warmups, assignment/stage/worker сохранены.
8 physical starts / 7 known terminals / 2 epochs / 4 warmups; target native
typed terminal неизвестен. 14 owned PIDs absent, protected resident unchanged.

1 native attempt / 1 successful; 215 measurement / 40 context source
files. Stdlib audit 772 checks / 29394 JSON keys;
offline replay и previous cancellation/deadline raw receipts PASS, model calls=0.
12 новых / 81 related tests, 1065 Python tests (4 optional skips),
strict TypeScript / 12 Node docs tests прошли. Immutable ZIP 91 files / 41402360 bytes сохраняет
raw/source/test/replay evidence; every-entry CRC/SHA/size и actual original-copy
restored replay PASS, model calls=0; public payloads только metadata. Следующий
runtime gate — отдельный prospective fault protocol NUM_PARALLEL=2 с actual
target-first admission и naturally pending peer; two-primary-slot deadline
ещё не квалифицирован. Owners/customer/SLO/human labels/holdout открыты,
routing=false / not_assessed.

### 10.10.2026 — active cancellation с двумя actual primary slots

[Протокол](qualification/local-decisions/performance/public-workflow-two-slot-parallel-real-primary-cancellation.md),
[summary](qualification/local-decisions/performance/public-workflow-two-slot-parallel-real-primary-cancellation-summary.json) и
[archive receipt](qualification/local-decisions/performance/public-workflow-two-slot-parallel-real-primary-cancellation-archive-summary.json)
фиксируют отдельный prospective gate: worker/global=2, NUM_PARALLEL2 / queue1,
actual np2 / context65536 / request32768, прежняя decode128 generation.
Whole 49 inputs / 44 groups и projection[24,25,12,27] сохранены. После failed
первого target-first attempt новый candidate создаёт peer при observed target
decode116..127 и подтверждает два processing slots до target response.
Фактически admission decoded=116; HTTP overlap=5618.000 ms.
3 completed / 1 cancelled, 3 known / 1 unknown callers; retry0.
3 healthy typed signatures совпали с baseline; cold recovery=7401.000 ms,
peer response после recovery, original lease/assignment/worker и primary output сохранены.
8 starts / 7 known terminals / 4 warmups / 2 epochs; target typed terminal неизвестен.
14 owned PIDs absent, protected resident27 checks unchanged.

2 native attempts / 1 successful; отдельный API diagnostic failed_parser /
wire audit PASS сохранён с 1 scoring primary + 1 warmup, вне workflow denominator.
Stdlib audit 4891 checks / 89948 JSON keys, source216 /
context source40; offline/legacy/restored replay model0 PASS.
18 новых / 99 related / 1083 Python tests (4 optional skips), strict TS /
12 Node docs tests PASS. Immutable evidence включает failed/native/probe/source/test
artifacts; CRC/every-entry SHA/size и actual original-copy replay PASS.
Следующий gate — NUM_PARALLEL2 worker deadline; customer/SLO/human labels/holdout
и owners открыты, routing=false / not_assessed.

## Вывод: что именно можно воспроизвести

**Функциональный локальный аналог сделать реалистично: готовая языковая основа → оценка разрешённых вариантов → вероятности → проверяемое решение.** Первую работающую версию можно получить без обучения собственной фундаментальной модели. Затем качество и калибровку придётся подтвердить на данных Агат и при необходимости дообучить модель.

В изученных [официальных пояснениях RLCD](https://docs.typesafe.ai/introduction/machine-learning-primer) и [публичных репозиториях TypeSafe](https://github.com/typesafe-ai) не найден полный комплект весов, архитектуры и рецепта обучения Jev, достаточный для точного воспроизведения. Поэтому равенство Jev по широкому набору задач, скорости и вероятностям нельзя обещать. Доступность SDK не означает доступность модели.

| Цель | Что потребуется | Что будет доказано |
|---|---|---|
| Похожий API с `Choice / Boolean / Score` | Локальная модель и адаптер | Совместимость формы входа/выхода |
| Быстрые решения без генерации текста | Прямое чтение logits либо обучаемая выходная голова; пакетирование | Выбранный способ вычисления и измеренная скорость |
| Полезная модель для процессов Агат | Предметная разметка, дообучение по необходимости, calibration и holdout | Качество на заданном распределении задач |
| Универсальный аналог Jev | Разнообразные задачи, новые семейства вопросов в тесте, оптимизация архитектуры и serving, много итераций исследований | Только результат конкретного сравнения; точная технология Jev остаётся неизвестной |

Локальный inference и локальное обучение — разные требования. В предложенном варианте оба можно выполнять без внешнего API: веса загрузить заранее, данные и teacher при необходимости держать в своём контуре. Размер модели-учителя может превышать возможности машины, на которой будет работать итоговая модель.

## Что уже доступно открыто

| Кандидат | Что опубликовано | Роль в эксперименте |
|---|---|---|
| [SemIf](https://github.com/TheoLeeCJ/SemIf), код MIT | Оценка вариантов из замороженной открытой модели; чтение logits без декодирования ответа; CUDA и MLX | Baseline без обучения, в том числе на Apple Silicon. Распределения требуют собственной проверки калибровки |
| [decider](https://github.com/Mapika/decider), код Apache-2.0; [decider-2b](https://huggingface.co/Mapika/decider-2b), model card Apache-2.0 | Готовая модель на Qwen3.5-2B, API структурированных решений, supervised training и eval | Первый кандидат на применение/адаптацию. Проверить русский язык, переносимость и числовую семантику ответа |
| [NanoJev](https://github.com/TianyuCodings/NanoJev), код MIT | Qwen3-0.6B с decision heads, веса, данные и training pipeline | Образец собственной небольшой модели с динамическими кандидатами. Показанные игровые задачи не доказывают качество документооборота |
| [GLiClass](https://github.com/Knowledgator/GLiClass) | Zero-shot классификация с динамическими метками | Лёгкий baseline для классификации. Не считать автоматически эквивалентом всех вопросов Jev |

Это кандидаты для сравнения, не готовый перечень production-компонентов. Лицензии конкретных базовых весов и наборов данных проверяются отдельно от лицензии кода.

Существенные детали decider: model card помечена как English; опубликованная реализация определяет `confidence` через вероятность выбранного варианта, тогда как TypeSafe описывает свою статистику распределения. Совпадение JSON-полей не делает эти величины взаимозаменяемыми. В репозитории также отмечено, что полный RL-этап v10 ещё не входит в этот пакет. Для воспроизводимого обучения следует отдельно выбрать доступный supervised recipe и закрепить версию; запуск готовых весов и воспроизведение их полного обучения — разные результаты.

## Предлагаемая архитектура модели

Для Агат сначала стоит сравнить два варианта. Это наш проектный выбор, а не реконструкция закрытой архитектуры Jev.

**Вариант A — logits готовой модели.** Передать состояние, вопрос и описания вариантов, привязанных к коротким меткам. Прочитать оценки разрешённых меток из сети в позиции ответа и нормировать их. JSON формирует обычный код. Вариант не требует, чтобы модель последовательно написала текст или числа вероятностей. Нужно проверить токенизацию: одна буква не гарантированно является одним и тем же токеном в любом контексте. Нормировка по разрешённым меткам даёт распределение, условное на этот список, а не доказанную вероятность истины.

**Вариант B — обучаемый scorer/decision head поверх backbone.** Сеть получает `(state, question, candidate)` и вычисляет оценку кандидата; веса scorer общие для кандидатов. Полный набор вариантов одного вопроса нормируется совместно. Такой интерфейс поддерживает новые описания вариантов во время выполнения. Статическая голова на три класса достаточна для одной фиксированной проверки, но не заменяет универсальный интерфейс с произвольными вариантами.

| Примитив | Вычисление | Особенность |
|---|---|---|
| Choice | `p_i = softmax(z / T)_i` по всему набору взаимоисключающих вариантов | Нужен вариант «другое/недостаточно данных», когда перечень не исчерпывающий |
| Boolean | Sigmoid одного logit либо распределение yes/no | Нельзя интерпретировать отдельные multi-label вероятности как одно Choice-распределение |
| Score | Распределение по описанным уровням; при заданной числовой шкале — взвешенная сумма | Для чисто порядковой шкалы среднее требует отдельного смыслового обоснования; сохранять полное распределение |

`T` — параметр калибровки, выбранный на отдельной выборке, не универсальная константа. Вероятности модели не следует смешивать с полем «уровень риска»: риск также зависит от последствий действия.

Техническая реализация первого собственного варианта: PyTorch/Transformers, небольшой открытый backbone порядка 0,6–2B, direct logits или общий scorer, отдельный локальный inference endpoint. Сравнение с 4B полезно как проверка потерь качества от уменьшения модели. Для обычной классификации включить более лёгкую модель и правила. Конкретный backbone выбирается по русскому benchmark, а не только по числу параметров.

Не нужно заранее обучать backbone с нуля. Если выбран вариант с новой случайно инициализированной головой, её необходимо обучить: подключение слоя само по себе не создаёт качественный классификатор.

## Данные: главный объём предметной работы

Первый scope — три семейства: поддержка утверждения источником; классификация документа/заявки; необходимость уточнения исходных данных. Каждое семейство имеет собственные допустимые варианты и правила разметки. Формулы и доступы проверяются кодом.

Одна запись содержит:

- состояние/исходный фрагмент, вопрос, полный набор вариантов с описаниями;
- правильную метку либо обоснованное целевое распределение;
- ссылки на доказательства, язык, семейство задачи и источник разметки;
- группирующий ID документа/процесса, версию правил и split.

Источники разметки: реальные разрешённые примеры с экспертным решением, программно проверяемые синтетические случаи, открытые наборы с подходящими условиями и дополнительные примеры от локальной сильной модели. Teacher-разметка остаётся псевдоразметкой; независимый человеческий holdout проверяет её ошибки. Не нужно назначать искусственные «0,93 уверенности» каждому примеру: cross-entropy обучается и на обычной правильной метке. Согласие нескольких разметчиков и вероятность события имеют разную семантику.

Особенно нужны отрицания, близкие числовые значения, даты, проценты/процентные пункты, похожие неподтверждённые утверждения, конфликтующие фрагменты, несуществующий правильный вариант, незнакомые категории и инструкции, внедрённые в данные. Перестановка вариантов не должна менять их смысл. Для кандидатов за пределами обученного числа отдельно проверяется переносимость.

**Стартовый бюджет данных — оценка для планирования:** 500–1 000 тщательно размеченных случаев для первоначального сравнения; затем, если дообучение оправдано, 5–30 тысяч качественных примеров по выбранным семействам. Это не минимальный универсальный рецепт и не обещание нужного качества. Объём увеличивается по learning curve и характеру ошибок. Универсальные новые вопросы потребуют значительно более разнообразных задач, а не только дополнительных перефразировок этих же примеров.

Разделить train, development, calibration и final holdout по документам, источникам и шаблонам; дополнительно отложить целые семейства вопросов для проверки обобщения. Разметка и пороги не меняются после просмотра итогового holdout; иначе нужен новый holdout. Короткого учебного SUPPORT-DEMO из пакета отчёта для этого недостаточно.

## Обучение и калибровка

Рекомендуемая последовательность:

1. Измерить готовые модели, правила и текущую генеративную проверку на одинаковых входах. Если готовый вариант проходит требования, собственное обучение не является обязательным этапом.
2. Для собственной головы провести head-only warm-up. Затем сравнить обучение головы с дообучением части backbone через LoRA/QLoRA либо полным fine-tuning небольшой модели. [QLoRA](https://arxiv.org/abs/2305.14314) описывает способ уменьшения памяти обучения; совместимость конкретной головы и runtime проверяется отдельно.
3. Обучать непосредственно выходные распределения: cross-entropy по правильной метке, soft cross-entropy/KL при обоснованных мягких целях; сравнить Brier как отдельный эксперимент. В loss участвует полный набор кандидатов вопроса, padding исключён. Не заменять эту задачу генерацией строки с вероятностями.
4. При необходимости применить [дистилляцию](https://arxiv.org/abs/1503.02531) от более сильного локального учителя. Сохранить проверенные истинные метки и отдельно разобрать расхождения учителя с ними. Наличие учителя не гарантирует сохранение его качества в маленькой модели.
5. После обучения подобрать temperature scaling на calibration split и измерить итог на независимом holdout. [Guo et al.](https://proceedings.mlr.press/v70/guo17a.html) дают базовый метод; он не гарантирует переносимость на новый язык, домен или изменённый набор вариантов.

**RLCD не является обязательным первым этапом.** Если истинные метки известны, прямое обучение распределений даёт более простой проверяемый baseline. RL имеет смысл исследовать позднее, если нужно обучение на результате взаимодействия и уже есть проверяемая среда, функция награды и сравнительное доказательство пользы. Называть собственный RL-эксперимент точной реализацией RLCD TypeSafe без опубликованной спецификации нельзя.

Высокая точность и хорошая калибровка — разные свойства. Корректировать чрезмерную уверенность недостаточно, если маленькая модель систематически не понимает документ. Тогда потребуется более сильная модель, другой контекст либо ограничение области автоматизации.

## Как получить скорость и сохранить смысл

Начать с обычного батча независимых вопросов; отсутствие декодирования уже убирает генерацию ответа. Затем профилировать повторную обработку состояния и использовать общий prefix/cache там, где это сохраняет вычислительную семантику. Для общего state и разных вопросов нужны изоляция строк или корректная маска внимания, чтобы вопросы не влияли друг на друга.

Один forward может обрабатывать большой батч; это не означает постоянную стоимость при росте числа токенов, вопросов и кандидатов. Длинный вход всё равно требует вычислений. Изменение порядка state/question, префиксные оптимизации, новый attention kernel и квантование проходят повторные проверки accuracy, калибровки и порядка вариантов. После квантования может потребоваться новая калибровка.

Измерять отдельно cold/warm start, tokenization, prefill, scoring, очередь, HTTP и p95 полного шага. Результаты на ускорителе автора проекта не являются SLO ноутбука Агат. Не обещать 100 мс до фиксации железа, длины входа, количества вопросов, кандидатов и параллельной нагрузки.

## Интеграция в Агат

AS-IS: [worker](../workers/agat_worker.py) использует обычный chat-completions вызов; [условия](../apps/coordinator/src/process-engine.ts) не различают ошибку JSON и отрицательный ответ. Подробное сопоставление уже приведено в [предыдущем анализе](./system-one-automation-assessment-2026-09-21.md).

Предлагаемый TO-BE: локальный model server на машине worker; профиль решения в существующем lease; coordinator принимает проверенный результат. Новый локальный endpoint не нужно публиковать в сеть. Ни обучение, ни inference не запускаются внутри deterministic Temporal Workflow.

Контракт должен хранить schema/model/tokenizer/prompt/calibration версии, полные распределения при наличии, происхождение confidence, input fingerprint и явные исходы `ok / abstain / error`. Версия набора вариантов входит в идентичность решения. При потере ответа допускается ограниченное повторное вычисление, но сохранённый принятый результат не переигрывается при восстановлении; внешние эффекты защищаются существующим runtime и idempotency.

Права, проверки данных, бюджеты и обязательные согласования остаются кодом. При недостаточной уверенности применяется разрешённая модель большего размера или человек; сбой локального исполнителя не разрешает неявную отправку данных наружу. Полные тексты не уходят в технические traces. Решения и evidence наследуют project ACL/retention, а не становятся общей памятью.

Rollout: offline → shadow без влияния на маршрут → ограниченная обратимая маршрутизация. Опубликованные версии и feature flag позволяют отключить новый профиль для новых запусков; активные сомнительные случаи уходят в штатную обработку. Смена модели, калибровки, квантования или кандидатов требует нового применимого eval.

## Ресурсы и оценка сроков

Для оценки предполагается один backend-разработчик, один ML-инженер и доступные предметный разметчик/QA; готовые открытые веса и существующий runtime Агат. Эти люди и оборудование не считаются уже выделенными.

| Этап | Ориентир при этих предпосылках | Результат |
|---|---|---|
| Baseline и минимальный локальный API | 1–2 недели после готовности первоначального benchmark | Сравнение кандидатов, память/latency, список ошибок, закреплённые версии |
| Предметное обучение, calibration, shadow-интеграция | Ещё 4–8 недель при доступной разметке | Прикладной пилот с воспроизводимыми результатами и fallback |
| Широкая универсальная модель уровня Jev | Срок и бюджет не определены | Отдельная исследовательская программа; оценка после первых learning curves и переноса на новые задачи |

Это инженерные оценки объёма, не результаты замеров и не обязательство срока. Подготовка данных и согласование разметки могут занять больше времени, чем вычислительное обучение.

Для CUDA-пилота с короткими входами и моделью 0,6–2B разумный **планировочный ориентир — один GPU с 24 ГБ VRAM**, с подбором batch/context и head/LoRA обучения по профилю памяти. Это не гарантия, что любое полное обучение или длинный контекст поместится. Кластер не требуется заранее для сравнения малых моделей. Для inference на Apple Silicon можно начать с подтверждённого проектом SemIf MLX-пути; объём unified memory и скорость конкретного Mac ещё надо измерить.

Оценка размера только весов: 2B при BF16 — приблизительно 4 ГБ, 4B — 8 ГБ; служебные тензоры, активации, cache, gradients и optimizer увеличивают расход. Итоговый бюджет оборудования определяется профилированием выбранной модели. Покупка GPU и запуск обучения в рамках этого ответа не выполняются.

## Приёмка и условия остановки

До эксперимента владелец процесса фиксирует допустимые ошибки по классам, требуемое автоматическое покрытие, максимальный контекст/батч, p95 и бюджет. Без этих значений можно сравнить кандидатов, но нельзя объявить их готовыми к автоматическим действиям.

Обязательные доказательства:

- accuracy/macro-F1 и ошибки по классам; для проверки отчёта — отдельно пропуски неподтверждённых утверждений и ошибки извлечения самих утверждений;
- Brier/NLL, reliability curve, ECE и ошибка среди принятых решений при фиксированном пороге; хорошо откалиброванная, но бесполезная модель не считается успехом;
- перенос на русский, новые документы и семейства вопросов; неизвестные варианты, изменённый порядок, длинный контекст и противоречия;
- регрессия после квантования/cache оптимизаций и повторные прогоны;
- отказ, timeout, отмена, retry, replay, отсутствие обхода policy и утечки между проектами;
- улучшение стоимости принятого результата/ручной работы относительно текущего процесса, включая обслуживание модели.

Правило из предыдущего анализа сохраняется: небольшой тест не доказывает ошибку ≤1%. При нуле ошибок примерно 300 независимых автоматически принятых случаев дают верхнюю одностороннюю 95% границу около 1%; связные утверждения одного документа не следует считать независимыми испытаниями.

Если нужное качество получается только на узком наборе, область применения ограничивается этим набором. Если дообучение не превосходит готовую модель или правила по выбранной метрике, обучение прекращается. Если оптимизация скорости ухудшает калибровку/качество, возвращается проверенная версия. При неясной метке решение передаётся на проверку, а не скрывается в среднем score.

Владельцы открытых gates: product/предметный эксперт — сценарии и цена ошибки; ML — данные, модель и калибровка; backend — контракт и leases; QA — независимый holdout; runtime/release owner — ресурсы, наблюдение и отключение. Конкретные назначения, объём потока и допустимые SLO остаются неподтверждёнными.

## Источники и проверка

Все внешние источники прочитаны 21.09.2026. Указанные README/model cards являются заявлениями авторов; их измерения здесь не воспроизводились. До запуска необходимо закрепить commit, revision весов, tokenizer и зависимости, поскольку проекты быстро меняются.

| ID | Источник | Поддерживаемый вывод |
|---|---|---|
| SRC-001 | [TypeSafe AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer), [GitHub](https://github.com/typesafe-ai) | Объявленный подход RLCD и граница публичного комплекта |
| SRC-002 | [SemIf](https://github.com/TheoLeeCJ/SemIf) | Baseline на готовой модели, прямые оценки, CUDA/MLX |
| SRC-003 | [decider](https://github.com/Mapika/decider), [model card](https://huggingface.co/Mapika/decider-2b) | Готовые веса, supervised pipeline, семантика confidence и ограничение воспроизводимости RL |
| SRC-004 | [NanoJev](https://github.com/TianyuCodings/NanoJev) | Пример decision heads, динамических кандидатов и открытого обучения |
| SRC-005 | [GLiClass](https://github.com/Knowledgator/GLiClass) | Классификационный baseline |
| SRC-006 | [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html) | Post-hoc temperature scaling |
| SRC-007 | [Hinton et al., 2015](https://arxiv.org/abs/1503.02531), [QLoRA, 2023](https://arxiv.org/abs/2305.14314) | Дистилляция и экономия памяти fine-tuning |
| SRC-008 | [Предыдущая оценка](./system-one-automation-assessment-2026-09-21.md), код Агат на `63bd93d` с рабочими изменениями | Реальный интеграционный контекст и ограничения |

Проверка исходного документа 21.09.2026: `architecture_audit.py --profile assessment` — PASS, 0 ошибок и 0 предупреждений. Repository link checker проверил 624 локальные ссылки в 101 Markdown-файле без ошибок; удалённые URL и anchors в этот счётчик не входят. В рамках того анализа был добавлен только документ, без benchmark, model deployment, модельных тестов и обучения. Последующая реализация и измерения отражены в разделе прогресса выше и связанных протоколах.
