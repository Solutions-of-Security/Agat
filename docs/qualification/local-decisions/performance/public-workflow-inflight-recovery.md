# Public workflow: active HTTP handler crash and owned recovery

[Launcher](../../../../scripts/run-public-support-workflow-recovery.py)
и [replay](../../../../scripts/lib/decision_public_workflow_recovery_verification.py)
расширяют diagnostic protocol явной v3 recipe. v1 whole workflow и v2
SIGTERM между completed instances сохраняют прежнюю семантику.

Весь original public development inventory проходит через actual worker,
published coordinator process и fixture chat-completions primary. Router
выключен, shadow не меняет primary output/branch. Labels, calibration,
holdout и customer qualification не добавляются.

Prospective native target — zero-based index **3**, whole eligible input
с **1337 tokens** из закреплённого context. Выбор сделан до model calls;
healthy prefix, target и весь recovery suffix входят в original denominator.
Serial integration не измеряет open arrival capacity или customer SLO.

После completed prefix driver запрашивает arm и ждёт exclusive receipt.
Parent проверяет quiescent counters и собственную live session. Только
после arm driver создаёт target instance; raw metrics должны показать
ровно **один active POST handler** при неизменных completed counters.
SIGKILL отправляется всей проверенной собственной process group, затем
exit `-9` и отсутствие её PID проверяются отдельно. Snapshot → signal
ограничен 250 ms; trigger учитывает HTTP handler, включая body read,
и не доказывает прерывание Metal/GPU forward.

Это соответствует documented semantics [new child session](https://docs.python.org/3/library/subprocess.html#subprocess.Popen),
[process-group signal](https://docs.python.org/3/library/os.html#os.killpg)
и [SIGKILL](https://docs.python.org/3/library/signal.html#signal.SIGKILL).
Сигнал не направляется по resident PID или чужому endpoint.

Worker должен durable записать actual `unavailable/unreachable` и завершить
primary branch выбранного target. До этого replacement не запускается.
Vacated loopback port временно резервируется TCP reset guard, без чтения
payload. Пока driver ждёт, guard должен получить **0 connections**:
unavailability исходного socket не выдаётся за новый TCP reset.

Replacement использует тот же port, profile, weights и 34 dependency pins.
У каждого runtime отдельный zero counter origin и **два warmup**; suffix
создаётся только после readiness/warmup receipt. Retry count — 0, caller
budget 10000 ms, inference 5000 ms. Startup ограничен 60 s/runtime, arm
30 s, active-handler observation 10 s, recovery 90 s, workflow 240 s,
driver 270 s и whole launcher 440 s. Missed trigger, changed profile,
unexpected return или cancellation сохраняются как failure.

Replay сверяет independent context/plan/result raw pins, historical Git
contributors и model implementation, full census/401→200 auth, raw journal,
оба barrier receipts, typed returns и отдельные physical counters двух
epochs. Прерванный handler остаётся отдельным attested start с unknown
terminal outcome; его не прибавляют к completed counters. Source/artifact
drift перед публикацией отвергается. Historical cleanup report не выдаётся
за новую live verification; actual boot/login, owner/SLO и human gates
остаются открытыми.

Восемь tests включают actual isolated socket crash/rebind, чужой listener,
stale handle rejection, actual Git offline replay, rehashed ownership/
epoch/timing/counter corruption, unavailable outside declared target,
journal drift, private exclusive publication и early output rejection.
