# UX-110: единый product dialog для значимых действий

Дата фиксации: 1 сентября 2026 года.

## Цель

Убрать системные `window.confirm` / `window.prompt` и до подтверждения отвечать оператору на четыре вопроса: что именно изменится, какой объект затронут, можно ли восстановиться и какое точное действие будет выполнено.

Визуальная основа — существующий shell АГАТ и его токены: [desktop](./agat-ux-implementation-desktop.png), [mobile](./agat-ux-implementation-mobile.png). Это локальное расширение готовой дизайн-системы, поэтому отдельный raster-концепт не создаётся.

## Общий контракт

Каждый вызов передаёт:

- конкретные `title` и `confirmLabel`;
- короткое описание намерения;
- объект и его тип, если действие относится к сущности;
- обязательные блоки `Что изменится` и `Можно ли восстановить`;
- tone `accent`, `warning` или `danger`;
- optional/required reason с собственными label, hint, placeholder и лимитом.

Результат — `{ confirmed, value }`. Отмена, Escape и размонтирование всегда возвращают безопасное `confirmed: false`. Одновременно может существовать только один запрос; новый запрос безопасно отменяет предыдущий.

## Матрица заменённых действий

| Зона | Действие | Tone | Основное последствие | Восстановление |
|---|---|---:|---|---|
| Credentials | Удалить credential | danger | Секрет и scope удаляются, integrations теряют authentication | Создать и привязать новый credential |
| Processes | Удалить schedule | danger | Новые автоматические запуски прекращаются | Настроить schedule заново |
| Processes | Ротировать webhook token | warning | Старый token немедленно недействителен | Обновить клиентов новым one-time token |
| Processes | Удалить webhook | danger | Входящие trigger/signal вызовы прекращаются | Создать endpoint и обновить клиентов |
| Process editor | Перейти с dirty draft | warning | Несохранённые локальные изменения отбрасываются | Остаётся последняя сохранённая версия |
| Knowledge | Удалить collection | danger | Удаляются документы, chunks и embeddings | Повторный ingest и index |
| Knowledge | Удалить document | danger | Документ исчезает из retrieval | Повторная загрузка и index |
| Knowledge | Удалить memory | danger | Запись больше не доступна агентам | Повторное ручное сохранение |
| A2A | Удалить outbound peer | danger | Удаляются connection и task mirrors | Повторно подключить peer |
| A2A | Ротировать endpoint token | warning | Старый token немедленно недействителен | Обновить клиентов новым one-time token |
| A2A | Удалить endpoint | danger | Новые внешние задачи не принимаются | Создать endpoint и обновить клиентов |
| MCP | Удалить server | danger | Tools исчезают из catalog/lease | Повторно добавить server и policy |
| MCP | Включить/снять emergency deny | danger + reason | Меняется глобальный circuit breaker проекта | Отдельное аудируемое обратное действие |
| Fleet | Revoke release | danger + reason | Release необратимо исключается из допуска | Новый signed release с новым ID/digest |
| Quality | Promote prompt/model | accent | Новые runs используют выбранную версию | Promote другой прошедшей gate версии |

## Иерархия и поведение

1. Header: семантическая иконка, действие, одно предложение контекста, безопасная кнопка закрытия.
2. Subject: моноширинное точное имя сущности.
3. Consequences: два стабильных блока без технического шума.
4. Reason: появляется только когда значение отправляется в API/audit; обязательность видна до submit.
5. Footer: cancel слева, точное действие справа. Generic `OK` и `Подтвердить` не используются.

Dialog не запускает side effect сам: после подтверждения существующий handler выполняет API-запрос и показывает свой штатный busy/error state.

## Доступность и responsive

- один глобальный provider и один modal host исключают конкурирующие overlays;
- native `<dialog>` плюс focus trap, Escape и возврат focus;
- `aria-labelledby`, `aria-describedby`, `aria-invalid` и `role="alert"`;
- первое поле причины получает focus, иначе focus получает безопасная кнопка закрытия;
- desktop width не больше 560 px;
- на mobile dialog превращается в нижний modal sheet с отступом 8 px;
- controls не меньше 44 px, основной текст не меньше 14 px;
- body прокручивается отдельно, actions остаются доступны без horizontal overflow.

## Проверочный сценарий

`MCP → Emergency deny → открыть dialog → очистить обязательную причину → получить inline error без закрытия → указать причину → отменить безопасно → открыть удаление MCP server → проверить subject/effect/recovery → закрыть Escape → повторить на 390×844`.

## Реализация

- `ActionDialogProvider` создаёт один глобальный modal host и promise-based API `{ confirmed, value }`;
- все 15 найденных `prompt/confirm` заменены в Credentials, Processes, Knowledge, A2A, MCP, Fleet и Quality;
- обязательная причина валидируется inline, не закрывает dialog и возвращает focus в поле;
- explicit autofocus имеет приоритет над нативным focus первого элемента `<dialog>`;
- Cancel, кнопка закрытия, Escape и размонтирование дают безопасный отрицательный результат и возвращают focus триггеру;
- unit guard рекурсивно проверяет frontend source и не позволяет вернуть blocking browser dialogs.

## Проверка реализации

Проверка выполнена 1 сентября 2026 года на актуальном frontend-коде:

- web tests — 23/23;
- web TypeScript — без ошибок;
- production build — успешно, Vite собрал 222 модуля;
- `rg` и unit guard не находят `window.prompt`, `window.confirm` и их неявные варианты в `apps/web/src`;
- встроенный Browser: desktop 1440×1000 и mobile 390×844;
- required reason: пробельное значение оставляет dialog открытым, показывает связанный `role="alert"` и фокусирует поле;
- подтверждение emergency deny обновляет server state; Cancel и Escape не выполняют side effect и возвращают focus исходной кнопке;
- mobile sheet занимает 374 px с симметричными отступами 8 px, document width остаётся 390 px, horizontal overflow отсутствует;
- mobile actions имеют высоту 48 px, body и footer не обрезаются;
- чистая перезагрузка проверочного сценария не создаёт console errors.

Frontend проверялся в гидратированной временной копии из-за существующих iCloud dataless placeholders в workspace; source синхронизирован с текущей реализацией. Mock API подтверждает frontend/API mapping, но не заменяет e2e с реальным coordinator.

## Release gate

- `rg` не находит blocking `prompt/confirm` в `apps/web/src`;
- unit guard не позволяет вернуть их позднее;
- dialog проверен с обычным confirm, required reason, Cancel и Escape;
- desktop/mobile не имеют clipping или horizontal overflow;
- screen reader и реальные iOS/Android устройства остаются общим release gate 1.8.
