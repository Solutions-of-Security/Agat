# Задачи развития агентной работы по сравнению от 17.10.2026

Основание: [рынок, исследования и AS-IS](./agent-market-and-research-2026-10-17.md). **Источник истины для приоритетов и последовательности — [roadmap](./roadmap.md)**. Этот документ раскрывает восемь новых задач и изменения существующих, не создавая второй независимый план. Все новые задачи имеют статус **запланировано**, реализация и проверки ещё не выполнены.

## Правила планирования

P0 — минимум для принятого report-сценария; P1 — повторное использование и развитие после первого пилота. Gate безопасности применяется перед соответствующим действием независимо от очереди. S/M/L — относительный инженерный размер с неопределённостью: S — одна ограниченная поверхность, M — несколько связанных компонентов, L — изменение контрактов/state/runtime с миграцией. Размер не переводится в дни без подтверждённой команды. Роли ниже — потребность в ответственности, а не назначенные люди.

Вход каждой задачи: назначенный инженерный владелец, зафиксированный scope и доступные fixtures. Выход: acceptance evidence и ограничения в `/docs`, code review и соответствующие проверки. За бизнес-приёмку отвечает владелец процесса, за runtime — оператор, за выпуск — release owner. До назначения конкретных людей работы не считаются календарным обязательством.

## Новые задачи

