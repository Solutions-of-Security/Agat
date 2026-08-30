# OCI provenance и runtime attestation обычных workers

## Результат

Schema v25 (`agat-worker-attestation-siem-dlq-v25`) закрывает разрыв между «manifest подписан» и «этот процесс действительно запущен из проверенного OCI image». Production worker допускается к lease только когда одновременно выполняются четыре независимые проверки:

1. release manifest подписан доверенным Ed25519 release key;
2. отдельный CI gate проверил OCI signature и SLSA provenance v1 через Sigstore/cosign и подписал компактный admission statement отдельным provenance key;
3. local workload attestor получил одноразовый challenge coordinator и подписал fresh runtime statement отдельным runtime-attestation key;
4. runtime statement связывает challenge, worker/cell, SPIFFE-compatible workload identity, selector hash, exact OCI digest и SHA-256 provenance admission.

Release, provenance и runtime trust roots разделены. Компрометация deployment release key не позволяет самостоятельно утверждать CI verification или выдавать workload attestation.

## Поток доверия

```mermaid
sequenceDiagram
  participant CI as Release CI
  participant Sig as Sigstore / registry
  participant C as Coordinator
  participant W as Worker
  participant A as Local attestation broker

  CI->>Sig: cosign verify image@sha256 + verify-attestation SLSA v1
  CI->>CI: hash verified bundle; sign compact admission
  CI->>C: release manifest + release signature + provenance admission
  C->>C: verify distinct trust roots and exact subject digest
  W->>C: request one-time challenge + signed release identity
  C-->>W: nonce + immutable release/provenance/cell binding
  W->>A: nonce + binding
  A->>A: attest local process/selectors/image identity
  A-->>W: signed runtime statement
  W->>C: registration or refresh + runtime evidence
  C->>C: consume nonce once; verify freshness/signature/exact binding
  C-->>W: scoped node token + attestation expiry
```

Coordinator не реализует Fulcio/Rekor/SLSA verification заново и не принимает сырой bundle как доказательство. Это делает release CI с официальным `cosign`; АГАТ принимает короткий signed admission с хэшем проверенного bundle. В runtime coordinator хранит нормализованный statement и хэши, но не attestor credential, SVID private key или сырой platform evidence.

## OCI/SLSA admission contract

`POST /api/v1/fleet/releases` принимает прежние `manifest/keyId/signature` и новый объект `provenance`:

```json
{
  "statement": {
    "schemaVersion": 1,
    "policyId": "worker-slsa-v1",
    "subjectDigest": "sha256:...",
    "ociRepository": "registry.example.internal/agat/worker",
    "predicateType": "https://slsa.dev/provenance/v1",
    "builderId": "https://ci.example.internal/builders/hardened",
    "buildType": "https://ci.example.internal/build-types/oci-v1",
    "sourceRepository": "https://git.example.internal/platform/agat",
    "sourceCommit": "0123456789abcdef0123456789abcdef01234567",
    "sigstoreBundleSha256": "...64 hex...",
    "verifiedAt": "2026-08-31T10:00:00.000Z",
    "expiresAt": "2026-09-07T10:00:00.000Z"
  },
  "keyId": "worker-provenance-2026q3",
  "signature": "base64-ed25519-signature"
}
```

Fail-closed проверки:

- `subjectDigest` обязан точно совпасть с immutable `manifest.artifactDigest`;
- принимается только in-toto/SLSA predicate URI `https://slsa.dev/provenance/v1`;
- builder/build type/source являются абсолютными HTTPS URL без credentials/fragment;
- source commit — полный 40/64 hex digest, Sigstore bundle — отдельный SHA-256;
- admission не может быть выдан в будущем, быть просроченным или жить дольше 31 дня;
- Ed25519 provenance root не может совпадением конфигурации подменить отсутствие release root: обе подписи проверяются независимо и повторно при worker authentication;
- неизвестные или удалённые trust roots, повреждённый stored JSON/hash и истёкший admission немедленно закрывают registration/auth/lease.

### Reference release pipeline

