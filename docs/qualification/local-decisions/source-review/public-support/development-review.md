# Экспертная разметка всего development inventory

Подготовка: 10.10.2026. Следующий шаг после технических замеров — получить
предметные решения на тех же исходных вопросах. Новый builder выделяет
**все development inputs** закреплённого context profile в два пустых пакета,
совместимых с [существующей терминальной сессией](../blind-terminal-review.md).
Полный прежний pool и его два review-пакета сохраняются. Этот отдельный scope
позволяет начать development-разметку без раскрытия заданий calibration/holdout
в интерфейсе рецензента.

## Какие данные видит рецензент

Терминал показывает полный исходный текст, вопрос, все пять вариантов с
описаниями и атрибуцию автора/лицензии. Варианты incident, enhancement, access,
other и insufficient сохраняют исходный смысл и порядок. insufficient —
предметный вариант при отсутствии одного установленного основного действия;
`:skip` оставляет задание без решения. Источник остаётся непроверенным
вопросом сообщества. Классификация запрошенной помощи не подтверждает
истинность описанного инцидента.

Builder использует только context profile и committed source bindings.
Нативные ответы, confidence, контрольные/shadow условия, primary outputs
и результаты замеров для формирования заданий не нужны. Терминал скрывает
group IDs, split seed и token/admission metadata. Исходный JSON review
содержит технические group/seed bindings; это свойство прежнего формата.
Для выполнения разметки используйте терминальную сессию.

Длинные входы сохраняются целиком. Отказ runtime по лимиту контекста не
исключает вопрос из экспертной разметки. Не переводите и не сокращайте
источник, не подставляйте observed `ok`/`abstain` в expectedOptionId.
Все reviewerId, reviewedAt, expectedOptionId и rationale первоначально null.

## Основание метода

[Google PAIR](https://pair.withgoogle.com/chapter/data-collection/) рекомендует
понятную рубрику, доступность всех вариантов, возможность пропуска и разбор
разногласий; качество инструкций и квалификация рецензентов влияют на данные.
Это поддерживает использование существующего интерфейса и двух отдельных
review. [Исследование эвристик аннотаторов](https://aclanthology.org/2022.emnlp-main.438/)
на multiple-choice reading comprehension показывает связь способов разметки
с качеством данных и обобщением моделей. Оно мотивирует проверку процесса,
но не устанавливает результат для этого корпуса. Скрытие модельных outcomes
здесь — наше решение для проведения независимой предметной разметки.

## Подготовка и воспроизведение

```bash
python3 scripts/prepare-public-support-development-review.py \
  --context-profile docs/private/2026-10-09/context-profile.json \
  --context-profile-file-sha256 c1c45eca0e0d11180dc060d292ab14480708ada261a00c1b79c57649f04dcf8c \
  --output-dir docs/private/development-review-new
shasum -a 256 docs/private/development-review-new/packet.json
python3 scripts/verify-public-support-development-review.py \
  --packet docs/private/development-review-new/packet.json \
  --packet-file-sha256 REPLACE_WITH_ACTUAL_64_HEX_SHA \
  --output-dir docs/private/development-review-verification-new
```

Output directory должен быть новым и находиться в `docs/private`; режимы
0700 для каталога и 0600 для файлов. `packet.json` закрепляет source commit,
все source SHA, исходный context file SHA, порядок каждого case/input/group
и SHA трёх outputs. Verifier восстанавливает каждое задание из закреплённого
context profile и сверяет полный canonical file content. Пропуск или замена
входа, изменение рубрики, заполненная метка, дополнительные файлы, symlink,
source drift или изменение утверждений о qualification дают отказ.
Верификация повторно проверяет исторические context sources; повторный сбор
Stack Exchange и повторная токенизация не выполняются.

Каждый реальный предметный рецензент начинает со своего blank review:

```bash
shasum -a 256 docs/private/development-review-new/review.first.blank.json
python3 scripts/review-decision-pool.py \
  --review docs/private/development-review-new/review.first.blank.json \
  --review-file-sha256 REPLACE_WITH_ACTUAL_64_HEX_SHA \
  --reviewer-id YOUR_OWN_REVIEWER_ID \
  --output-dir docs/private/your-development-review-session
```

Второй рецензент использует review.second.blank.json и отдельный новый
каталог. Не показывайте ему решения первого. Submitted reviews сохраняются
отдельно от immutable packet. Согласованные полные reviews могут быть
переданы существующему finalize-review; разногласия требуют третьего
рецензента. Пустые пакеты не проходят finalize. Reviewer ID и заполненная
форма сами по себе не подтверждают человека, квалификацию и независимость.
Эти основания должен подтвердить фактический процесс предметной проверки.

Человеческая разметка, качество, калибровка, представительность потока Agat,
owners и SLO остаются открытыми. Development-разметка не заменяет независимый
итоговый holdout. Routing=false, qualification=not_assessed, model calls=0.

## Проверка подготовки

Из committed sources `211732769ffe0302ceb27543ed0584bfcf96cb7a` подготовлен один immutable packet:
**49 whole inputs / 44 groups**, 46 context-eligible и три over-limit.
Два независимых blank-пакета содержат по 49 полностью пустых labels.
Два файла имеют одинаковые исходные bytes; фактическая независимость будущих
рецензентов этим не подтверждается. Calibration/holdout cases в заданиях — 0.

Packet file SHA: `a343b2d95bded58122bfc946c760d261db9579cbffa3ac564cc65fa245c99e24`. [Сводка](./development-review-native-summary.json)
содержит aggregate counts и file pins; исходные тексты остаются в docs/private.
Три outputs, context file SHA и все 49 input/group bindings восстановлены
offline. 236 preparation sources и 40 historical context sources проверены.

17 новых / 46 related / 1223 Python tests с четырьмя optional skips, 12 Node
tests, links и catalog PASS. Builder с незакоммиченными sources отказал до
создания output. Stdlib auditor без application imports независимо проверил
765 условий / 6852 JSON keys, все original input fingerprints,
атрибуцию, длинные тексты, source closure и 98 null labels. Auditor был pinned
до создания packet; preparation reruns=0. 27 protected resident checks и
восемь snapshot fields неизменны. Модель не вызывалась.

Результат — подготовленные задания. Реальных человеческих review и gold
labels — 0. Существующий finalize-review отвергает оба пустых пакета.
Предметное качество и qualification остаются not_assessed.