| ID | Приоритет / размер | Результат | Зависимости | Ответственная роль |
| --- | --- | --- | --- | --- |
| [EVAL-103](#eval-103) | P0 / M | Измеренный выбор workflow/single/chain/team для первого pack | EVAL-101 и существующие runtime profiles | Agent/eval + QA + владелец процесса |
| [SAFE-101](#safe-101) | P0, по включаемой поверхности / M | Негативный release gate для контекста, памяти, tools и egress | SP-002, текущие policy/MCP; CLD-002 только для cloud-набора | Backend/security + QA |
| [EVAL-104](#eval-104) | P1 / L | Ошибка → human review → regression example → affected eval | EVAL-101, USE-004, UX-306 | Agent/eval + backend + QA |
| [CTX-101](#ctx-101) | P1; до long research / M | Структурированные claims, источники и зависимости вывода | SP-002, USE-003, минимальный RUN-103 | Runtime/data + frontend + QA |
| [MEM-101](#mem-101) | P1; до расширения постоянной памяти / L | Типизированная память с исправлением, отзывом и измерением пользы | Минимальный RUN-101, текущая memory; SAFE-101 как regression suite | Backend/runtime + data owner + QA |
| [RUN-104](#run-104) | P1; до расширенной делегации / M | Версионированное задание и проверяемый результат специалиста/peer | SP-002, EVAL-103, существующие team/A2A contracts | Runtime + backend + QA |
| [RUN-105](#run-105) | P1; до интерактивных долгих этапов / L | Clarify/steer/pause/resume с подтверждённой версией намерения | USE-005, RUN-102, existing Temporal Update/signal | Runtime + frontend + QA |
| [UX-308](#ux-308) | P1 / M | Доставленное уведомление о необходимом решении или результате | UX-111, USE-005, существующие inbox/notifications | Frontend/backend + UX + QA |

<a id="eval-103"></a>

### EVAL-103 — выбрать профиль исполнения по evidence

**Зачем:** получить пригодный отчёт без неоправданной цены координации. Источники SRC-201/SRC-203 и существующий specialist runtime. Точки изменения: [Golden eval](./golden-eval-prompt-registry.md), [worker](../workers/agat_worker.py), manifest pack. Новый оркестратор не требуется.

**Приёмка:**

- На одном зафиксированном корпусе EVAL-101 сравниваются применимый deterministic baseline, workflow с одним agent stage, текущая фиксированная цепочка трёх ролей и `specialist_team_v1`. Неподдержанные deterministic случаи отмечаются явно; сравнение делается по общим поднаборам и полному знаменателю отдельно.
- Для стохастических профилей выполнено минимум три trials на случай; права, исходное состояние, доступные данные и общий лимит tokens/tools/cost/time на задачу сопоставимы. Local и cloud результаты представлены раздельно.
- Сохранены pass/fail конечного состояния, качество источников/чисел, abstention, нарушения policy, handoffs, повторы, p50/p95 времени, usage и полные доступные затраты. Для чисел по возможности используется кодовый oracle, а не второй LLM.
- Ошибки размечены как минимум по design, handoff/misalignment, evidence, verification, budget и recovery. Доля случаев с тремя успешными trials показана отдельно от доли хотя бы одного успеха.
- Выбор профиля закреплён в новой версии pack с обоснованием. Критерий и quality margin заданы до опыта. Если superiority не доказана или доверительный интервал слишком широк, остаётся простой профиль; нет автоматического объявления team победителем.

**Выходное evidence:** versioned dataset manifest, протокол конфигураций, task/trial результаты, парное сравнение по task clusters, решение владельца процесса. **Откат:** прежний pack/profile для новых запусков; старые manifests не меняются.

<a id="safe-101"></a>

### SAFE-101 — квалифицировать недоверенный контекст и агентные действия

**Зачем:** новые tools, cloud и memory не должны обходить имеющиеся ограничения. Основание SRC-205 и data-policy подходы SRC-105; это продуктовый regression gate, не заявление о найденной уязвимости. Точки изменения: существующие MCP/data policy, retrieval и eval harness.

**Приёмка:**

- Versioned fixtures покрывают инструкции в документе, tool description/result, peer output и памяти; подмену источника, cross-project retrieval, запрещённый recipient, изменение policy после preflight, revoked credential и истёкшее approval.
- Проверяется действительный результат на изолированном fake upstream: число и аргументы вызовов, отсутствие запрещённой записи/отправки, состояние approval и audit. Текст «не буду выполнять» сам по себе не является pass.
- При недоступном обязательном policy/validator действие блокируется; timeout/unknown outcome не маркируется успешно и не вызывает слепой повтор записи.
- Удалённая/отозванная память не используется в следующем запуске. Секреты и payload fixtures не попадают в общую telemetry; тесты используют синтетические данные.
- Gate содержит ноль запрещённых эффектов в проверенном наборе и проходит benign control cases. Это граница набора, а не доказательство нулевого production-риска. Local read/report проходит свой набор; cloud/tool/memory изменения расширяют его до включения.

**Выходное evidence:** case IDs, наблюдаемые эффекты, redacted audit, версия policy/инструмента/модели. **Откат:** выключить новый профиль/tool/recipient через существующий control plane, сохранить расследуемое состояние и метаданные.

<a id="eval-104"></a>

### EVAL-104 — замкнуть цикл качества и актуальности evidence

**Зачем:** ошибки пилота должны превращаться в регрессию; смена reviewers или версии не должна выглядеть улучшением модели. Основание SRC-103/SRC-202/SRC-209/SRC-210. Точки изменения: Golden eval, append-only reviews и принятие результата USE-004.

**Приёмка:**

- Из результата «Нужны правки», ошибки источника или policy failure создаётся кандидат regression case со ссылкой на исходный run, classified failure и exact versions. Prompt/output не копируются автоматически из telemetry; данные включаются только по разрешённой процедуре очистки.
- Человек подтверждает expected outcome и пригодность данных; случай получает новую immutable dataset version. Закрытый holdout не переходит в разработку без создания нового holdout и фиксации причины.
- Для model judge и reviewer rubric существует перекрывающийся human calibration set, disagreement report и процедура adjudication. N/A/непроверенное не превращается в pass.
- Изменение model alias, prompt, tool schema, corpus, memory policy или runtime помечает затронутое evidence как требующее перепроверки. Freshness зависит от изменений и выбранного риска; фиксированный TTL из чужого исследования не копируется.
- Online sampling ограничен бюджетом/retention, не выполняет повторно внешние side effects и сохраняет связь с принятием, rework и фактическим исходом задачи. Его отключение не останавливает основной процесс, но статус качества становится unknown.

**Выходное evidence:** демонстрация одного полного error-to-regression цикла, calibration report и promotion/rollback при stale evidence. **Откат:** отключить sampling, оставить ручные reviews и существующий offline gate; ранее собранные данные обрабатываются по retention policy.

<a id="ctx-101"></a>

### CTX-101 — сохранить основания длинного исследования

**Зачем:** пользователь должен видеть, на чём основан существенный вывод, включая промежуточные зависимости. Основание SRC-207; существующие citations переиспользуются. Точки изменения: structured output SP-002, Artifact Store, [knowledge citations](../apps/coordinator/src/knowledge-citations.ts), result view.

**Приёмка:**

- Сохраняется ограниченный versioned объект `claimId, text, sourceRefs, dependsOn, status`, где status различает supported/conflicting/insufficient. Source reference содержит document/chunk/version/hash и доступную страницу или ячейку; DOCX не получает выдуманную страницу.
- Детерминированно отклоняются неизвестные ссылки, циклы зависимостей, превышение размера и ссылки вне project/run scope. Наличие ссылки и достоверность утверждения оцениваются раздельно.
- Противоречивый/недостаточный upstream claim помечает зависимый вывод для review; отсутствие промежуточного evidence не маскируется красивым финальным текстом.
- UI открывает ровно разрешённый источник/фрагмент и показывает ограничения extraction. При отзыве доступа содержание недоступно, историческая ссылка не выдаёт payload.
- На длинных cases EVAL-101 измеряются intermediate correctness, final correctness и время проверки человеком относительно обычных citations. Полный graph engine/новая БД для MVP этой задачи не нужны.

**Выходное evidence:** schema, corpus fixtures, graph validation и human comparison. **Откат:** обычные citations; новые поля читаются как optional, payload сохраняется по общей data policy.

<a id="mem-101"></a>

### MEM-101 — управлять памятью и её исправлением

**Зачем:** повторные задачи используют проверенные актуальные сведения, а пользователь может их исправить или отозвать. Основание SRC-204/SRC-205/SRC-208, [существующие working/episodic memory](./local-rag-and-memory.md).

**Приёмка:**

- Отделены transient task state, утверждённое episodic evidence и разрешённые user/project preferences. Для entry заданы owner/scope, purpose, writer, source, version, срок/правило хранения и статус. Данные из retrieval/peer не становятся policy/instruction.
- Модель предлагает запись; commit разрешает код по writer policy и необходимости human confirmation. Для справочного общего знания доступ read-only по умолчанию. Отдельного разрешения на tools запись памяти не даёт.
- Исправление создаёт новую версию, старая помечается superseded; конфликт не перезаписывает доверенное значение молча. Delete/revoke исключает содержание из будущего retrieval и cache; повтор после restart не восстанавливает его.
- Retention учитывает embeddings, summaries и производные caches. Исторический audit сохраняет только разрешённые метаданные; срок хранения содержания и исключения утверждает data owner до rollout.
- Проверены scope isolation, stale/conflicting preferences, poisoned entry и concurrent update. On/off опыт на одинаковых повторных задачах показывает качество и стоимость construction/read/update; при отсутствии пользы память остаётся выключенной для этого профиля.

**Выходное evidence:** lifecycle contract, миграция старых записей без автоматического расширения scope, delete/correction tests и repeated-task eval. **Откат:** запрет новых writes и исключение новых типов из context; старые записи не переинтерпретируются как утверждённые preferences.

<a id="run-104"></a>

### RUN-104 — задать контракт делегации и проверки peer output

**Зачем:** координатор передаёт ограниченную работу и может проверить результат, независимо от внутреннего specialist или внешнего A2A peer. Основание SRC-201/SRC-202, [существующая team schema](./langgraph-specialist-teams.md), A2A foundation.

**Приёмка:**

- Versioned envelope включает task/parent ID, цель, разрешённый input/evidence scope, output schema, deadline, лимиты и terminal statuses. Result содержит status, claims/source refs, artifact refs, ограничения и usage, когда он доступен; unknown usage явно обозначен.
- Права вычисляются существующим control plane и не расширяются полями envelope, prompt или ответом peer. Aggregated fan-out/handoffs/tokens/time/cost проверяется у родителя; дочерний budget не создаётся заново при retry.
- Результаты dedupe по task/attempt/version. Cancel/revoke, просроченный результат, отказ части участников, противоречия и невалидная schema обрабатываются явно; incomplete не становится completed.
- Existing `specialist_team_v1` и A2A wire contract продолжают читаться. Adapter нормализует envelope, не объявляя произвольный REST agent A2A-совместимым. Nested unbounded delegation не включается.
- Проверены compromised/ошибочный peer, duplicate/late results и restart coordinator; финальная ответственность и human approval остаются у родительского процесса.

**Выходное evidence:** schema/migration fixtures, contract suite и ограниченный read/draft E2E. **Откат:** существующий bounded team либо отключение нового remote profile для новых задач, с сохранением связи с активным remoteTaskId.

<a id="run-105"></a>

### RUN-105 — вмешиваться в долгую работу без потери намерения

**Зачем:** пользователь может дать существенное уточнение или остановить работу и знать, когда изменение применено. Основание session steering SRC-106 и acquisition-vs-recall SRC-208. Точки изменения: process/runtime state, Temporal Updates/signals, Run detail. Новый параллельный chat/state store не создаётся.

**Приёмка:**

- Команда имеет command ID, expected intent revision и actor; ответ различает accepted, applied, rejected/stale. Журнал фиксирует revision, с которой продолжился следующий безопасный шаг.
- Clarification имеет ограниченные поля, срок, показываемую причину и состояние ожидания; новый ввод не выдаёт разрешение на side effect и не снимает policy/approval.
- Pause/cancel прекращает новые действия после подтверждённой границы; уже принятый внешним сервисом запрос сверяется отдельно. Нельзя обещать отмену удалённого вычисления или возврат оплаты без подтверждения.
- Проверены steering до/во время/после tool effect, duplicate Update, restart, concurrent editor и поздний ответ. После изменения разрешённого действия его approval перепроверяется.
- UI показывает текущую цель, применённую версию, сохранённый progress и оставшуюся работу. Resume использует RUN-102 checkpoint contract; unsupported legacy stage предлагает безопасный restart вместо ложного resume.

**Выходное evidence:** state/command contract, replay-compatible histories и операторский E2E. **Откат:** запрет новых interactive commands для legacy профилей; активное ожидание переводится в документированный cancel/handoff.

<a id="ux-308"></a>

### UX-308 — доставлять внимание к решению

**Зачем:** пользователь замечает ожидание и возвращается к точному run/approval без постоянной проверки панели. Основание SRC-102 и локальное исследование Coucou от 1 октября (исходник не опубликован в этом срезе). Уже существующие notification center/inbox переиспользуются.

**Приёмка:**

- Один выбранный для пилота канал, например opt-in browser/PWA notification, сообщает о required action, error и готовом результате. Доставка дедуплицируется по event ID; reconnect/restart не создаёт шквал дублей.
- Уведомление ведёт к точному объекту, проверяет текущий доступ и актуальное состояние. Сообщение не содержит секреты, названия закрытых документов или содержимое отчёта; click не исполняет действие автоматически.
- TTL, resolved и revoked доступ отражаются при открытии; ответ через другой браузер не оставляет исполнимое старое approval. UX-111 остаётся обязательным для deadline/races.
- Есть mute/quiet hours и понятное включение канала. Недоступность доставки видна в интерфейсе, основной inbox остаётся доступен.
- До пилота задан критерий: снижение median времени реакции минимум на 20% без роста ошибочных решений и лишних прерываний относительно inbox. Порог — проверяемая гипотеза; при отсутствии эффекта расширение каналов прекращается.

**Выходное evidence:** delivery/role/race tests и протокол оператора. **Откат:** выключить канал; серверные состояния и решения остаются в прежнем API. Отдельный native tray/маскот не входит в задачу.

## Изменение существующих задач

| ID | Что меняется | Почему / приёмка остатка |
| --- | --- | --- |
| SP-001, SP-005 | Статус «частично»: fixed manifest и idempotent install уже существуют; следующий scope — общий lifecycle, diff/update/rollback | Сохранить совместимость `internal-report v1`, digest и history; новый pack version не меняет активный instance |
| SP-102, SP-101 | Сохранить поставленные три роли и текущую цепочку как baseline; следующий профиль выбирается EVAL-103 | Наличие трёх шаблонов не требует трёх model calls для каждого входа; не менять старую опубликованную версию |
| USE-003, UX-206 | PDF/DOCX extraction и provenance переиспользовать; уточнить интеграцию pack, preview, CSV/XLSX и incomplete input | Нет повторной разработки parser. Сканы/изображения остаются отдельным DOC-101; нет выдуманных страниц DOCX |
| USE-004, RUN-103 | Минимальные schema/формализованные расчёты/source IDs/artifact validators — P0; semantic claims оцениваются отдельно; bounded model repair — P1 | Сбой validator блокирует выдачу «проверено». Принятие человеком и технический completed — разные события |
| RUN-101 | P0: context estimate/reserve/limit и явный overflow; P1: compaction, dynamic skills/tool discovery | Compaction сохраняет цель/ограничения/evidence; benefit проверяется on/off, scopes остаются у gateway |
| EVAL-101 | Повторные trials, holdout, trajectory/outcome, severity, abstention; local path без обязательной зависимости от CLD-005 | Раздельные профиль/версии и human calibration; прошлый synthetic PASS не повышается до общего quality badge |
| EVAL-102 | Сравнивать с одним выбранным прямым конкурентом: Dify/n8n по сценарию; Portable Computer — дополнительный local-first comparator | До опыта фиксировать версии/редакции, права, настройки и setup effort; отдельные режимы и стоимость принятых результатов |
| CLD-006, UX-207 | Полный remote-agent UX — P1 после базового report path; P0 UX подключений ограничить нужными models/tools | OpenRouter остаётся первым планируемым inference adapter; общие policy/budget contracts не ослабляются |
| CLD-007 | Advisor — P1 эксперимент с минимальным context и парным baseline | Нет скрытого local→cloud fallback; доказать incremental quality/cost benefit |
| USE-008, USE-009 | Сначала выбранный профиль установки и corpus/ручной baseline; дополнительные режимы квалифицируются отдельно | Назначить две команды и владельца; две недели пилота считаются от готовности, а не обещанием даты выпуска |

Конкретные строки этих задач изменены в roadmap. DOC-101, RAG-101, дальнейшие business packs, production OPS и watchlist сохраняются с прежними entry conditions. Экспериментальные локальные classifiers остаются в отдельном qualification-плане: этот backlog не включает automatic routing и не отменяет `not_qualified`.

## Первая очередь и выходные gates

1. Подготовить USE-008/EVAL-101: владелец, реальные разрешённые данные, ручной baseline, rubric и главный критерий.
2. Зафиксировать SP-001/SP-002; довести выбранный local путь. Для cloud параллельно нужны CLD-001–CLD-005 и budget/data gates; local пилот их не ожидает.
3. Завершить USE-003/USE-004 и минимальные RUN-101/RUN-103; расширить EVAL-101 и выполнить соответствующий SAFE-101.
4. EVAL-103 выбирает профиль SP-102, затем USE-001/USE-009 и UX-306/UX-307 закрывают пользовательский путь.
5. Провести USE-008; после evidence повторной пользы брать P1. Для внешнего peer сначала RUN-104 и CLD-006, для долгих sessions — RUN-102/RUN-105, для memory expansion — MEM-101.

Прохождение документационных проверок означает готовность задач к инженерной оценке. Оно не означает выполнение этих gates или принятие production-риска.
