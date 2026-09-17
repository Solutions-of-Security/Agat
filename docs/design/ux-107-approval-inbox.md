# UX-107: единый inbox согласований

Дата фиксации: 1 сентября 2026 года.

## Цель

Оператор принимает решение из одного project-scoped inbox и до действия понимает: что именно будет разрешено, кто запросил действие, что изменится, почему требуется контроль и когда запрос перестанет быть актуальным. Технические данные остаются проверяемыми, но не конкурируют с решением.

## Визуальные ориентиры

- [Inbox · desktop](./agat-approval-inbox-desktop.png)
- [Карточка решения · mobile](./agat-approval-inbox-mobile.png)
- [Реализация · desktop](./ux-107-implementation-desktop.png)
- [Реализация · mobile queue](./ux-107-implementation-mobile-list.png)
- [Реализация · mobile decision](./ux-107-implementation-mobile.png)

Концепты фиксируют иерархию, master-detail и responsive-поведение. Реальный интерфейс использует production tokens, существующий shell и SVG-иконки АГАТ.

В production-варианте сохранены master-detail, status/risk-first иерархия, конкретное действие, контекст эффекта, MCP disclosure, комментарий и audit trail. Осознанные отличия от концепта: project scope вынесен в summary вместо второго project-фильтра, технический disclosure на desktop занимает компактную правую колонку, а audit расположен рядом с контекстом. Это сокращает высоту первого экрана и оставляет обе decision-кнопки видимыми без прокрутки при 1440×900.

## Информационная модель

Каждый запрос нормализуется в один элемент inbox:

| Вопрос оператора | UI и источник |
|---|---|
| Что разрешаю? | Конкретный action label: MCP tool call или продолжение указанного запуска |
| Кто запросил? | Агент этапа; для stage approval — имя согласующего шага |
| Что затронет? | MCP-сервер/tool или запуск и следующий этап |
| Что произойдёт? | Пользовательское описание эффекта без raw payload |
| Почему это риск? | Risk tier и policy reason; для stage — причина ручной контрольной точки |
| Когда решить? | Точный MCP deadline; для stage явно `Срок не задан` |
| Кто уже решил? | Required approvers, текущий счётчик и имена approvers |
| Что было после решения? | Audit timeline с actor, временем, комментарием или причиной отклонения |

Project-фильтр остаётся в глобальном переключателе проекта: API inbox уже изолирован заголовком `x-agat-project-id`. Внутри страницы этот scope повторяется read-only меткой, чтобы оператор не потерял контекст.

## Desktop

- верхний summary показывает число ожидающих решений, высокий риск и запросы без срока;
- фильтры: поиск, риск, тип, срок и состояние;
- очередь и detail образуют один master-detail workspace;
- строка очереди содержит action, источник, risk и оставшееся/прошедшее время;
- detail сначала показывает контекст и эффект, затем технический disclosure и audit;
- primary action повторяет конкретный глагол запроса, а не абстрактное `Разрешить`.

## Mobile

- список и detail — два состояния одного route;
- возврат `Все согласования` сохраняет фильтры;
- technical details свёрнуты;
- decision actions закреплены над bottom navigation и не перекрывают audit/content;
- controls не меньше 44 px, базовый текст 14 px, secondary 12 px;
- на 390 px horizontal overflow запрещён.

## Решение и защита от ошибки

- согласование принимает необязательный комментарий до 1000 символов;
- `Отклонить` сначала открывает reason-step и не отправляет запрос немедленно;
- причина отклонения обязательна и проверяется одновременно в UI и API;
- после успешного решения кнопки исчезают, но карточка остаётся доступной как recent audit item;
- повторный submit не предлагается;
- для multi-approver MCP первое согласование сохраняется в timeline, а запрос остаётся pending до достижения required count.

## MCP disclosure

В свёрнутом по умолчанию блоке показываются только уже redacted данные coordinator:

- arguments summary;
- preview diff;
- policy reason, rule/version и SHA-256;
- risk/risk tier;
- число и имена approvers.

Raw credentials, decrypted arguments и результат tool call в inbox не передаются.

## Audit contract

Decision API принимает:

```json
{
  "decision": "approve | reject",
  "comment": "optional, max 1000",
  "reason": "required for reject, max 1000"
}
```

