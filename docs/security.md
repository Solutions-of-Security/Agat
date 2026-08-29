# Безопасность

## Реализовано

- coordinator отказывается слушать non-loopback адрес без admin token;
- стандартный enrollment token запрещён при non-loopback bind;
- node token случайный, в SQLite хранится только SHA-256 hash;
- сравнение секретов выполняется constant-time;
- dashboard mutation отделена от worker authorization;
- Kubernetes-панель использует Keycloak Authorization Code + PKCE S256; implicit и password grants отключены;
- coordinator самостоятельно проверяет RS256 signature/JWKS, issuer, audience/authorized party, срок действия и роли;
- роли `admin/designer/operator/viewer/auditor` проверяются на каждом маршруте, а project claim — повторно на backend;
- agents, runs, processes, credentials, events и artifacts изолированы по project ID;
- HTTP credentials зашифрованы AES-256-GCM с отдельным `AGAT_CREDENTIALS_KEY`; secret values никогда не возвращаются list API;
- process webhook использует отдельный project-scoped bearer token, показываемый только при create/rotation; в SQLite хранится SHA-256, а start receipt требует `Idempotency-Key`;
- non-GET process HTTP автоматически получает deterministic per-instance/node/visit idempotency key; compensation регистрируется только после подтверждённого source success и использует отдельный стабильный key;
- signal delivery ограничена process/project/name и optional instance/correlation key; payload и correlation проходят bounded validation;
- BPMN import ограничен 1 MiB, запрещает DTD/entities и создаёт только draft, который повторно проходит backend validation при публикации;
- создание и изменение system prompt агента требует роли `admin/designer` либо legacy admin token;
- произвольный browser Origin не получает CORS-доступ;
- CSP запрещает сторонние scripts, objects и embedding панели во frame;
- model API key остаётся на worker;
- upstream MCP endpoint и credentials остаются только в coordinator; worker получает lease-scoped schemas и вызывает tool через node-authenticated proxy;
- MCP servers создаются с deny-by-default legacy policy, annotations недоверенные по умолчанию, а versioned policy-as-code назначает risk tier/effect поверх более строгой legacy-границы;
- destructive и любой critical MCP-вызов требуют двух approvals от разных проверенных OIDC `sub`; один субъект не может подтвердить call повторно;
- MCP arguments/results шифруются AES-256-GCM, operator preview/diff редактирует sensitive keys, а audit хранит policy version/hash/rule, approvers и result hash/size;
- MCP credentials ограничиваются namespace/tool/risk/catalog/expiry, а глобальный persisted emergency deny повторно проверяется непосредственно перед upstream side effect;
- повтор MCP-вызова дедуплицируется по `lease_id + client_call_id`; retry stage блокируется после потенциального non-read side effect;
- A2A task API аутентифицируется отдельным project-scoped endpoint bearer до поиска task; token показывается один раз, а в SQLite хранится только SHA-256 hash;
- публичный A2A Agent Card минимален и не содержит system prompt, memory, model pin, tools или credentials; вход ограничен inline text/JSON, а выход — финальным text artifact;
- A2A `messageId` дедуплицирует запрос внутри endpoint, лимиты размера/активных tasks применяются до dispatch, а входное message шифруется `AGAT_CREDENTIALS_KEY`;
- model/hardware profiles считаются worker telemetry, нормализуются и ограничиваются coordinator; они влияют на scheduling, но не дают worker новых полномочий или доступ к чужому project data;
- knowledge collections/documents/memory изолированы по project; worker search ограничен collections snapshot активного stage lease, а vectors проходят лимит размерности и finite-number validation;
- RAG snippets и memory маркируются как недоверенный контекст, source/chunk provenance и hashes сохраняются в audit, а embedding запроса — только как SHA-256 и размерность;
- модель не получает произвольный HTTP-клиент: web-доступ ограничен `web_search`/`web_fetch`, публичными IP, портами 80/443, типами контента, размером, таймаутом и числом раундов;
- LangGraph runtime ограничен известным профилем `tool_loop_v1` и 1–12 model-итерациями; пользователь не загружает произвольный Python-код или graph module;
- поисковая строка и содержимое прочитанной страницы не записываются в журнал tool calls;
- скрытая chain-of-thought и provider-поля reasoning/thinking не записываются; trace содержит только наблюдаемые действия;
- полный trace и скачивание артефакта требуют authenticated dashboard access;
- пользователь задаёт только относительный каталог артефактов; абсолютные пути, traversal и symlink-сегменты отклоняются;
- скачивание принимает artifact ID, а не путь, и всегда отдаёт файл как attachment;
- worker открывает только исходящие соединения;
- approval gate стоит перед финальным этапом;
- body size ограничен 1 MiB; только authenticated Kong/coordinator routes загрузки knowledge document и embedding batch имеют отдельный лимит 8 MiB при доменном лимите текста 2 млн символов;
- systemd unit использует `NoNewPrivileges`, `ProtectSystem` и отдельный writable path.
- локальный worker launcher требует роли `admin` (либо legacy admin token), валидирует модель и лимиты и не принимает произвольный manifest/image/command;
- launcher service account ограничен namespace Role для Deployments, а создаваемые worker pods не получают Kubernetes token, запускаются non-root и с read-only root filesystem.
- Kong — единственный публичный API service: 1 MiB body limit, rate limit, correlation ID; Admin API выключен, `/api/v1/internal` закрыт отдельным маршрутом;
- Temporal internal tick дополнительно защищён отдельным случайным token и недоступен через Gateway;
- LangGraph не импортируется в Temporal Workflow и не использует отдельный durable checkpointer, поэтому graph state не создаёт ещё один backup/trust boundary;
- Keycloak PostgreSQL, Temporal, coordinator и workers запускаются non-root; root init используется только для одноразовой установки владельца конкретного PostgreSQL PVC с capabilities `CHOWN/FOWNER`.

