# Изолированное выполнение MCP tools

Релиз 1.5 добавляет два project-scoped transport-профиля к существующему MCP gateway: `wasi` для небольших WebAssembly command-модулей и `container` для digest-pinned OCI workloads. Это не отдельный путь выполнения: каталог, risk labels, immutable policy-as-code, preview diff, approvals/four-eyes, scoped credentials, emergency deny, encrypted call audit и OTel trace остаются общими для HTTP и isolated tools.

## Граница доверия

Только `admin` создаёт или изменяет isolated profile. `designer` по-прежнему управляет HTTP MCP и project policy, но не выбирает исполняемый module/image. Worker и модель получают только MCP schema/public name; module, image command и credentials не входят в lease.

Перед каждым вызовом coordinator:

1. повторно вычисляет effective MCP policy и risk tier;
2. при необходимости ждёт одного или двух distinct OIDC approvers;
3. применяет MCP credential scope по namespace, tool glob, risk и expiry;
4. проверяет глобальный emergency deny;
5. создаёт одноразовые Kubernetes `Secret`, `NetworkPolicy` и `Job`;
6. перепроверяет emergency deny при ожидании Job и перед чтением результата;
7. удаляет Job и Secret независимо от результата, а NetworkPolicy — только после подтверждённой остановки Pod; при недоступном Kubernetes API policy остаётся как fail-closed boundary для операторской очистки.

Audit сохраняет `transport`, `sandboxProfileSha256`, policy version/hash, approvers, redacted input diff и result hash/size. Зашифрованный profile и полные arguments/result остаются в SQLite под `AGAT_CREDENTIALS_KEY`; API возвращает только module/profile hashes, limits, image digest, command и egress rules.

`sandboxProfileSha256` привязан к call до approvals. Если admin меняет transport, module, image, command, tool manifest, limits или egress между запросом, вторым решением и стартом, call отклоняется; новый profile требует нового вызова и нового набора approvals.

## Профиль WASI

