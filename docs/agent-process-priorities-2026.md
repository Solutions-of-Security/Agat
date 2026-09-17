# Приоритеты агентов и процессов: сентябрь 2026 — август 2027

Дата среза: 1 сентября 2026 года. Горизонт прогноза: 12 месяцев, до 31 августа 2027 года.

## Решение

Для Agat приоритетен не ещё один общий агент, а набор готовых **solution packs**: ограниченная роль, типовой процесс, коннекторы, схема результата, eval-набор, approval policy и измеримый KPI. Технический control plane Agat 1.7 уже закрывает значительную часть требований к durable execution, RAG, MCP policy, approvals, specialist teams, replay/eval, HA и SIEM. Основной продуктовый разрыв — упаковка этих возможностей в воспроизводимые бизнес-сценарии.

Рекомендуемый порядок:

1. **0–3 месяца, P0:** корпоративный исследователь, документный оператор, ITSM/ITOps и аналитик данных; общие supervisor и reviewer/guardian; пять сквозных процессов исследования, документов, IT, аналитики и контролируемого выпуска агентов.
2. **3–6 месяцев, P1:** разработка ПО, поддержка, финансы, договоры, закупки и security/compliance — после готовности соответствующих коннекторов, схем и eval-наборов.
3. **6–12 месяцев, P2:** продажи/RFP и HR/onboarding.
4. **Watch:** voice и browser/computer use до появления устойчивого качества, наблюдаемости и безопасного исполнения. Генерацию маркетингового контента не делать ядром позиционирования до product-market fit.

## Методика

Каждая категория получила пять порядковых оценок от 1 до 5:

- популярность сейчас;
- необходимость сейчас;
- ожидаемая популярность через 12 месяцев;
- ожидаемая необходимость через 12 месяцев;
- соответствие текущей архитектуре Agat.

Итоговый балл используется только для сортировки:

`приоритет = 20 × среднее пяти оценок`

Приоритет и календарная волна важнее небольших различий итогового балла. Это экспертная сценарная оценка, а не доля рынка и не вероятностный прогноз. Vendor-sponsored исследования могут иметь выборочное смещение; российский опрос автоматизации отражает смежный спрос и не равен доле автономных AI-агентов.

## Сводная таблица агентов

| Приоритет | Волна | Агент | Популярность сейчас | Необходимость сейчас | Популярность +12 мес. | Необходимость +12 мес. | Fit Agat | Балл |
|---|---|---|---:|---:|---:|---:|---:|---:|
| P0 | 0–3 мес. | Корпоративный исследователь / RAG | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | Документный оператор | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | ITSM / ITOps специалист | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | Аналитик данных и отчётности | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | Supervisor / координатор специалистов | 3 | 5 | 5 | 5 | 5 | 92 |
| P0 | 0–3 мес. | Reviewer / guardian / quality gate | 3 | 5 | 5 | 5 | 5 | 92 |
| P1 | 3–6 мес. | Агент разработки ПО | 5 | 5 | 5 | 5 | 4 | 96 |
| P1 | 3–6 мес. | Финансовый аналитик / контролёр | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | Клиентская поддержка | 5 | 5 | 5 | 5 | 4 | 96 |
| P1 | 3–6 мес. | Юридический / договорный специалист | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | Закупки / сравнение поставщиков | 4 | 4 | 5 | 5 | 5 | 92 |
| P2 | 6–12 мес. | Продажи / RFP / коммерческое предложение | 4 | 4 | 5 | 4 | 4 | 84 |
| P2 | 6–12 мес. | HR / рекрутинг / онбординг | 4 | 4 | 4 | 4 | 4 | 80 |
| Watch | после 6 мес. | Голосовой фронт-офис | 4 | 3 | 5 | 4 | 2 | 72 |
| Watch | после 9 мес. | Browser / computer-use оператор | 3 | 3 | 5 | 4 | 2 | 68 |
| Defer | после PMF | Маркетинг / генерация контента | 5 | 3 | 5 | 3 | 2 | 72 |