## Production checklist

- [ ] Сгенерированы разные `AGAT_ADMIN_TOKEN` и `AGAT_ENROLLMENT_TOKEN` длиной не менее 32 случайных байт.
- [ ] Coordinator доступен только по HTTPS или внутри authenticated overlay-сети.
- [ ] SQLite volume шифруется средствами диска/хоста.
- [ ] Каталог `AGAT_ARTIFACTS_DIR` находится на шифруемом томе, имеет ограниченные права и входит в backup.
- [ ] SQLite knowledge store и JSON exports классифицированы как внутренние данные; export хранится и передаётся по защищённому каналу.
- [ ] Golden inputs, references, prompt versions, human rationale и judge outputs классифицированы как trace data; доступ и retention проверены.
- [ ] `.env`, worker credentials и model API keys не попадают в Git или logs.
- [ ] Reverse proxy ограничивает request rate и размер body.
- [ ] Keycloak переведён с `start-dev` на optimized production build, используется TLS и корпоративный IdP; demo-user удалён.
- [ ] Temporal dev server заменён на Temporal Cloud/полный self-hosted cluster с TLS/mTLS и внешней БД.
- [ ] Production Temporal workers используют immutable build ID, Worker Deployment Versioning и replay gate до ramp.
- [ ] Старые pinned worker builds не удаляются до завершения или безопасного Continue-As-New executions.
- [ ] Kong опубликован по TLS; при нескольких replicas rate limit использует Redis.
- [ ] `AGAT_ALLOWED_ORIGINS` содержит только реальные адреса панели.
- [ ] Enrollment token ротируется после подключения парка.
- [ ] Неиспользуемые node credentials отзываются пересозданием записи/ротацией token.
- [ ] Tools с внешними эффектами идемпотентны по `stage.id`.
- [ ] Process HTTP и compensation endpoints реально дедуплицируют переданный `Idempotency-Key`; обратная операция проверена на staging.
- [ ] Process webhook tokens сохранены в secret manager, ротируются и не присутствуют в logs/URL; producer повторяет один source event с тем же key.
- [ ] BPMN import проходит review/version diff перед публикацией; неподдерживаемые BPMN elements не считаются исполненными.
- [ ] Egress coordinator к MCP endpoints ограничен NetworkPolicy/firewall; HTTP transport не разрешён за пределами доверенной локальной сети.
- [ ] Для каждого MCP server проверены владелец, namespace, credentials, catalog и per-tool policy; `trustAnnotations` не включён автоматически.
- [ ] MCP policy candidate просмотрен через effective diff, опубликован с актуальным `baseSha256`, а critical tools не используют legacy local auth вместо двух OIDC-аккаунтов.
- [ ] MCP credentials имеют `kind=mcp`, минимальные namespaces/tool patterns/risks, ограниченный upstream IAM, expiry и проверенную rotation procedure.
- [ ] Дежурная смена умеет включить emergency deny, проверить `executingCalls`, остановить side effect во внешней системе и снять switch только с новой причиной.
- [ ] Для каждого A2A endpoint проверены владелец внешнего клиента, агент, публичное описание, knowledge collections, approval/limits; token передан через secret channel и имеет rotation procedure.
- [ ] `AGAT_A2A_PUBLIC_BASE_URL` совпадает с реальным HTTPS origin; Agent Card URL не публикуется как доказательство авторизации.
- [ ] Если оператор вручную включил LangSmith/сторонний tracing, настроены egress, redaction, retention и договорные основания передачи данных.
- [ ] Backup SQLite регулярно проверяется восстановлением.
- [ ] `agat-coordinator` RoleBinding не расширен за пределы локального namespace и Deployments.
- [ ] `AGAT_CREDENTIALS_KEY`, пароль Keycloak PostgreSQL и imported user passwords не «ротируются» заменой Secret без миграции данных.

