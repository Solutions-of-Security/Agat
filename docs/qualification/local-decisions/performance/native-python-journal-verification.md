# Проверка native Python journal и незавершённых traces

Статус: **24 original CLI invocations и независимый stdlib replay PASS**.
Этап добавляет reusable проверку [original native journal](./native-python-call-observations.md)
без новых model calls.

## Поведение verifier

[CLI](../../../../scripts/verify-native-python-journal.py) и
[библиотека](../../../../scripts/lib/decision_native_python_journal.py)
сначала проверяют external SHA всех bytes journal и declared observer source SHA.
Identity SHA и ожидаемую последовательность input SHA можно закрепить отдельно.
Повторы input SHA сохраняются в исходном порядке. CLI создаёт только новый
`docs/private/.../verification.json` с mode 0600 и отказывает перезапись.

Проверяются schema v1, точные поля, SHA chain, sequence/span/score IDs,
PID/owner thread, вложенность calls, восстановление hooks, монотонность host/CPU
clocks и арифметика elapsed values. Boolean не принимается как integer.
Файл ограничен 8 MiB; final symlink, unfinished JSONL record, nonfinite number
и duplicate member отвергаются. Строгая обработка duplicate names соответствует
[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html#section-4) и учитывает
[default Python JSON behavior](https://docs.python.org/3.13/library/json.html#repeated-names-within-an-object).

Entry, return, raise и unfinished span имеют отдельные counts, включая каждого
recorded score. `completeScoringTrace=true` требует непустой trace, всех ended
spans, подтверждённых hooks и всех expected inputs, если schedule передан.
Context refusal с ended exception — завершённая запись отказа, а не успешное
model inference. Header-only trace остаётся incomplete. Recorded source metadata
и byte pins не аутентифицируют исполнение модели: observer/runtime source bytes
не перечитываются CLI. Эти границы явны в receipt; GPU, quality и routing claims false.

Если bound schedule содержит четыре запроса, полностью ended первый запрос
не означает полный schedule. Verifier отдельно сообщает завершённость recorded
spans, unstarted expected inputs и полноту всей ожидаемой последовательности.
Неполный journal по умолчанию отказывает до создания output directory.
`--allow-incomplete` разрешает только diagnostic artifact со статусом incomplete;
он не переводит counts entries в returns или completed inference.

## Original CLI controls

Original 25-record journal прочитан буквально из immutable native archive.
External observer/identity pins и schedule закреплены до первого CLI invocation.
В основной копии и standalone checkout из двух файлов выполнена полная проверка;
оба verification файла byte-identical. Новые inference и primary calls=0.

| Контроль | Text-backbone entries | Returns | Complete scoring trace |
|---|---:|---:|---|
| Original complete journal, две CLI копии | 3 | 3 | true |
| Header-only prefix | 0 | 0 | false |
| Open model span prefix | 1 | 0 | false |
| Returned model, backend span открыт | 1 | 1 | false |
| Только первый score ended, expected schedule=4 | 1 | 1 | false |
| Все spans ended, model object preservation=false | 3 | 3 | false |

17 отказов без output directory включают incomplete без opt-in, неправильные
byte/source/identity/sequence pins, изменённый binding, неправильный порядок end,
boolean clock, ошибочную delta, GPU claim, duplicate member, missing final LF,
broken seal, call после restoration, другой owner thread, integer hook flag
и неизвестный payload field. Semantic controls заново sealed, поэтому простое
отличие outer file SHA не скрывает проверяемый дефект.

## Evidence и ограничения

Source commit `6188b349c4529bc06ac7aa8bb6b26161a10a06f3`: five pinned snapshot sources;
actual standalone CLI closure=2. 19 новых / 31 targeted tests и 1290 full Python
tests PASS, четыре optional skips. [Regressions](../../../../scripts/test/test_decision_native_python_journal.py)
проверяют partial traces, отдельные counts, strict parsing, schedule binding,
clock/identity defects и отсутствие недоказанных claims.

Original probe: 134 checks. Independent stdlib audit: 3537 checks / 3146 JSON keys,
seal `b0698f89cd159b7a4cacadfd12f49a1181cfb70c5d66add35f06c9e0683f3db0`. Он восстановил все 24 controls, все семь receipts
и их seals из saved bytes, а также отсутствие 17 output directories.
Auditor не импортирует producer/verifier и не выполняет CLI повторно.
Protected resident: 27 checks / восемь неизменных полей; 25 owned PID отсутствуют.

У original native experiment было три observed model calls; у этого этапа
и offline replays новые model calls=0. Hash consistency не подтверждает human
identity, model execution authenticity, GPU work, accuracy, SLO или capacity.
Публичные dev labels и sealed holdout не использовались. Routing=false,
qualification=not_assessed; реальные reviews и owner criteria остаются открыты.

[Aggregate summary](./native-python-journal-verification-summary.json),
[archive summary](./native-python-journal-verification-archive-summary.json).

## Использование

```bash
python3 scripts/verify-native-python-journal.py \
  --journal docs/private/your-run/events.jsonl \
  --journal-file-sha256 JOURNAL_SHA256 \
  --expected-observer-sha256 OBSERVER_SOURCE_SHA256 \
  --expected-identity-sha256 HEADER_IDENTITY_SHA256 \
  --expected-input-sha256 FIRST_INPUT_SHA256 \
  --expected-input-sha256 SECOND_INPUT_SHA256 \
  --output-dir docs/private/your-run/new-verification
```

Повторяйте input flag для каждого expected score в полном порядке, включая
повторные входы. Для чтения незавершённого journal добавьте `--allow-incomplete`;
сохранённый receipt явно покажет missing spans и ещё не начатые expected inputs.

Архив: 118 files / 38018023 bytes / 3179 selected Git objects;
ZIP SHA `1b1809a5a7f1a97e7c6b4bcd83de5cc4d9c1137fdd80322e93b85359c7c856d0`. CRC, every file SHA и размеры проверены.
Фактическая копия в исходном workspace восстановила все original CLI controls
и семь verification receipts через stdlib audit без CLI/model rerun.
Proof file SHA `28ebf591598a78fea4da3320251e5095294617e6885256eca212805dce5ee788`; exact replay helper
встроен в отдельную квитанцию рядом с ZIP.