### Почему P0

- **Research/RAG:** исследование, knowledge management и работа с внутренним контекстом входят в ведущие production-сценарии; Agat уже имеет local RAG, provenance и управляемую память.
- **Документы:** документооборот и заявки — наиболее распространённый класс автоматизации в российском опросе; задача хорошо ограничивается схемой данных и approval.
- **ITSM/ITOps:** IT — один из лидирующих enterprise use cases, а действия можно ограничить runbook, MCP risk tiers и human approval.
- **Аналитика/отчётность:** research/data и data reporting востребованы уже сейчас и естественно используют traceable artifacts.
- **Supervisor и reviewer:** сами по себе они не всегда видимы пользователю, но становятся обязательным слоем при росте specialist teams и требований к качеству, политике и человеческой проверке.

## Сводная таблица процессов

| Приоритет | Волна | Процесс | Популярность сейчас | Необходимость сейчас | Популярность +12 мес. | Необходимость +12 мес. | Fit Agat | Балл |
|---|---|---|---:|---:|---:|---:|---:|---:|
| P0 | 0–3 мес. | Исследование → анализ → проверка → отчёт | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | Документ / заявка → извлечение → сверка → approval → архив | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | IT-инцидент / service request → решение → контролируемое действие | 5 | 5 | 5 | 5 | 5 | 100 |
| P0 | 0–3 мес. | Мониторинг данных → диагностика отклонения → управленческий отчёт | 4 | 5 | 5 | 5 | 5 | 96 |
| P0 | 0–3 мес. | Eval → policy review → release → observe → rollback | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | Issue → план → код → тесты → review → release gate | 5 | 5 | 5 | 5 | 4 | 96 |
| P1 | 3–6 мес. | Запрос клиента → ответ / действие → эскалация → QA | 5 | 5 | 5 | 5 | 4 | 96 |
| P1 | 3–6 мес. | Финансовое закрытие / сверка / прогноз → sign-off | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | Договор → clauses → risk → redline → approval → учёт | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | КП поставщиков → сравнение → риск → заявка → approval | 4 | 5 | 5 | 5 | 5 | 96 |
| P1 | 3–6 мес. | Security / compliance alert → evidence → risk → response | 3 | 5 | 5 | 5 | 5 | 92 |
| P2 | 6–12 мес. | Lead / RFP → исследование → предложение → review → CRM | 4 | 4 | 5 | 4 | 4 | 84 |
| P2 | 6–12 мес. | Онбординг / офбординг сотрудника | 4 | 4 | 4 | 4 | 4 | 80 |
| Watch | после 6 мес. | Голосовой запрос → бронирование / первая линия → человек | 4 | 3 | 5 | 4 | 2 | 72 |
| Watch | после 9 мес. | Cross-app browser execution без API | 3 | 3 | 5 | 4 | 2 | 68 |
| Defer | после PMF | Контент-кампания → brand review → публикация | 5 | 3 | 5 | 3 | 2 | 72 |

## Прогноз на следующие 12 месяцев

Наиболее устойчивый сдвиг — от универсальных чат-ассистентов к task-specific агентам и совместным командам специалистов. Поэтому популярность supervisor/reviewer, release governance, observability и human-in-the-loop будет расти вместе с числом прикладных агентов. Высокая частота экспериментов не означает готовность к полной автономии: контроль качества и governance остаются ограничителями production.

На горизонте 3–6 месяцев сильнее всего выглядят сценарии, где одновременно присутствуют большой объём повторяемой интеллектуальной работы, структурированный результат и ясный владелец решения: software engineering, support, finance, legal и procurement. На горизонте 6–12 месяцев можно расширяться в sales/RFP и HR после появления готовых интеграций и доменных eval-наборов.