## Kubernetes worker launcher

В локальном Docker Desktop контуре coordinator получает service-account token только для управления worker Deployments. Kubernetes RBAC не умеет ограничить `create` по label или префиксу имени, поэтому Role технически действует на Deployments всего namespace `agat`. Прикладной слой управляет только объектами с `agat.local/managed=true` и не предоставляет пользователю raw Kubernetes API. Не переносите этот RoleBinding в общий production namespace; выделите отдельный namespace или отдельный launcher service с admission policy.

Docker socket намеренно не монтируется. Docker Compose и обычный host-запуск coordinator не создают процессы по запросу браузера: без локального Kubernetes launcher возвращает явный статус `available: false`.

## Dashboard auth и RBAC

При `AGAT_OIDC_ENABLED=true` все dashboard API, включая overview, SSE, trace и artifact download, требуют Bearer access token. Browser adapter использует Authorization Code flow с PKCE, обновляет token до истечения и не кладёт access token в URL. CSP разрешает `connect-src` только same-origin и точный origin OIDC issuer.

`admin` управляет projects/scheduler/local workers; `designer` — agents, credentials и definitions; `operator` — launches, cancel и approvals; `viewer/auditor` — read-only. Точная матрица приведена в [identity и gateway](./identity-and-gateway.md).

При `AGAT_OIDC_ENABLED=false` сохраняется legacy local mode: изменяющие/чувствительные запросы используют `X-Agat-Admin-Token`. Этот режим предназначен для loopback/разработки и не заменяет SSO при сетевой публикации.

## Чувствительные данные в trace

Trace повышает наблюдаемость, но становится журналом внутренней информации. Не передавайте секреты в prompt; если это неизбежно, до production добавьте redaction/retention policy и RBAC. Экспортированный из браузера `trace.json` имеет ту же чувствительность, что input и output запуска.

Web tool arguments по умолчанию редактируются: сохраняется длина search query и host для fetch, но не полный запрос или URL path/query. Tool output представлен статусом, типом и размером; содержимое внешней страницы остаётся только в краткоживущем model context worker.

Artifact Store разрешает только UTF-8 text artifacts через текущий worker API и ограничивает количество/размер. Имена нормализуются, реальный storage filename для agent artifact получает случайный префикс, а UI скачивает файл по UUID metadata. `AGAT_ARTIFACTS_DIR` является trusted operator config; не разрешайте пользователю менять эту переменную.

