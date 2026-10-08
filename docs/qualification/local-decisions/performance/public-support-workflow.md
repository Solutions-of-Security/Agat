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
