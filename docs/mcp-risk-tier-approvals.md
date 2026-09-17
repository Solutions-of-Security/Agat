# Risk-tier approvals и emergency deny для MCP

Релиз 1.1 усиливает существующий MCP gateway без создания второго контура авторизации. Risk labels, legacy server/tool policy, OIDC RBAC, lease ownership и audit trace остаются обязательными входами; поверх них добавлены версионированная policy-as-code, preview diff, four-eyes для критических side effects, scoped credentials и глобальный emergency deny.

## Гарантии релиза

- каждый tool получает tier `low | elevated | high | critical`, effect `allow | approval | deny` и требуемое число подтверждений `0 | 1 | 2`;
- `destructive` и любой `critical` tool можно только запретить либо разрешить после двух подтверждений от разных OIDC `sub`;
- legacy `deny` остаётся жёстким запретом, а legacy `approval` — нижней границей в одно подтверждение;
- policy публикуется как immutable project-scoped версия с SHA-256 и optimistic concurrency по хэшу preview;
- аргументы вызова и их безопасный preview diff фиксируются до решения оператора;
- MCP credentials можно ограничить namespace, tool glob, risk-классом, catalog injection и сроком действия;
- глобальный kill switch немедленно закрывает новые и ожидающие MCP-вызовы и повторно проверяется перед upstream `tools/call`;
- решение policy, версия, approvers и результат вызова продолжают существующий append-only event/audit trace и OTel span.

## Базовая policy

До первой пользовательской публикации действует виртуальная версия `v0` `baseline-enterprise`:

| MCP risk | Tier | Effect | Подтверждения |
|---|---|---|---:|
| `read` | `low` | `allow` | 0 |
| `write` | `elevated` | `approval` | 1 |
| `unknown` | `high` | `approval` | 1 |
| `destructive` | `critical` | `approval` | 2 |

Annotations MCP по-прежнему считаются недоверенными, пока оператор явно не включил `trustAnnotations` для проверенного сервера. Источник risk label и правила нормализации описаны в [MCP gateway](./mcp-gateway.md).

## Формат policy-as-code

Policy — JSON-документ `schemaVersion: 1`. Defaults обязательны для всех четырёх risk labels. Rules обрабатываются сверху вниз; применяется первое совпавшее правило.

```json
{
  "schemaVersion": 1,
  "name": "production-mcp",
  "defaults": {
    "read": { "tier": "low", "effect": "allow", "approvals": 0 },
    "write": { "tier": "elevated", "effect": "approval", "approvals": 1 },
    "destructive": { "tier": "critical", "effect": "approval", "approvals": 2 },
    "unknown": { "tier": "high", "effect": "approval", "approvals": 1 }
  },
  "rules": [
    {
      "id": "deny-crm-delete",
      "description": "Запретить удаление CRM-объектов",
      "match": {
        "serverNamespaces": ["crm"],
        "toolPatterns": ["delete_*", "purge_*"],
        "risks": ["destructive"]
      },
      "decision": { "tier": "critical", "effect": "deny", "approvals": 0 }
    },
    {
      "id": "two-eyes-payments",
      "description": "Критические изменения платёжных данных",
      "match": {
        "serverNamespaces": ["billing"],
        "toolPatterns": ["update_*"],
        "risks": ["write"]
      },
      "decision": { "tier": "critical", "effect": "approval", "approvals": 2 }
    }
  ]
}
```

`serverNamespaces` принимает точные нормализованные namespace или `*`. `toolPatterns` поддерживает `*` и `?`; сопоставление выполняется с полным upstream tool name. Rule ID уникален и стабилен для audit.

Допустимые согласованные решения:

- `allow` требует `approvals: 0`;
- `approval` требует `approvals: 1` или `2`;
- `deny` требует `approvals: 0`;
- `risk=destructive` или `tier=critical` требует `deny` либо `approval: 2`.

Policy-as-code не отменяет существующие server default и per-tool override. Сначала они вычисляют legacy policy, затем итоговая policy применяет более строгую границу:

- legacy `deny` всегда даёт `deny`;
- legacy `approval` повышает `allow` минимум до одного approval;
- обязательный four-eyes повышает любой разрешённый critical/destructive вызов до двух approvals;
- emergency deny имеет последний и абсолютный приоритет.

## Preview и публикация

Редактор MCP выполняет двухшаговую публикацию:

1. `POST /api/v1/mcp/policy/preview` валидирует и канонизирует документ, вычисляет candidate SHA-256 и сравнивает effective decision для текущего каталога каждого MCP-сервера проекта.
2. `PUT /api/v1/mcp/policy` принимает тот же документ и `baseSha256`. Если активная policy изменилась после preview, запрос отклоняется и preview нужно повторить.

Preview не меняет runtime. Он показывает число проверенных/изменённых tools, новые allow/deny и рост/снижение числа approvals, а также before/after для каждого изменившегося tool. Успешная публикация создаёт новую immutable версию и событие `mcp.policy.activated` с actor, SHA-256 и summary diff. Повторная публикация документа с тем же хэшем не создаёт версию.

Ожидающий или исполняемый call не сохраняет право на ослабленную старую policy. Coordinator повторно вычисляет актуальное решение во время approval и непосредственно перед upstream-вызовом; ужесточение увеличивает требуемое число approvals или переводит call в `rejected`.