## Trust boundary

Worker получает вход задачи и результаты предыдущих этапов, поэтому worker-host должен считаться доверенным для соответствующих данных. Local-first означает, что inference не уходит к внешнему model provider, но не означает автоматическую изоляцию разных внутренних команд.

Projects дают логическую backend-изоляцию и RBAC, но все проекты пока разделяют один процесс coordinator, SQLite-файл, Artifact Store, ключ шифрования и пул доверенных workers. Для жёсткого multi-tenant сценария нужны отдельные очереди/ключи/storage boundaries, scoped node labels, quotas и шифрование payload на уровне tenant.

## Local RAG и memory

Document text, chunks, memory и retrieval excerpts могут содержать коммерческую тайну, PII и инструкции злоумышленника. Local inference предотвращает отправку этих данных внешнему model provider только при условии, что сам model endpoint действительно локальный и контролируемый. Worker-host получает query, chunks и memory текущего проекта и потому входит в доверенную границу данных.

Indirect prompt injection остаётся возможным: системная политика worker запрещает выполнять инструкции из `[K…]/[M…]`, но модель может ошибиться. Не используйте RAG-ответ как автоматическое разрешение внешнего side effect. MCP write/destructive calls по-прежнему проходят policy/approval независимо от содержимого найденного документа.

Удаление collection каскадно удаляет её текущие chunks/vectors/jobs, но provenance уже выполненных runs остаётся в audit до удаления самих runs. Export содержит полный chunk text и memory, поэтому требует роли `admin/designer/auditor`, `no-store` response и той же защиты, что backup SQLite. Подробная модель lifecycle: [Local RAG, provenance и управляемая память](./local-rag-and-memory.md).

## Golden eval и prompt registry

Prompt versions, golden inputs, reference outputs, candidate outputs и reviewer rationale хранятся в project-scoped SQLite и возвращаются только authenticated API. Они не экспортируются в OpenTelemetry, но имеют ту же чувствительность, что полный run trace. `viewer` и `auditor` могут читать eval data; создание versions/promotion ограничено `admin/designer`, запуск — `admin/designer/operator`, append-only human review — также `auditor`.

Model judge выполняется обычным worker run. Это не создаёт cloud egress в coordinator, но выбранный model endpoint должен быть действительно локальным и доверенным. Judge получает candidate/reference content; не используйте внешний OpenAI-compatible endpoint без отдельного legal/security решения. Его score не является независимой истиной и уступает последней human review.

Promotion сверяет project, prompt/version, model, bound agent и PASS gates. Knowledge fingerprint обнаруживает drift, но не является защитой от содержательной prompt injection внутри зафиксированного документа. Approval/MCP policy не ослабляются даже при высоком eval score. Полный контракт: [Golden eval и prompt registry](./golden-eval-prompt-registry.md).

## A2A boundary

Agent Card доступен без bearer token и должен считаться публичным metadata-документом. UUID endpoint уменьшает случайное обнаружение, но не является контролем доступа. Все `Send/Get/List/Cancel Task` требуют отдельный endpoint token; coordinator проверяет его одновременно с `enabled=1` до чтения task, поэтому token одного endpoint или проекта не раскрывает существование task другого.

Token не возвращается list API, не сохраняется в браузере после закрытия one-time окна и при rotation немедленно инвалидирует старый hash. Это долгоживущий opaque secret: production-владелец обязан задать retention/rotation и защищённый канал выдачи. OAuth delegation, mTLS и short-lived credentials в 0.9 ещё не реализованы.

Adapter принимает только inline `text/plain` и явно включённый `application/json`; `raw`, URL/file parts, push callback и продолжение произвольной task запрещены. Выбранные knowledge collections принадлежат конфигурации endpoint и не управляются внешним request. Внешний ответ содержит только финальный text artifact и trace metadata — внутренние stage outputs, MCP calls, memory и files остаются за dashboard RBAC.

