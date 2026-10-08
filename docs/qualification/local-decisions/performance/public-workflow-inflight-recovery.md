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

## Native run 08.10 MSK

Из `4bab6af` whole run завершён: **49 completed instances / 49 bound caller
returns / 49 fixture primary calls**, без изменения primary branch.
**45 computed = 31 ok / 14 abstain**, **один unavailable/unreachable** на
prospective index 3 и **три whole context rejection**. [Summary](./public-workflow-inflight-recovery-summary.json)
закрепляет raw pins и verification seal; accepted не означает human gold.

Active HTTP snapshot зафиксировал один handler и пять прежних completed
handlers (три prefix + два warmup); SIGKILL отправлен через **68.570 ms**,
actual group из трёх PID завершилась, parent exit **-9**. Replacement на
том же port/profile прошёл fresh zero origin и два warmup до следующего
instance. Recovery request → ready/warmup receipt — **5465 ms** в этом run;
это единичное наблюдение, не customer recovery SLO.

Completed counters двух epochs — **5 / 47**, вместе **52**: **48 scheduled
completed + 4 warmup**. Один attested active scheduled handler сохранён
отдельно с unknown terminal outcome после последнего snapshot. Guard
accept/reset — **0 / 0**; closed original socket не выдан за guard reset.
GPU-forward interruption не установлено. Whole elapsed — **38540.540 ms**,
computed caller p50 **204.272 / p95 514.305 / max 653.482 ms**; unavailable
caller **71.440 ms**. Serial fixture integration не даёт capacity/SLO.

Independent standard-library audit — **741 checks**, **186 committed
contributors**; все **48 noninterrupted responses** совпали с прежним
standalone control при binary64 JSON comparison без id/duration.
Все **57 recorded temporary PID** отсутствуют, credentials удалены;
protected resident **4 PID / 35 sources / fresh raw metrics** сохранён.
Actual boot/login не наблюдался. Восемь новых tests и full **899 Python /
4 optional skips, 12 Node**, links/catalog прошли.

Initial targeted run получил sandbox socket denial. Повтор выявил неверный
worker profile tag в synthetic socket fixture; исправление проверено
реальными socket crash/rebind и полным regression run. Эти failures
сохранены отдельно; native run выполнен один раз после исправления.
v1/v2 compatibility, customer owner/SLO и independent human/calibration/
holdout gates сохраняют прежний статус; routing false / not_assessed.