## Preview diff вызова

До approval coordinator шифрует полные arguments и отдельно сохраняет redacted operator preview. Если payload имеет пары `before/after` либо `current/desired`, UI показывает верхнеуровневые операции `add/remove/replace`. Для остальных контрактов показывается `invoke` с ограниченным снимком аргументов.

Ключи, похожие на `authorization`, `credential`, `password`, `secret`, `token`, `apiKey` или `privateKey`, заменяются на `[redacted]`; глубина, длина строк, массивы и число полей ограничены. Preview помогает понять ожидаемый эффект, но не является доказательством фактического diff во внешней системе: MCP server всё равно обязан валидировать preconditions и поддерживать idempotency.

## Four-eyes

Critical/destructive call создаётся со статусом `waiting_approval` и счётчиком `0/2`.

1. Первый `admin` или `operator` подтверждает вызов; API возвращает `202`, call остаётся `waiting_approval` со счётчиком `1/2`.
2. Второй пользователь с другим проверенным OIDC `sub` подтверждает тот же call.
3. Coordinator атомарно переводит call в `executing`, повторно проверяет policy, lease, server/tool и kill switch и только затем вызывает upstream.

Повторное решение того же субъекта отклоняется, даже если изменилось отображаемое имя. Любой `reject` терминален. В legacy local mode существует один стабильный субъект `local-admin`, поэтому four-eyes намеренно невозможно завершить одним admin token; для критических вызовов нужен OIDC с двумя отдельными учетными записями.

Таблица `mcp_tool_call_approvals` хранит одно текущее решение на `(call_id, actor_subject)`; append-only events отдельно фиксируют запрос, каждый approval и terminal rejection. Overview и API возвращают только отображаемые имена approvers, количество и требуемый порог.

## Scoped credentials

Старый scope `project` сохранён для обратной совместимости. Для MCP рекомендуется `kind: "mcp"`:

```json
{
  "name": "CRM read token",
  "type": "api_key",
  "data": { "apiKey": "secret-value" },
  "scope": {
    "kind": "mcp",
    "serverNamespaces": ["crm"],
    "toolPatterns": ["get_*", "search_*"],
    "risks": ["read"],
    "allowCatalog": true,
    "expiresAt": "2026-12-31T21:00:00.000Z"
  }
}
```

Контроль выполняется в coordinator до добавления secret header:

- binding к MCP server требует точного разрешённого namespace;
- `tools/call` требует совпадения tool glob, risk label и незавершившегося `expiresAt`;
- при `allowCatalog=false` secret не добавляется к `tools/list`;
- MCP-scoped secret нельзя выбрать или использовать в HTTP process step;
- secret value остаётся AES-256-GCM encrypted at rest и никогда не выдаётся worker/UI list API.

Scope ограничивает применение credential, но не полномочия самого upstream token. Выдавайте внешнему сервису минимальные права и срок жизни независимо от gateway.

## Централизованный emergency deny

`PUT /api/v1/mcp/emergency-deny` доступен только `admin`, требует явную причину и меняет один persisted глобальный switch для всего coordinator:

- при включении все `waiting_approval` calls всех проектов становятся `rejected`;
- новые calls, calls из уже выданных leases и final preflight перед upstream получают `deny`;
- catalogs и audit остаются доступны для расследования;
- состояние, actor, причина, время и число отклонённых calls сохраняются после рестарта;
- уже отправленный в MCP server запрос физически отозвать нельзя; snapshot отдельно показывает число calls в `executing`.

Снятие блокировки также требует `admin` и причины. Оно не возобновляет отклонённые calls: модель или пользователь должны инициировать новый вызов с новым `clientCallId`. Операционный порядок действий приведён в [runbook](./operations.md#аварийная-блокировка-mcp).

## RBAC и audit trace

| Операция | Роли |
|---|---|
| Читать policy, catalog и audit summaries | `admin`, `designer`, `operator`, `viewer`, `auditor` |
| Preview/activate policy, server/tool legacy policy, scoped credentials | `admin`, `designer` |
| Approve/reject call | `admin`, `operator` |
| Включить/снять emergency deny | `admin` |

В SQLite migration v14 добавлены immutable `mcp_policy_versions`, distinct approvals, credential scope и policy/diff snapshot каждого call. Существующие события дополняются `riskTier`, `requiredApprovals`, policy version/SHA/rule/reason и redacted diff. OTel `execute_tool` span содержит `agat.mcp.risk_tier`, `agat.mcp.required_approvals`, `agat.mcp.policy.version` и `agat.mcp.policy.sha256`; prompt, secret и полные arguments/results не экспортируются.

## Известные границы

- policy preview сравнивает только tools из последнего сохранённого catalog; новый tool всё равно получит defaults/rule при первом появлении;
- SQLite и глобальный switch предполагают один активный coordinator; HA требует общего state store и сериализованной активации policy;
- kill switch не является distributed transaction с внешним MCP server и не компенсирует уже выполненный side effect;
- автоматической классификации PII и семантического анализа произвольной schema нет;
- scopes ограничивают header injection в АГАТ, но не заменяют upstream IAM, egress firewall и rotation.