Встроенный image `agat-local/sandbox-wasi:1.5.0` закрепляет официальный Python binding `wasmtime==48.0.0` и SHA-256 его Linux wheel artifacts из [PyPI release metadata](https://pypi.org/project/wasmtime/48.0.0/). Runner принимает core WebAssembly module с WASI Preview 1 `_start`, включает fuel metering и запускается внутри ограниченного Job. Host не вызывает `preopen_dir`, поэтому guest не получает filesystem capability. WASI Preview 1 network imports также не предоставляются.

Контракт guest:

- `AGAT_TOOL_INPUT_B64` — base64 JSON arguments;
- `AGAT_TOOL_CREDENTIAL_B64` — base64 JSON с уже проверенным scoped credential либо `{}`;
- stdout — ровно одно JSON-значение в пределах `AGAT_MCP_MAX_RESPONSE_BYTES`;
- stderr не копируется в call result или Kubernetes Job log через runner.

Размер module ограничен 512 KiB. Magic/version WebAssembly проверяются до шифрования и сохранения. При update `moduleBase64` можно не передавать: coordinator сохраняет прежний module и пересчитывает profile только из нормализованного manifest.

Wasmtime использует capability-oriented WASI: filesystem доступен только для явно preopened directories; АГАТ не выдаёт их. См. [официальное руководство WASI](https://github.com/bytecodealliance/wasmtime/blob/main/docs/WASI-tutorial.md), [CLI capabilities](https://docs.wasmtime.dev/cli-options.html) и [релизы Wasmtime](https://github.com/bytecodealliance/wasmtime/releases/).

## Профиль OCI

Native workload принимает только image reference с полным `@sha256:<64 hex>` и exec-array `command`; mutable tag без digest отклоняется. Контракт контейнера:

- `/run/agat/input.json` — JSON arguments;
- `/run/agat/credential.json` — scoped credential либо `{}`;
- stdout — ровно одно JSON-значение;
- stderr должен оставаться пустым: Kubernetes возвращает объединённый container log, поэтому любой вывод в stderr делает результат невалидным и call завершается ошибкой;
- root filesystem read-only, единственная writable область `/tmp` — memory-backed `emptyDir` 16 MiB.

Pod запускается с фиксированным non-root UID/GID 65532, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, `capabilities.drop: [ALL]`, без service-account token и service links. CPU, memory и deadline ограничены profile; `backoffLimit: 0`, поэтому side effect не повторяется Kubernetes Job controller. При настроенном `AGAT_SANDBOX_RUNTIME_CLASS` Pod дополнительно направляется в установленный оператором gVisor/Kata RuntimeClass. RuntimeClass — усиление изоляции, а не автоматически поставляемая microVM.

Kubernetes рекомендует non-root, seccomp, запрет privilege escalation и удаление Linux capabilities для Restricted workloads; для более сильной границы возможен sandboxed runtime. Источники: [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) и [Linux kernel security constraints](https://kubernetes.io/docs/concepts/security/linux-kernel-security-constraints/).

## Egress allowlist

Каждый Job сначала получает default-deny ingress/egress `NetworkPolicy`. OCI profile может добавить не более 16 пар `public exact IP + TCP port`; DNS names, CIDR, private, loopback, link-local, multicast, documentation и IPv4-mapped private addresses отклоняются. DNS egress не выдаётся, поэтому hostname resolution внутри tool не является скрытым обходом allowlist.

`NetworkPolicy` действует только при CNI, который её реально исполняет. Поэтому native container execution fail-closed требует операторское подтверждение `AGAT_SANDBOX_NETWORK_POLICY_ENFORCED=true`. Docker Desktop profile оставляет его `false`: WASI работает без network capability, OCI блокируется до установки и проверки enforcing CNI. Поведение default deny и зависимость от network plugin описаны в [документации Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/).

## Ephemeral secret broker

Coordinator выступает минимальным broker: после scope check создаёт immutable Secret с input, credential и, для WASI, module; Secret имеет имя конкретного `callId`, монтируется read-only только в его Pod и удаляется в `finally`. Raw values не записываются в Job spec, lease, server DTO, event metadata или OTel attributes. Kubernetes RBAC coordinator ограничен namespace и только необходимыми `create/get/list/delete` для Jobs, Pods/log, Secrets и NetworkPolicies.

Это не заменяет внешний Vault/KMS: долгоживущий credential всё ещё хранится зашифрованно в SQLite. Broker сокращает runtime lifetime и scope, но оператор обязан ротировать источник после подозрения на компрометацию.

## Конфигурация

| Переменная | Default | Назначение |
|---|---:|---|
| `AGAT_SANDBOX_ENABLED` | `false` | Включить Kubernetes executor; Docker Compose остаётся fail-closed |
| `AGAT_SANDBOX_NAMESPACE` | `agat` | Namespace одноразовых объектов |
| `AGAT_SANDBOX_WASI_IMAGE` | `agat-local/sandbox-wasi:1.5.0` | Доверенный fixed runner image |
| `AGAT_SANDBOX_RUNTIME_CLASS` | пусто | Опциональный установленный gVisor/Kata RuntimeClass |
| `AGAT_SANDBOX_NETWORK_POLICY_ENFORCED` | `false` | Явное подтверждение CNI enforcement для OCI |

`npm run k8s:up` собирает WASI image и включает executor. Docker Compose не имеет Kubernetes service account и по умолчанию оставляет executor выключенным.

## Проверка и incident response

```bash
node --import tsx --test apps/coordinator/test/mcp.test.ts apps/coordinator/test/sandbox.test.ts
docker buildx build --load --tag agat-local/sandbox-wasi:1.5.0 --file sandbox/Dockerfile .
kubectl auth can-i create jobs --as=system:serviceaccount:agat:agat-coordinator -n agat
kubectl auth can-i create secrets --as=system:serviceaccount:agat:agat-coordinator -n agat
kubectl auth can-i create networkpolicies.networking.k8s.io --as=system:serviceaccount:agat:agat-coordinator -n agat
```

Для smoke test используйте read-only WASI module без credentials. Проверьте `transport/profileSha256` в MCP audit и отсутствие оставшихся объектов с `sandbox.agat.dev/managed=true`. Затем отдельно проверьте emergency deny во время long-running call: Job и Secret должны быть удалены, NetworkPolicy — удалена после остановки Pod, call — перейти в failed/rejected audit state. Если API не смог подтвердить остановку Pod, policy намеренно остаётся; удалите её только после проверки отсутствия Pod с тем же `sandbox.agat.dev/call`.

При инциденте сначала включите централизованный MCP emergency deny. После этого отзовите source credential, disable profile и проверьте Kubernetes audit по `callId`. Kill switch останавливает ожидающие/new calls и удаляет наблюдаемый Job при очередной preflight-проверке, но не откатывает уже подтверждённый внешний side effect.