Ниже показана граница, а не универсальный vendor CI template. Используйте digest, а не tag, и pin-версию `cosign` в собственном build environment:

```bash
cosign verify \
  --certificate-identity='https://github.com/example/agat/.github/workflows/worker.yml@refs/heads/main' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
  'registry.example.internal/agat/worker@sha256:...'

cosign verify-attestation \
  --type slsaprovenance \
  --certificate-identity='https://github.com/example/agat/.github/workflows/worker.yml@refs/heads/main' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
  'registry.example.internal/agat/worker@sha256:...' > verified-slsa.json

sha256sum worker.sigstore.json
npm run fleet:sign-worker-provenance -- \
  provenance-admission.json provenance-ed25519-private.pem worker-provenance-2026q3 \
  > provenance-envelope.json
```

`sign-worker-provenance.mjs` — reference для offline/dev Ed25519. Production private key должен находиться в KMS/HSM или защищённом CI signer; в cluster передаётся только JSON map public keys.

## Runtime attestation contract

### Challenge

Worker отправляет enrollment token, имя, platform/architecture, cell и signed release identity в `POST /api/v1/workers/attestation/challenge`. Coordinator повторно проверяет release/provenance и возвращает nonce, SHA-256 nonce, TTL и immutable binding. В database сохраняется только hash nonce.

Challenge:

- живёт `AGAT_WORKER_RUNTIME_CHALLENGE_TTL_SECONDS` (30–600 секунд, default 120);
- принадлежит одной `region/residencyDomain` cell;
- связывает точные release digest и provenance SHA-256;
- атомарно переходит `issued → consumed`; replay отклоняется;
- становится `failed/expired` после ошибки, TTL или region-loss activation.
- terminal challenge state содержит только hash/binding и очищается bounded maintenance через 7 дней; audit event остаётся в основном ledger.

### Local broker

Python worker передаёт challenge в `AGAT_WORKER_RUNTIME_ATTESTATION_BROKER_URL`. Разрешён HTTPS либо loopback HTTP для sidecar; redirects, URL credentials/query/fragment и ответы больше 1 MiB запрещены. Отдельный `AGAT_WORKER_RUNTIME_ATTESTATION_BROKER_TOKEN` передаётся только worker pod и не попадает coordinator.

Request broker:

```json
{
  "schemaVersion": 1,
  "challenge": "base64url-nonce",
  "challengeSha256": "...",
  "expiresAt": "...",
  "binding": {
    "workerName": "worker-a",
    "platform": "Linux 6.12",
    "architecture": "amd64",
    "region": "eu-central-1",
    "residencyDomain": "eu",
    "releaseId": "worker-1.8.0",
    "artifactDigest": "sha256:...",
    "provenanceSha256": "..."
  }
}
```

Broker возвращает `{statement,keyId,signature}`. Statement обязан повторить binding и добавить:

- allowlisted `provider`, по умолчанию `spiffe`;
- workload URI внутри `AGAT_WORKER_RUNTIME_IDENTITY_PREFIXES`;
- SHA-256 канонического selector set;
- `imageDigest`, точно равный release OCI digest;
- честный boolean `hardwareBacked` без автоматического повышения trust;
- fresh `issuedAt/expiresAt` с lifetime не больше `AGAT_WORKER_RUNTIME_MAX_LIFETIME_SECONDS`.

SPIRE подходит как local authority: Kubernetes selectors должны включать namespace/service account и image identity policy, Docker — `image_id`, Unix/systemd — OS-managed selectors. Broker обязан сам получить selector evidence от локального trusted agent; worker-supplied labels не являются attestation.

### Refresh без потери lease

Registration возвращает `attestationExpiresAt`. Python worker за 120 секунд до expiry получает новый challenge и вызывает authenticated `POST /api/v1/workers/attestation/refresh`. Coordinator сохраняет прежний node token и меняет только verified attestation claims. Если refresh недоступен до expiry, auth и новые/renewed leases закрываются; worker удаляет stale credential и проходит enrollment заново после restart.