Вход A2A становится обычным run input, поэтому сохраняется в полном execution trace проекта. Зашифрованная копия исходного A2A message нужна для protocol history; обе формы имеют чувствительность пользовательского prompt. Входной `traceparent` валидируется, но trace ID не является секретом и не даёт доступ к `/api/v1/runs`. Подробности: [A2A adapter](./a2a-adapter.md).

## MCP и tools

MCP-интеграцию нельзя подключать как неограниченный shell. Coordinator АГАТ является host и применяет consent/risk policy до upstream `tools/call`; token passthrough отсутствует. Реализация закрепляет современную ревизию, не делает silent downgrade и не исполняет `input_required` автоматически. См. [tools 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) и [отдельное описание gateway](./mcp-gateway.md).

В 1.1 реализованы:

- allowlist server/tool;
- risk-классы чтения, записи, destructive и unknown плюс tiers `low/elevated/high/critical`;
- immutable project policy-as-code с preview и optimistic hash activation;
- approval непосредственно перед side effect и distinct four-eyes для critical/destructive;
- scoped credentials и запрет MCP-secret в HTTP process steps;
- redaction секретов в traces/operator diff и глобальный admin-only emergency deny.

Legacy server/tool policy не заменена: её `deny` остаётся абсолютным, а `approval` — нижней границей. Актуальная policy, lease и kill switch перепроверяются до secret injection/upstream `tools/call`. Однако kill switch не может отозвать HTTP-запрос, уже принятый внешним MCP server; такой incident требует внешней остановки/компенсации. Один `local-admin` субъект также не удовлетворяет four-eyes — production critical approvals требуют OIDC.

Произвольные PII-поля автоматически не классифицируются: для них нужны project-specific schema/data-classification rules. До их появления не передавайте персональные данные в MCP arguments без необходимости и ограничивайте доступ к overview/audit через RBAC.

Egress NetworkPolicy/firewall, полная schema validation arguments на стороне host и sandbox/microVM для локальных STDIO servers остаются production-hardening этапами. STDIO subprocess намеренно не поддерживается. Полный контракт: [Risk-tier approvals и emergency deny](./mcp-risk-tier-approvals.md).

## Temporal production boundary

- `local` допускает незашифрованный loopback/ClusterIP `start-dev`; `cloud` и `self-hosted` fail-closed требуют TLS и authentication;
- API key используется только вместе с TLS, mTLS cert/key принимаются только полной парой и читаются из mounted files;
- Activity API защищён отдельным `AGAT_TEMPORAL_INTERNAL_TOKEN`; он не совпадает с Temporal API key;
- health snapshot и rollout argv не содержат secrets;
- production worker требует immutable build ID и Worker Deployment Versioning;
- replay fixtures проходят sanitization: пользовательские payloads и credentials не коммитятся в repository;
- SQLite ограничивает deployment одним coordinator; попытка включить незавершённый PostgreSQL driver отклоняется.

Runbook и список параметров: [Production hardening durable runtime](./production-durable-runtime.md).

## Web tools

Реализованный `web_fetch` сначала требует точное совпадение URL с результатом `web_search` текущего этапа или URL, явно указанным пользователем во входе запуска. Allowlist не разделяется между параллельными этапами. Затем reader проверяет исходный URL и каждый redirect, разрешает DNS и отклоняет весь hostname при наличии хотя бы одного непубличного адреса. Это прикладной барьер против prompt-driven exfiltration и SSRF, но production-контур должен дублировать его egress firewall/proxy с запретом loopback, RFC1918, link-local, metadata и внутренних сетей. Kubernetes NetworkPolicy полезна только при CNI, который реально реализует её egress-правила.

HTML и результаты поиска считаются недоверенным вводом. Worker удаляет исполняемую/служебную разметку, ограничивает текст и добавляет модели явный запрет следовать инструкциям страницы. Это уменьшает, но не устраняет prompt injection; будущие инструменты записи или иных side effects должны требовать отдельный approval после чтения web-контента.

Inference остаётся локальным, однако поисковая строка уходит в SearXNG и выбранные им внешние поисковые системы, а `web_fetch` обращается к исходному сайту. Не отправляйте в search секреты, персональные данные и внутренние идентификаторы.
