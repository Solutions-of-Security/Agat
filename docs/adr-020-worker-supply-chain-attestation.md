# ADR-020: раздельные OCI provenance и runtime attestation для server workers

- Статус: принят и реализован
- Дата: 2026-08-31
- Связано: [Fleet/HA 1.7](./fleet-ha-1.7.md), [worker supply chain](./worker-supply-chain-attestation.md), [ADR-019](./adr-019-residency-region-loss-dr.md)

## Контекст

Ed25519 release manifest v1.7 связывал worker version с artifact digest и rollout, но не доказывал происхождение OCI image и то, что зарегистрировавшийся процесс действительно исполняет этот digest. Shared enrollment token мог быть использован с копией подписанного manifest на другом workload. Встраивать Fulcio/Rekor/SLSA verifier в coordinator означало бы дублировать быстро меняющийся внешний trust stack и дать runtime сетевой доступ к registry/transparency services.

## Решение

Приняты три раздельных слоя доверия:

1. существующий signed release manifest;
2. компактный Ed25519 provenance admission, который выдаёт CI только после внешнего `cosign verify` и `verify-attestation` SLSA v1;
3. fresh challenge-response statement от локального workload attestor, связывающий workload identity/selectors и running image digest с exact release/provenance/cell.

У каждого слоя отдельный public-key map. Coordinator проверяет canonical statements, freshness, exact digest bindings и one-time challenge, но не принимает raw Sigstore/platform evidence. Python worker обращается к loopback/HTTPS broker, запрещает redirects и обновляет attestation до expiry без ротации node token.

## Последствия

Положительные:

- stolen enrollment token или copied manifest недостаточны для production lease;
- runtime не зависит от online Fulcio/Rekor/registry verification;
- provenance signer, release signer и runtime broker можно отзывать независимо;
- SPIRE/Kubernetes/Docker/TPM specifics остаются за узкой broker boundary;
- target cell после region-loss требует новые challenges/evidence.

Отрицательные:

- нужен внешний attestation broker и отдельный key lifecycle;
- compact admission доверяет CI тому, что он действительно выполнил Sigstore verification; audit CI является частью TCB;
- expiry создаёт availability dependency от local attestor, поэтому worker реализует proactive refresh;
- Docker Desktop developer profile без этих gates не является production evidence.

## Отвергнутые варианты

- Только release signature: не доказывает provenance или running workload.
- Raw Sigstore verification внутри coordinator: расширяет attack surface, network/CA/log dependencies и дублирует официальный verifier.
- Worker self-report image digest/labels: attacker контролирует claims.
- Один общий signing key: компрометация одной функции ломает всю chain of trust.
- Долгоживущая attestation без challenge/expiry: допускает replay после relocation/revoke.

## Проверка решения

Acceptance требует distinct keys, tamper/replay/expiry negative tests, token-preserving refresh, tenant deny на challenge table, production config fail-close при отсутствующих roots и documented Sigstore/SPIRE operational evidence.