## Конфигурация

Coordinator production minimum:

```dotenv
AGAT_WORKER_RELEASE_PUBLIC_KEYS={"release-2026q3":"-----BEGIN PUBLIC KEY-----..."}
AGAT_REQUIRE_SIGNED_WORKER_RELEASES=true
AGAT_WORKER_PROVENANCE_PUBLIC_KEYS={"worker-provenance-2026q3":"-----BEGIN PUBLIC KEY-----..."}
AGAT_REQUIRE_WORKER_PROVENANCE=true
AGAT_WORKER_RUNTIME_ATTESTATION_PUBLIC_KEYS={"spire-broker-eu-1":"-----BEGIN PUBLIC KEY-----..."}
AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION=true
AGAT_WORKER_RUNTIME_ATTESTATION_PROVIDERS=spiffe
AGAT_WORKER_RUNTIME_IDENTITY_PREFIXES=spiffe://example.internal/agat/worker/
AGAT_WORKER_RUNTIME_CHALLENGE_TTL_SECONDS=120
AGAT_WORKER_RUNTIME_MAX_LIFETIME_SECONDS=3600
```

Worker production minimum дополняет прежние `AGAT_WORKER_RELEASE_*`:

```dotenv
AGAT_WORKER_RUNTIME_ATTESTATION_BROKER_URL=http://127.0.0.1:8091/v1/attest
AGAT_WORKER_RUNTIME_ATTESTATION_BROKER_TOKEN=<sidecar-scoped-secret>
AGAT_WORKER_RUNTIME_ATTESTATION_TIMEOUT_SECONDS=10
```

Docker Desktop profile явно оставляет три production gates выключенными. Это developer exception, а не passing supply-chain attestation.

## Отзыв и incident response

1. OCI/SLSA incident: отзовите worker release через Fleet API — связанные nodes немедленно offline и `release_verified=0`.
2. Compromised provenance signer: удалите public key из config, перезапустите coordinators и зарегистрируйте новый release/admission; existing node authentication fail-closes на missing root.
3. Compromised runtime broker: удалите runtime root, перезапустите coordinators, revoke affected nodes/releases и смените sidecar scoped token.
4. Не увеличивайте lifetime как способ пережить broker outage. Восстановите attestor и re-enroll; running lease может быть повторён по обычной at-least-once модели.
5. Region-loss activation отзывает source worker credentials и инвалидирует все issued challenges; target workers получают evidence заново внутри target cell.

## Проверки

- `fleet-ha.test.ts`: отдельные keys, provenance digest/freshness, challenge binding, signature, replay deny, refresh без token rotation и expiry fail-close;
- `test_worker_attestation.py`: exact broker payload/scoped token и redirect deny;
- PostgreSQL suite: runtime role имеет DML, tenant role не читает global challenge table;
- coordinator/web typecheck, Python tests и оба Kubernetes kustomize renders входят в release gate.

## Источники

| ID | Первичный источник | Claim |
|---|---|---|
| SRC-2501 | [Sigstore: verifying signatures](https://docs.sigstore.dev/cosign/verifying/verify/) | image signature payload связывается с digest; `cosign verify` проверяет claims/signature, `verify-attestation` — attestations |
| SRC-2502 | [SLSA build provenance v1.2](https://slsa.dev/spec/v1.2/build-provenance) | canonical predicate URI — `https://slsa.dev/provenance/v1`; provenance содержит build definition и run details/builder |
| SRC-2503 | [SPIRE concepts](https://spiffe.io/docs/latest/spire-about/spire-concepts/) | node и workload attestation разделены; workload identity определяется trusted selectors |
| SRC-2504 | [SPIRE Agent configuration](https://spiffe.io/docs/latest/deploying/spire_agent/) | Kubernetes, Docker, Unix/systemd attestors предоставляют platform selectors, включая Docker `image_id` |

Источники проверены 2026-08-31. Они определяют семантику внешних механизмов; passing status конкретного CI, registry или SPIRE deployment подтверждается только его собственным evidence.
