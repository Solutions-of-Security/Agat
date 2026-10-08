# Whole public inventory через worker и coordinator

Предшествующие [HTTP](./public-support-http-load.md) и
[primary-active](./public-support-quarter-rate.md) probes проверяли runtime
на полном frozen public development inventory. Следующий контракт
проверяет все 49 cases через настоящий `agat_worker.py`, authenticated
coordinator и полный cohort census. Primary — явный HTTP fixture; mode
`serial_closed_model_integration`, без утверждения open-arrival capacity
или производительности совместной модельной нагрузки.

[Launcher](../../../../scripts/run-public-support-workflow.py) закрепляет
контекст, profile, manifest, 34 dependency pins и весь source snapshot до
создания собственного MLX runtime. Он получает отдельный loopback port,
2048-token limit / wired 4096 MiB / 5000-ms inference deadline. Два warmup
отделены от cohort. Resident не получает inference или изменений jobs.

[Driver](../../../../scripts/run-public-support-workflow.mts) создаёт
отдельные coordinator, fixture server, published process v1 и настоящий
worker. Workflow recipe записан до первой instance; все исходные inputs
передаются целиком и в исходном порядке. Process instance → run → stage →
case/input SHA сохраняются в journal. Основной route определяется fixture
output `PRIMARY_OUTPUT`; каждая instance должна завершить `PRIMARY_BRANCH`
без `WRONG_BRANCH`, независимо от результата shadow.

StartAt — реальный UTC перед первой instance, EndAt — реальный UTC после
полного inventory и drain. Это явно объявленный creation-window closure
для изолированного process; duration не превращается в семидневный pilot.
401 без auth и 200 с ephemeral local admin проверяются при GET census.
Raw inputs, questions/options, primary outputs и events исключены из
cohort DTO. Runtime/worker logs private, ephemeral credentials удаляются
после drain. Source/manifest/context и plan bytes повторно сверяются.

[Verifier](../../../../scripts/lib/decision_public_workflow.py) требует
весь исходный denominator, единственный assigned stage/assignment,
negotiated caller intent + returned ledger, соответствующий recorded
observation, первоначальные input/profile/tokens и quiescent physical
counter parity. Все over-limit cases должны дать context_too_long.
Пропуски, retries, replay, transformed input, изменённый route, raw context
в DTO и выдуманная qualification отвергаются. Fresh owned PID cleanup
проверяется отдельно от claims внутри результата.

Шесть regression tests используют только synthetic responses: полный
denominator с context rejection, пропущенный return/лишняя instance,
ledger/input/profile/route corruption, half-open window/privacy/authority,
mixed configuration и early output guard. Classification accuracy не
измеряется. Owners не назначены; customer workflow, human review,
calibration/holdout и actual boot/login остаются внешними gates.

## Native результат 08.10 MSK

Из `23249ec` получены **49 completed instances / 49 durable caller returns**,
**46 computed** (31 ok, 15 abstain), **3 context_too_long**. Все **49** original
input SHA сохранены; каждый run имеет один assigned shadow stage и один
negotiated intent/returned ledger. Все **49** primary branches сохранены,
fixture primary выполнен ровно 49 раз. Cohort включает 49 runs, 98 stored
stages, 49 shadow stages; authenticated HTTP 200 / unauthenticated 401.

Computed caller p50 **199.027 ms**, p95 **576.571 ms**, max **657.463 ms**.
Это HTTP caller timing внутри serial integration, без workflow queue delay
и без inference настоящей primary модели. Total launcher elapsed **32001.797
ms** включает startup, warmup, workflow и teardown. Эти значения не являются
open-arrival capacity или latency production workflow.

Физические counters: **51** handlers, включая два отдельных warmup — 31 ok,
17 abstain, три context rejection, прочие outcomes zero. Source snapshot:
**177** files. Independent audit: **553 checks**, все **30** наблюдавшихся
owned PIDs, включая transient helpers, отсутствуют при свежем census.
Ephemeral credentials удалены; resident сохранил 4 процесса, 35 sources и
counters 1/0/0. Actual boot/login остаётся awaiting_event.

Числовые значения/distributions/выборы всех **49** responses совпали со
standalone baseline без id/duration. Raw representation signatures — **0/49**:
Node сериализует temperature `1.0` как `1` и integral logits без десятичной
части. Для сравнения использованы binary64 JSON number semantics с zero
normalization; bool не превращается в число. Правило сверено с
[RFC 8785 §3.2.2.3](https://www.rfc-editor.org/rfc/rfc8785#section-3.2.2.3).
Это отдельная семантическая проверка; исходные bytes/SHA artifacts сохранены.
Первоначальное неверное ожидание byte-signature equality и его correction
сохранены в private audit logs. Classification accuracy не измерена.

Full docs: **857 Python / 4 optional skips, 12 Node**, links/catalog pass.
[Allowlisted summary](./public-support-workflow-summary.json) связывает
scope, source/receipt SHA и полный denominator.
[Проверенный постоянный архив](./public-workflow-archive-summary.json)
содержит 31 файл; CRC, размер и SHA каждого entry и полного ZIP сверены
после копирования. Следующий технический шаг —
публичный offline receipt verifier для этого workflow evidence; owners,
human review/calibration/holdout, customer SLO и actual boot/login остаются
открытыми, routing false / not_assessed.
