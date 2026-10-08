# Offline verifier полного public HTTP inventory

[CLI](../../../../scripts/verify-public-support-load.py) повторно проверяет
[HTTP measurement](./public-support-http-load.md) после переноса private evidence.
Он требует три независимо закреплённых raw SHA: context profile, load plan и
load result. Seals обеспечивают локальную целостность; они не являются подписью
владельца, доказательством human agreement или разрешением данных.

Исторические context/load source inventories восстанавливаются из Git.
Проверяются все обязательные contributors, каждый SHA, raw committed profile,
runtime implementation hash и 34 package pins / Python 3.13.12 / arm64.
Исторический Python код не выполняется; модель и tokenizer не загружаются,
HTTP и inference не запускаются. Полная Git history с обоими measured commits
нужна и на другой машине. Verifier, его imports и собственный runtime отдельно
закрепляются в новом sealed report до/после проверки.

Полный ordered development inventory должен совпасть с pinned context, включая
тексты, варианты, токены и три over-limit cases. Проверяются schedule offsets,
один slot, typed result/profile/input, распределение и policy, настоящий
caller timing contract, over-limit rejection, warmup, raw journal и отдельный
phase файл. Quantiles считаются nearest-rank независимо от measurement summary.
Ошибки и drops остаются в scheduled denominator. Rehashed omissions, повторы,
boolean/numeric aliases и ложные ratios не проходят.

Три последовательных health/metrics snapshots требуют одного runtime start,
ready/quiescent state, zero начальные counters и отдельные два warmup.
Typed replies и busy/profile mismatch связываются с точным physical outcome.
У unreachable/timeout/invalid response/cancelled caller observation физический
server outcome неизвестен: его нельзя вывести из ответа, которого нет.
Тогда counters проверяются по консервативной границе, unknown calls остаются
в знаменателе, report имеет `insufficient_data / bounded_unknown`, exit 2.
Противоречие counters или изменение consumed file даёт failure, exit 1.

`pass`, exit 0, означает согласованное diagnostic evidence с exact accounting.
Known runtime errors или низкий measured ratio не превращаются в SLO acceptance.
Human accuracy, representative traffic, qualification и routing остаются false /
not_assessed. Report проверяет сохранённое утверждение cleanup, но явно имеет
`liveCleanupVerified=false`: отсутствие PID сейчас требует отдельного native
наблюдения и нельзя подтвердить старым JSON. Это не меняет ранее выполненный
независимый live audit.

Новый output directory разрешён только в `docs/private`, 0700; immutable
`verification.json` — 0600. Уже существующий output не заменяется. Невалидный
input не создаёт usable report; safe stderr не печатает исходные вопросы.

```bash
python3 scripts/verify-public-support-load.py \
  --evidence-dir docs/private/public-load-native-20261008 \
  --context-profile docs/private/public-context-native-20261008/context-profile.json \
  --context-profile-file-sha256 c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c \
  --plan-file-sha256 d1d2cebc32b62d58e5a80a58e4f925d817c2a8e6195ece07df8236edcb39445e \
  --result-file-sha256 9530b67d9a3078e8446410282207ec0afbfa331805ef12278124e5c63e2256e6 \
  --output-dir docs/private/public-load-new-verification
```

Для другого run нужны его независимо полученные pins; SHA из редактируемого
соседнего JSON не устанавливает доверие к этому же JSON. Raw data сохраняются
в [проверенном архиве](./public-performance-archive-summary.json).

13 новых tests проверяют реально читаемые Git bytes, full denominator,
rehashed corruptions, bounds/aliases, неизвестный transport outcome, counters,
artifact drift и exclusive private CLI output. Fixtures полностью synthetic;
меток предметных экспертов и новых model calls они не создают.

## Native CLI replay 08.10 MSK

Из committed verifier `bf47ef3` выполнен новый offline CLI replay настоящего
публичного native inventory. Report — **pass / exact**, 49 scheduled/admitted,
46 computed, три context rejections, два warmup, те же quantiles и ratios.
Сверены все 40 historical context и 57 load contributors; сам verifier и
53 его sources закреплены отдельно, Python **3.14.3 / Darwin**. Это версия
проверяющего процесса; исходный MLX runtime остаётся Python 3.13.12 / arm64.

Новых model calls, live process assertions и human labels — 0. Сохранённое
cleanup утверждение согласовано, `liveCleanupVerified=false`; прежний live
audit находится в исходном measurement evidence. 39 targeted tests и полный
docs check **833 Python / 4 optional skips, 12 Node**, links/catalog — pass.
[Allowlisted report summary](./public-support-load-verification-summary.json)
закрепляет raw pins и result seal, не раскрывая вопросы или распределения.
