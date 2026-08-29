# Эксплуатация

## Health

```bash
curl -fsS http://127.0.0.1:8787/api/v1/health
npm run k8s:status
```

В Kubernetes health должен содержать `processRuntime.mode=temporal` и `connected=true`. Ответ через localhost обязан иметь `Server: kong/...`; `k8s:status` считает прямой stale coordinator на том же порту ошибкой, а не здоровым Gateway.

Узел считается `sleeping` после 90 секунд без heartbeat и `offline` после 300 секунд. Активный lease продлевается worker каждые 45 секунд; стандартный TTL — 180 секунд.

## Temporal rollout и replay

Перед каждым production worker build выполняются `npm run typecheck`, `npm test` и `npm run build`. `npm test` включает replay сохранённых Temporal histories. Новый immutable build сначала запускается как canary, затем получает ограниченный ramp и только после наблюдения становится current:

```bash
AGAT_TEMPORAL_BUILD_ID=git-abc123 npm run temporal:version
AGAT_TEMPORAL_BUILD_ID=git-abc123 AGAT_TEMPORAL_RAMP_PERCENTAGE=5 npm run temporal:ramp
npm run temporal:status
AGAT_TEMPORAL_BUILD_ID=git-abc123 npm run temporal:promote
```

Старый worker нельзя масштабировать в ноль, пока на нём остаются pinned executions. Полный runbook подключения, rollback и drain: [Production hardening durable runtime](./production-durable-runtime.md).

Разделы панели читают один snapshot `/api/v1/overview` и обновляют его по SSE либо раз в 15 секунд. Поэтому счётчики агентов, моделей и узлов отражают SQLite и свежесть heartbeat, а не локально сгенерированное UI-состояние.

При открытом запуске панель отдельно получает authenticated `/runs/:id/trace`. Вкладка показывает до 10 000 событий, а `trace.json` можно выгрузить из браузера. Если видны только краткие события overview, проверьте OIDC-сессию/роль (или legacy admin token) и ответ этого endpoint.

Вкладка **Replay / Eval** использует тот же trace endpoint: отдельный polling-сервис не нужен. OTLP export опционален и не является источником истины. Для его включения и диагностики см. [OpenTelemetry, execution manifest, replay и eval](./observability-replay-evals.md).

## Live и demo режимы

`AGAT_SEED_DEMO=false` — production-default. `true` допустим только для презентации на отдельной базе. При первом live-запуске после обновления coordinator удаляет известные demo-fixtures; пользовательские агенты, узлы и запуски сохраняются.

## Backup SQLite и артефактов

Coordinator использует WAL. Для согласованного backup предпочтителен SQLite online backup или остановка container перед копированием volume. Простое копирование только `agat.db` во время записи может потерять данные из `-wal`.

Artifact metadata находится в SQLite, а содержимое — в `AGAT_ARTIFACTS_DIR` (по умолчанию `./data/artifacts`, в контейнерах `/data/artifacts`). База и этот каталог должны восстанавливаться как один согласованный набор.

Knowledge collections, исходные chunks, embedding vectors, memory и retrieval provenance целиком находятся в SQLite. Отдельного vector volume нет. JSON export удобен для переноса содержимого и audit, но не заменяет backup: vectors в export не включаются и воспроизводятся повторной локальной индексацией.

A2A endpoint registry, token hashes и task mapping также находятся в SQLite; исходные protocol messages зашифрованы `AGAT_CREDENTIALS_KEY`. После восстановления сохраните тот же encryption key, иначе history нельзя расшифровать. Полные endpoint tokens из hash восстановить невозможно: если client secret утрачен или backup откатил rotation, выполните новую rotation в панели.

Минимальный безопасный сценарий для небольшого локального контура:

1. Остановить coordinator.
2. Скопировать `agat.db`, `agat.db-wal`, `agat.db-shm` и весь каталог артефактов как один набор.
3. Запустить coordinator.
4. Периодически разворачивать копию в отдельном окружении, выполнять `PRAGMA integrity_check` и скачивать выборочные artifacts через API для сверки SHA-256.

В текущей версии нет автоматического retention/garbage collector. Не удаляйте файлы вручную по glob: сначала сверяйте их с таблицей `artifacts` и делайте проверенный backup.

В Kubernetes согласованный disaster-recovery набор шире:

- `agat-data`: основной SQLite + Artifact Store;
- `agat-temporal-data`: history/timers локального Temporal dev server;
- `agat-keycloak-postgres`: users, roles, sessions и realm state;
- Secret `agat-secrets`, сохранённый в защищённом secret manager/зашифрованном backup;
- definitions управляемых worker-пулов в Kubernetes API.

Не восстанавливайте только одну из трёх state-систем в произвольную дату: SQLite instance и Temporal history могут разойтись. Для production используйте поддерживаемую внешнюю БД Temporal/Keycloak и согласованные snapshot/restore procedures.

## Восстановление durable process