Voice и computer use будут заметны и популярны, но для Agat их следует считать экспериментальным каналом, а не основой продуктового обещания: действие через UI хуже ограничивается, сложнее воспроизводится и требует более дорогой проверки, чем API/MCP-интеграция.

## Рекомендуемый состав solution pack

Каждый приоритетный пакет должен включать:

1. один entry-point агент и ограниченную команду специалистов;
2. version-pinned процесс с явными состояниями, таймаутами, compensation и approval;
3. готовые коннекторы к системам записи;
4. JSON Schema результата и правила provenance;
5. golden eval dataset, offline gate и production SLO;
6. MCP policy, risk tiers, least-privilege credentials и four-eyes для высокорисковых действий;
7. dashboard ценности: время цикла, доля straight-through completion, escalation rate, error/rework rate и стоимость успешного результата.

## Проверенные рыночные сигналы

- Microsoft сообщает о росте числа активных агентов Microsoft 365 в 15 раз год к году и подчёркивает сдвиг к quality control и critical thinking: [Work Trend Index 2026](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization).
- В опросе более 1 300 специалистов 57,3% сообщили о production-агентах; лидируют customer service, research/data и internal workflow automation: [LangChain State of Agent Engineering 2026](https://www.langchain.com/state-of-agent-engineering).
- McKinsey фиксирует 23% организаций, масштабировавших agentic AI хотя бы в одной функции, и ещё 39% в эксперименте; максимальные внедрения — в IT и knowledge management: [State of AI 2025](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai).
- В российском опросе чаще всего автоматизируются документы и заявки — 70%, затем бухгалтерия и финансы — 55%; объединённую категорию AI-агентов и ассистентов используют 39% компаний: [СберАналитика](https://sberanalytics.ru/researches/automation).
- Dynatrace указывает ITOps/DevOps, software engineering и support среди ведущих областей внедрения; 69% решений всё ещё проверяются человеком: [Pulse of Agentic AI 2026](https://www.dynatrace.com/news/press-release/pulse-of-agentic-ai-2026/).
- Gartner прогнозирует task-specific agents в 40% enterprise applications к концу 2026 года, но отдельно предупреждает о риске массового вывода автономных агентов из эксплуатации при слабом governance: [прогноз task-specific agents](https://www.gartner.com/en/newsroom/press-releases/2025-08-26-gartner-predicts-40-percent-of-enterprise-apps-will-feature-task-specific-ai-agents-by-2026-up-from-less-than-5-percent-in-2025), [proportional governance](https://www.gartner.com/en/newsroom/press-releases/2026-05-26-gartner-says-applying-uniform-governance-across-ai-agents-will-lead-to-enterprise-ai-agent-failure).
- Дополнительные сигналы по enterprise workflows: [OpenAI](https://openai.com/index/how-enterprises-put-ai-to-work/), [KPMG Finance](https://kpmg.com/xx/en/our-insights/ai-and-technology/kpmg-global-ai-in-finance-report.html), [Salesforce Service](https://www.salesforce.com/news/stories/ai-service-agents-improve-customer-satisfaction/), [Sinch governance](https://sinch.com/news/sinch-releases-ai-production-paradox/), [Yandex AI Studio](https://yandex.ru/company/news/15-04-2026-01), [Yandex business agent](https://yandex.ru/company/news/30-07-2026-03), [ТеДо](https://data.tedo.ru/publications/AI-agents-current-realities-and-prospects.pdf).

## Связанные артефакты

- Общий roadmap с эпиками Solution packs: [`roadmap.md`](./roadmap.md)
- Интерактивный отчёт: [`agent-process-priority-report/dist/index.html`](./agent-process-priority-report/dist/index.html)
- Исходные данные и provenance: [`agent-process-priority-report/src/data.json`](./agent-process-priority-report/src/data.json)
- Компонент отчёта: [`agent-process-priority-report/src/content/report/ReportContent.jsx`](./agent-process-priority-report/src/content/report/ReportContent.jsx)
