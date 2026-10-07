# История shadow assignments при retry

07.10.2026. [Stage inventory](./stage-inventory.md) фиксирует сохранённые
стадии. Отдельная история назначений сохраняет каждую shadow dispatch lease
при primary retry. Scope — coordinator assignments выбранных traces;
начало HTTP и полнота клиентской population этим не подтверждаются.
SLO, routing и human qualification остаются открытыми.

Activity JSON shadow stage содержит `decisionShadowAssignmentHistory`
со schema `agat.decision.shadow-assignment-history.v1`. Новый stage получает
complete empty history; safe replay — replay history без assignments.
Dispatch добавляет отдельный assignment UUID, stageAttempt, profile/input
pins, callerTimeoutMs и observation=null в существующей transaction.
LeaseId в history DTO не передаётся. После retry прежние rows сохраняются.

`recordDecisionShadow` прикрепляет validated observation один раз к текущему
stageAttempt в той же transaction, что основное observation и event.
Ownership/deadline проверки и rollback действуют на обе записи.
Completion без shadow result прикрепляет прежний missing_result;
caller timing и model result не придумываются. После accepted observation
новый primary retry уже не получает shadow payload и не создаёт assignment.

`getRunTrace` добавляет `decisionAssignmentHistory` со schema
`agat.decision.shadow-assignment-inventory.v1`, scope
`coordinator_shadow_assignments` и stages. Данные взяты из того же SQL read,
что stage inventory и observations. Stage содержит coverage и assignments
с сохранёнными bindings/observation и readonly outcome:

| Outcome | Основание |
|---|---|
| recorded | Validated observation прикреплено к assignment |
| pending | Текущий stageAttempt ещё имеет active lease и не завершён |
| ended_without_observation | Assignment утратило lease ownership или stage завершён без observation |

Ended assignment не объявляется HTTP timeout или backend failure. Worker
делает shadow после primary и renewal: три primary failures могут создать
три assignments без единого HTTP-вызова. Assignment не доказывает вход
в LocalDecisionClient. Caller timing сохраняет прежнюю monotonic boundary.

Старые stages без history получают legacy_gap с пустым массивом; прошлые
назначения не восстанавливаются по текущему lease или stage age. Legacy gap
сохраняется при новом назначении после прежних attempts. Новые unassigned
stages и safe replay имеют отдельный coverage. Unknown schema, duplicate
IDs, неверный порядок attempts и numeric aliases отклоняются.

[Helper](../../../../../apps/coordinator/src/decision-shadow-assignments.ts)
не копирует state, question, profileJson или node identity. Caller SLI
по-прежнему считает observations и stored stages; assignment history
доступна отдельно. Current CLI закрепляет десять sources, включая новый
contributor. Исторические proofs сохраняют прежние source bindings.

Регрессии проверяют три retry assignments, record-once, предотвращение нового
shadow после accepted observation, safe/live replay, missing completion,
legacy active lease, isolation и SQLite reopen. Native proof публикуется
после независимого snapshot audit.

[OpenTelemetry HTTP spans](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
рекомендует отдельный span для каждой попытки отправки HTTP и ordinal
resend_count при повторе. Dispatch history поэтому не маркируется полным
HTTP attempt ledger. Следующий caller accounting gate требует отдельных
intent/return receipts с идемпотентной записью и unknown outcomes при worker
death. [Owner/SLO proposal](./resident-service-acceptance.md) остаётся draft.