После рестарта coordinator перечитывает активные `runtime=temporal` instances и идемпотентно получает/запускает соответствующие Workflow IDs. Temporal worker replay-ит историю, а SQLite восстанавливает execution tokens, join arrivals, external signal waits, embedded subprocess links и compensation stack. Durable `wait` не превращается в polling-таймер coordinator. Завершение lease, approval, внешний signal и cancel отправляют подтверждаемый Update с Signal fallback; периодическая сверка остаётся fallback на случай краткого сбоя transport.

Проверка runtime:

```bash
kubectl logs -n agat deployment/agat-temporal-worker --tail=100
curl -fsS http://127.0.0.1:8787/api/v1/health
```

Workflow конкретного instance открывается по ссылке **Temporal** в таблице запусков процесса либо в `http://127.0.0.1:8233`.

## Потеря worker

Ничего вручную возвращать в очередь не нужно. После TTL coordinator переводит `running → queued`. Non-GET process HTTP и compensation автоматически получают стабильный key на весь retry одного stage, но at-least-once риск исчезает только если upstream действительно дедуплицирует этот header. MCP gateway дополнительно блокирует автоматический retry stage, если non-read call достиг `executing/completed/failed`: это предотвращает наиболее опасный повтор, но не заменяет идемпотентность upstream системы.

Если start webhook вернул `503` после создания receipt, повторите тот же запрос с тем же Bearer token и `Idempotency-Key`: coordinator повторно использует тот же instance и идемпотентно запускает его в Temporal. Не генерируйте новый key при transport retry одного source event.

A2A task является обычным run, поэтому потеря worker обрабатывается тем же lease TTL. Внешний клиент продолжает polling той же task ID; повторный `message:send` с тем же `messageId` и payload не создаёт второй run.

Для LangGraph повторяется весь внутренний graph attempt: per-node checkpoints намеренно не сохраняются отдельно от stage. Текущие web-tools read-only; будущие write-tools должны дедуплицироваться как минимум по `stage.id`.

Embedding job использует такой же pull lease и максимум три попытки. После потери worker просроченная job возвращается в `pending`; уже принятый batch не пересчитывается. Если документ перешёл в `failed`, удалите его и загрузите заново после исправления модели/endpoint. Изменение размерности одной embedding-модели внутри существующей collection отклоняется — создайте новую collection и переиндексируйте документы.

## Ротация worker token

Повторная регистрация узла с тем же `name` и действующим enrollment token выдаёт новый node token и инвалидирует старый. Удалите локальный credentials file и перезапустите worker.

В Docker Desktop Kubernetes актуальный enrollment token выводится командой `npm run --silent k8s:enrollment-token`. Он отличается от legacy admin token для режима без OIDC.

При включённом OIDC браузер использует Keycloak, а legacy admin token не является login password. Пароль локального `agat-admin` выводится `npm run --silent k8s:keycloak-password` и меняется через Keycloak. Не заменяйте Secret вручную: импортированный пользователь от этого не обновится.

## Изменение производительности

- слабая машина: `AGAT_WORKER_CONCURRENCY=1`, global `sequential`;
- несколько независимых GPU: задайте лимит каждого worker и global `parallel`;
- ноутбуки: global `auto`, передавайте battery env metrics при наличии интеграции ОС;
- неоднородный fleet: оставьте модель агента пустой, накопите реальные samples и выберите policy в разделе **Модели**; coverage и причины решения сверяйте по `routing.selected`;
- длинный inference: увеличьте `AGAT_LEASE_TTL_SECONDS`, если heartbeat network нестабилен, но оставьте renew включённым.

Model Router использует benchmark не старше 30 дней. Если throughput выглядит устаревшим, выполните репрезентативные stages на каждом worker; synthetic warmup при старте намеренно отсутствует. Настройка capabilities, VRAM и energy описана в [руководстве Model Router](./model-router.md).

Локальные worker-пулы можно создавать и останавливать в разделе **Узлы**. Остановка масштабирует управляемые Deployments до нуля; `npm run k8s:stop` делает то же самое для всех объектов с label `agat.local/managed=true`. Повторный `npm run k8s:up` сохраняет определения пулов, после чего их можно возобновить кнопкой **Запустить**.

## Диагностика

```bash
npm run typecheck
npm test
npm run build
python3 workers/agat_worker.py --help
python3 -m py_compile workers/agat_worker.py workers/web_tools.py workers/telemetry.py
PYTHONPATH=workers python3 -m unittest discover -s workers -p 'test_*.py' -v
```

Для проверки реального LangGraph-пути сначала установите закреплённую зависимость. В разделе **Узлы** ожидаются runtime `single` и `langgraph`; если показан только `single`, worker безопасно не получит LangGraph-stage:

```bash
python3 -m pip install --requirement workers/requirements.txt
PYTHONPATH=workers python3 -m unittest \
  workers.test_web_tools.ModelToolLoopTests.test_langgraph_runtime_executes_bounded_model_tool_graph -v
```

Dry-run проверяет coordinator без model server:

```bash
python3 workers/agat_worker.py --once --dry-run
```

Для диагностики web-инструментов сначала проверьте `agat-search`, затем worker. Ошибка поиска возвращается модели как tool result и также появляется в журнале этапа без текста запроса:

