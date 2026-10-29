# ADR-022: развитие агентных профилей через проверяемый результат

## Статус

**Предложено, 17.10.2026.** Изменение очереди документировано по запросу пользователя; этот ADR не объявляет принятыми новые runtime-контракты или production rollout. Product owner отвечает за сценарий и критерий пользы, engineering lead — за реализацию, runtime/data owners — за эксплуатацию и данные, release owner — за выпуск. Конкретные люди и capacity пока не назначены.

Основания: [сравнение рынка и девять исследований](./agent-market-and-research-2026-10-17.md), [AS-IS pack](./internal-report-pack.md), [specialist teams](./langgraph-specialist-teams.md). Порядок работ: [roadmap](./roadmap.md); контракты задач: [backlog](./agent-evolution-backlog-2026-10-17.md).

## Контекст

У Агата существуют durable процессы, bounded agent teams, policy, approvals, RAG и Golden eval. Фиксированный `internal-report v1` и один реальный синтетический qualification run дают узкую основу; полный сценарий с пользовательскими документами, human acceptance и повторяемым качеством ещё не подтверждён. Рынок также предлагает основные механизмы orchestration/evaluation. Число агентов не является достаточным критерием выбора архитектуры.

## Предлагаемое решение

1. Довести один report pack и выбранный local/cloud профиль до принятого результата. Доступность cloud или внешнего peer не становится зависимостью local pilot.
2. Закреплять профиль pack по EVAL-101/EVAL-103: deterministic операции в коде, bounded single agent как простой baseline, chain/team при измеренной пользе. Старый pack не меняется задним числом.
3. Сохранить Temporal/coordinator источником истины процесса, а stage checkpoint привязать к lease/attempt и fencing. Новый независимый durable checkpointer без reconciliation-контракта не вводить.
4. До расширения памяти, делегации и интерактивных долгих stages определить versioned contracts, бюджеты, cancellation, unknown outcomes и владельца результата.
5. Сохранить существующие policy/approval/data gates на execution boundary. Prompt, skill, memory, peer result и выбранная топология полномочий не добавляют.

## Альтернативы

| Вариант | Причина выбора или отказа | Условие пересмотра |
| --- | --- | --- |
| Текущая фиксированная цепочка | Сохраняется контрольным профилем; уже есть ограниченное evidence | Может остаться основным профилем, если paired eval показывает лучший результат |
| Один минимальный агентный workflow с измеряемым усложнением | Рекомендуется как направление: меньше новых компонентов, понятный контроль эффекта | Усложнение при подтверждённом качестве/затратах и demand |
| Multi-agent по умолчанию и отдельный orchestration plane | Больше координации и согласования state без подтверждённой пользы | Только после задач, которые требуют независимой параллельной работы или разных полномочий |
| Managed external runtime как основа | Уменьшает часть runtime-разработки, добавляет vendor/state/data dependency | Конкретный adapter/пилот после проверки хранения, бюджета, доступности и recovery |

## Последствия и переход

P0 получает минимальные validators/context limits и измерение профиля; dynamic skills, memory expansion и долгие sessions остаются P1. OpenRouter сохраняется первым запланированным inference adapter, полный external-agent UX CLD-006 переносится в P1. Это уменьшает зависимости первого local pilot, но не отменяет cloud scope и gates.

Миграции additive и versioned; старые manifests читаются. Переключение active pack/profile влияет на новые runs. Активный run продолжает свой snapshot либо явно останавливается. При неизвестном результате записи выполняется reconciliation, а не повтор. Memory revoke исключает содержание из новых contexts и caches по утверждённой retention policy; audit не становится обходом удаления данных.

Для выпуска каждого профиля нужны role/scopes, contract tests, offline quality/safety evidence, operator runbook, disable/rollback и qualification выбранного deployment. Существующие OPS-101–OPS-105 остаются обязательными перед соответствующим production использованием. Новый SLO, RTO/RPO или стоимость этим ADR не назначаются.

## Проверка и критерии отказа

- EVAL-101 фиксирует corpus/dev/holdout и минимум три trials; EVAL-103 сравнивает сопоставимые права/бюджеты и реальные outcomes. Критерий выигрыша и quality margin задаются до опыта; неизвестные затраты не равны нулю.
- Сложный профиль не повышается при ухудшении обязательного quality gate, запрещённом эффекте, неподтверждённом выигрыше или слишком широкой неопределённости.
- Если memory/skills on/off не показывают пользы, соответствующая опция остаётся выключенной для данного профиля. Если checkpoint допускает повтор необратимого действия, resume не выпускается.
- Local/cloud qualifications раздельны. Старый synthetic PASS и успешные проверки документов не заменяют новый пилот или human acceptance.

ADR может быть принят после инженерной оценки контрактов и фиксации владельцем процесса критериев первого пилота. Подготовка документации и backlog завершима до этого решения; реализация и выпуск проверяются своими gates.