После доменного решения coordinator записывает project-scoped событие `approval.decision.recorded`. Событие содержит actor, decision, comment/reason и безопасный snapshot запроса. Snapshot нужен, чтобы после удаления запроса из pending-очереди detail сохранил контекст, но не мог повторить side effect.

Для MCP `expiresAt` вычисляется в API как `createdAt + текущий server approval TTL`. Это корректно при неизменной runtime-конфигурации; для переноса запроса между конфигурациями с разным TTL нужен persisted `expires_at` в store DTO. Stage approval не получает вымышленный deadline.

## Доступность

- фильтры имеют видимые labels;
- выбранная строка помечается `aria-current`;
- сообщения валидации связаны с textarea через `aria-describedby` и имеют `role="alert"`;
- audit размечен как именованный список;
- status не кодируется только цветом;
- read-only роли видят причину недоступности решения;
- после открытия reason-step фокус переводится в обязательное поле.

## Проверочный сценарий

`Согласования → отфильтровать высокий риск → открыть MCP-запрос → проверить effect/policy/diff → согласовать с комментарием → увидеть audit без повторной кнопки → открыть другой запрос → отклонить → получить ошибку без причины → указать причину → увидеть отклонённый audit item`.

## Реализация

- `ApprovalsPage` собирает summary, фильтры, risk/deadline sorting, очередь и responsive list/detail;
- `ApprovalPanel` отвечает за контекст решения, progressive MCP disclosure, комментарий, двухшаговое отклонение, focus/error state и read-only audit;
- `approvalInbox` нормализует stage/MCP approvals, связывает события, восстанавливает безопасный resolved snapshot и формирует устойчивые `stage:*` / `mcp:*` identifiers;
- route `#approvals/:runId/:approvalId` сохраняет выбранное решение и допускает прямой безопасный переход;
- coordinator валидирует decision body, обязательную причину отказа и лимит 1000 символов, затем пишет project-scoped `approval.decision.recorded`;
- overview добавляет MCP deadline, а клиент отправляет один типизированный decision payload из inbox и из run detail.

## Проверка реализации

Проверен полный сценарий на mock API в реальном browser runtime:

1. На desktop 1440×900 отфильтрован высокий риск и открыт MCP-запрос.
2. Проверены redacted arguments, preview diff, policy reason/version/hash и прогресс approvers.
3. Согласование отправлено с комментарием; pending-item исчез, resolved-карточка осталась с audit и без повторного action.
4. Для второго запроса отклонение без причины заблокировано, textarea получила focus и `aria-invalid`; после ввода причины показан read-only rejected audit.
5. На mobile 390×844 проверены queue/detail navigation, свёрнутые фильтры и disclosure, sticky actions над bottom nav, touch targets не меньше 44 px и отсутствие horizontal overflow (`scrollWidth === clientWidth`).
6. В console нет runtime errors или warnings; присутствуют только сообщения Vite и React development runtime.

Автоматическая проверка финального кода:

- web unit tests: 21/21;
- web TypeScript: без ошибок;
- web production build: 221 modules;
- coordinator decision helper tests: 5/5;
- расширенный coordinator suite: 142/142 на 25 из 26 гидратированных test-файлов; integration-наборы без настроенных внешних сервисов штатно пропущены;
- полный coordinator TypeScript: без ошибок в чистой локальной копии с гидратированным исходным `database.ts`.

Прогон непосредственно из workspace повторно запускался, но существующие iCloud dataless-файлы периодически завершают чтение с `ETIMEDOUT`. Повтор в гидратированной локальной копии прошёл; единственный существующий `postgres-resilience.test.ts` не удалось получить из iCloud и он не вошёл в 142 теста. Ограничение среды зафиксировано явно и не маскируется как полный repository suite.

## Осознанные ограничения

- recent resolved items восстанавливаются из event window overview; долговременный поиск по всему audit archive требует отдельного cursor API;
- deadline stage approval не определён текущим runtime-контрактом и честно показан как отсутствующий;
- вычисление MCP deadline опирается на серверный TTL; перенос persisted запроса между coordinator-конфигурациями с разным TTL требует прямого `expires_at` в store DTO;
- реальный multi-user race, screen reader и physical-device проверки остаются release gate 1.8.