```bash
kubectl get deployment,pod,service -n agat
kubectl logs -n agat deployment/agat-search --tail=100
kubectl logs -n agat deployment/agat-worker --tail=100
kubectl logs -n agat deployment/agat-gateway --tail=100
kubectl logs -n agat deployment/agat-keycloak --tail=100
kubectl logs -n agat deployment/agat-temporal-worker --tail=100
```

Для MCP сначала проверьте `lastError` и время catalog sync в разделе **MCP**, затем coordinator log. Worker никогда не соединяется с MCP endpoint напрямую. `waiting_approval` требует решения в карточке запуска; `expired` означает, что lease или approval TTL закончился. Полные arguments/results в logs и overview намеренно отсутствуют — сверяйте `callId`, status, SHA-256 и размер.

## Аварийная блокировка MCP

Глобальный emergency deny предназначен для утечки credential, скомпрометированного MCP server, ошибочной policy или неконтролируемого side effect. Его включает только `admin`; согласование инцидента не должно задерживать первичную блокировку.

1. Откройте **MCP** и включите **Emergency deny**, указав номер инцидента и краткую причину. Эквивалентный API-вызов:

   ```http
   PUT /api/v1/mcp/emergency-deny
   Content-Type: application/json

   { "enabled": true, "reason": "INC-2026-041: suspected CRM token leak" }
   ```

2. Убедитесь, что ответ имеет `enabled: true`. `pendingCallsDenied` показывает немедленно отклонённые ожидающие calls; `executingCalls` — запросы, которые могли уже уйти во внешнюю систему.
3. Для каждого `executing` call найдите `callId`, server/tool, run/stage и policy hash в MCP audit/trace. Kill switch не отзывает уже отправленный HTTP request: при необходимости отключите MCP endpoint, отзовите upstream token и выполните vendor-specific cancel/compensation.
4. Не удаляйте server/call records до расследования. Экспортируйте связанный run trace и сопоставьте события `mcp.emergency_deny.enabled`, `mcp.call.emergency_denied` и `mcp.call.execution_blocked` с логами внешнего MCP server.
5. Исправьте policy или credential. Для credential задайте MCP scope, минимальные upstream права и новый expiry; скомпрометированный secret ротируйте во внешней системе до обновления АГАТ.
6. Выполните policy preview и проверьте `newlyAllowed`, `newlyDenied`, `approvalsIncreased/Decreased` и каждый effective change. Публикуйте только с актуальным `baseSha256`.
7. Снимите блокировку отдельным admin-действием и новой причиной:

   ```json
   { "enabled": false, "reason": "INC-2026-041 contained; token rotated; policy v4 reviewed" }
   ```

8. Выполните один безопасный read-only smoke call. Отклонённые calls автоматически не возобновляются: новый вызов должен получить новый `clientCallId` и пройти актуальную policy заново.

Switch хранится в SQLite и сохраняется после рестарта coordinator. Если панель недоступна, используйте тот же authenticated API через Gateway; не редактируйте таблицу `settings` вручную, иначе будет потерян actor/reason audit.

Для A2A сначала откройте раздел **A2A** и проверьте `enabled`, Agent Card URL, token suffix/rotation time, active-task limit и связанный внутренний run. Затем проверьте protocol boundary:

```bash
curl -i 'http://127.0.0.1:8787/a2a/v1/endpoints/ENDPOINT_ID/agent-card.json'
curl -i \
  -H 'Authorization: Bearer ENDPOINT_TOKEN' \
  -H 'A2A-Version: 1.0' \
  'http://127.0.0.1:8787/a2a/v1/endpoints/ENDPOINT_ID/tasks?pageSize=1'
```

`401` означает wrong/rotated token; `VERSION_NOT_SUPPORTED` — отсутствующий или не `1.0` заголовок; `CONTENT_TYPE_NOT_SUPPORTED` для POST — не `application/a2a+json`; `TASK_LIMIT_REACHED` — достигнут endpoint limit. `AUTH_REQUIRED` не является сбоем: оператор должен принять решение в обычной карточке run, после чего тот же task продолжит lifecycle. Подробности: [A2A adapter](./a2a-adapter.md).

Для Local RAG сначала проверьте карточку collection: точное имя `embeddingModel`, число `embeddingWorkers`, `pendingJobs` и ошибку документа. Затем проверьте установленную модель и endpoint:

```bash
ollama list
curl -fsS http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embeddinggemma","input":["health check"]}'
kubectl logs -n agat deployment/agat-worker --tail=100
```

Если indexing готов, но ответ не использует знания, убедитесь, что collection отмечена именно в окне этого run/process, а в trace есть `knowledge.retrieved`. Пустой список hits при готовых chunks чаще всего означает другое имя/размерность embedding-модели или нерелевантный query. Подробности: [Local RAG и управляемая память](./local-rag-and-memory.md).

Если model endpoint отвечает ошибкой на поле `tools`, выбранная модель или server не поддерживает function calling. Отключите web через `AGAT_WEB_ENABLED=false` и запретите MCP tools для такого stage либо выберите модель/OpenAI-compatible server с function calling. Настройка встроенных web tools описана в [руководстве по web-доступу](./web-access.md), gateway — в [руководстве MCP](./mcp-gateway.md).
