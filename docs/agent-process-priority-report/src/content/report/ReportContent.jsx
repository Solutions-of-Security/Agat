import React from "react";

import {
  DataTable, MetricCard, ReportSection, RichNarrative, useDataApp,
} from "../../data-app-public.jsx";

const SOURCE_PREVIEWS = {
  "https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization": {
    title: "2026 Work Trend Index",
    summary: "Телеметрия Microsoft 365 и опрос 20 000 AI-пользователей показывают 15-кратный рост активных агентов год к году и подчёркивают роль повторяемых workflow, human judgment и evaluation infrastructure.",
    source: "Microsoft",
    date: "2026-05-05",
    approvedForReport: true,
  },
  "https://www.langchain.com/state-of-agent-engineering": {
    title: "State of Agent Engineering 2026",
    summary: "В опросе более 1 300 специалистов 57,3% сообщили о production-агентах; лидируют customer service, research/data analysis и internal workflow automation. Quality, security и observability остаются главными ограничителями.",
    source: "LangChain",
    date: "2026-06-12",
    approvedForReport: true,
  },
  "https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai": {
    title: "The state of AI in 2025",
    summary: "McKinsey фиксирует 23% организаций, масштабирующих agentic AI, ещё 39% — экспериментирующих. Чаще всего агенты встречаются в IT и knowledge management; redesign workflow связан с более высокой отдачей.",
    source: "McKinsey",
    date: "2025-11-05",
    approvedForReport: true,
  },
  "https://sberanalytics.ru/researches/automation": {
    title: "Автоматизация российского бизнеса",
    summary: "Российский опрос показывает максимальную автоматизацию документооборота и заявок (70%) и финансового учёта (55%); ИИ-агентов или ИИ-ассистентов используют 39% компаний.",
    source: "СберАналитика",
    date: "2026-01-22",
    approvedForReport: true,
  },
  "https://www.dynatrace.com/news/press-release/pulse-of-agentic-ai-2026/": {
    title: "The Pulse of Agentic AI 2026",
    summary: "В опросе 919 руководителей наиболее часты ITOps/DevOps, software engineering и customer support. 69% agentic-решений проверяются людьми, а security и управление агентами в масштабе остаются ключевыми барьерами.",
    source: "Dynatrace",
    date: "2026-01-22",
    approvedForReport: true,
  },
  "https://www.gartner.com/en/newsroom/press-releases/2026-05-26-gartner-says-applying-uniform-governance-across-ai-agents-will-lead-to-enterprise-ai-agent-failure": {
    title: "Proportional governance for AI agents",
    summary: "Gartner рекомендует соотносить controls с уровнем автономии и прогнозирует, что к 2027 году 40% предприятий понизят автономность или отключат агентов после выявления governance gaps.",
    source: "Gartner",
    date: "2026-05-26",
    approvedForReport: true,
  },
};

const priorityOrder = { P0: 0, P1: 1, P2: 2, Watch: 3, Defer: 4 };

const portfolioColumns = [
  { field: "kind", label: "Тип", presentation: "status" },
  { field: "priority", label: "Приоритет", presentation: "status" },
  { field: "wave", label: "Горизонт" },
  { field: "item", label: "Агент / процесс", presentation: "identity", secondaryField: "summary" },
  { field: "popularNow", label: "Популярность сейчас" },
  { field: "neededNow", label: "Необходимость сейчас" },
  { field: "popular12m", label: "Популярность +12м" },
  { field: "needed12m", label: "Необходимость +12м" },
  { field: "agatFit", label: "Fit Agat" },
  { field: "priorityScore", label: "Итог /100" },
];

const agentColumns = [
  { field: "priority", label: "Приоритет", presentation: "status" },
  { field: "wave", label: "Горизонт" },
  { field: "agent", label: "Агент", presentation: "identity", secondaryField: "role" },
  { field: "popularNow", label: "Поп. сейчас" },
  { field: "neededNow", label: "Нужен сейчас" },
  { field: "popular12m", label: "Поп. +12м" },
  { field: "needed12m", label: "Нужен +12м" },
  { field: "agatFit", label: "Fit" },
  { field: "priorityScore", label: "Итог" },
  { field: "rationale", label: "Почему" },
];

const processColumns = [
  { field: "priority", label: "Приоритет", presentation: "status" },
  { field: "wave", label: "Горизонт" },
  { field: "process", label: "Процесс", presentation: "identity", secondaryField: "flow" },
  { field: "popularNow", label: "Поп. сейчас" },
  { field: "neededNow", label: "Нужен сейчас" },
  { field: "popular12m", label: "Поп. +12м" },
  { field: "needed12m", label: "Нужен +12м" },
  { field: "agatFit", label: "Fit" },
  { field: "priorityScore", label: "Итог" },
  { field: "why", label: "Почему" },
];

const waveColumns = [
  { field: "wave", label: "Волна", presentation: "identity", secondaryField: "objective" },
  { field: "agents", label: "Агенты" },
  { field: "processes", label: "Процессы" },
  { field: "dependencies", label: "Зависимости" },
  { field: "exitCriteria", label: "Критерий выхода" },
];

const readinessColumns = [
  { field: "capability", label: "Capability", presentation: "identity", secondaryField: "evidence" },
  { field: "status", label: "Статус", presentation: "status" },
  { field: "implication", label: "Вывод" },
];

export function ReportContent() {
  const { reviewedRows, canEdit, mode, appTitle, setAppTitle } = useDataApp();
  const signals = reviewedRows("market_signals");
  const agents = reviewedRows("agent_priorities");
  const processes = reviewedRows("process_priorities");
  const waves = reviewedRows("roadmap_waves");
  const readiness = reviewedRows("agat_readiness");

  const signal = (id) => signals.find((row) => row.signalId === id);
  const portfolio = [
    ...agents.map((row) => ({
      kind: "Агент", priority: row.priority, wave: row.wave, item: row.agent, summary: row.role,
      popularNow: row.popularNow, neededNow: row.neededNow, popular12m: row.popular12m,
      needed12m: row.needed12m, agatFit: row.agatFit, priorityScore: row.priorityScore,
    })),
    ...processes.map((row) => ({
      kind: "Процесс", priority: row.priority, wave: row.wave, item: row.process, summary: row.flow,
      popularNow: row.popularNow, neededNow: row.neededNow, popular12m: row.popular12m,
      needed12m: row.needed12m, agatFit: row.agatFit, priorityScore: row.priorityScore,
    })),
  ].sort((left, right) => (priorityOrder[left.priority] ?? 9) - (priorityOrder[right.priority] ?? 9)
    || right.priorityScore - left.priorityScore || left.kind.localeCompare(right.kind, "ru"));

  return <article className="report-content" aria-label="Приоритеты агентов и процессов Agat">
    <header className="report-hero">
      <h1 data-data-app-title contentEditable={canEdit && mode === "edit"} suppressContentEditableWarning
        aria-label={canEdit && mode === "edit" ? "Изменить заголовок отчёта" : undefined}
        onBlur={canEdit && mode === "edit" ? (event) => setAppTitle(event.currentTarget.textContent.trim() || appTitle) : undefined}
        onKeyDown={canEdit && mode === "edit" ? (event) => {
          if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
        } : undefined}>{appTitle}</h1>
      <RichNarrative id="report:intro" className="report-deck" label="Изменить введение"
        value="Рыночный срез на **1 сентября 2026 года** и базовый прогноз до **31 августа 2027 года**. Фокус: корпоративные AI-агенты и процессы, наиболее релевантные local-first, regulated и on-prem целевому сегменту Agat." />
    </header>

    <ReportSection id="executive-summary" queryId="market_signals"
      queryIds={["market_signals", "agent_priorities", "process_priorities", "agat_readiness"]}
      sourceRowsByQuery={{ market_signals: signals, agent_priorities: agents, process_priorities: processes, agat_readiness: readiness }}
      showHeading={false}>
      <RichNarrative id="executive-summary:body" className="report-summary" label="Изменить вывод"
        sourcePreviews={SOURCE_PREVIEWS}
        value={`## Главный вывод

Agat не нужно начинать с универсального «цифрового сотрудника». Наиболее защищённая ставка — **шесть узких базовых ролей** (Researcher, Document Operator, Data Analyst, ITSM, Supervisor и Reviewer) и **пять контролируемых P0-процессов**: research-to-report, document approval, IT incident, data-to-report и agent release governance.

Такой выбор отражает одновременно текущий спрос и архитектурный fit. [LangChain](https://www.langchain.com/state-of-agent-engineering) ставит customer service и research/data analysis во главе production-use cases; [McKinsey](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai) — IT и knowledge management; российский срез [СберАналитики](https://sberanalytics.ru/researches/automation) — документы, заявки и финансы. При этом [Microsoft](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization) и [Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-05-26-gartner-says-applying-uniform-governance-across-ai-agents-will-lead-to-enterprise-ai-agent-failure) показывают, что рост агентности повышает, а не отменяет потребность в evaluation, permissions, approvals и human accountability.

Техническая база Agat 1.7 уже достаточна. Главный продуктовый пробел — **solution pack**: единый устанавливаемый bundle из агентов, process version, knowledge requirements, MCP tools/policy, golden eval, KPI и human-escape contract.`} />
    </ReportSection>

    <section className="signal-grid" aria-label="Ключевые рыночные сигналы">
      <MetricCard id="signal-m365" title="Рост активных агентов" queryId="market_signals"
        sourceRows={[signal("m365-active-agents")]} value="15×" comparison="год к году · Microsoft 365"
        description={signal("m365-active-agents")?.scope} />
      <MetricCard id="signal-production" title="Агенты в production" queryId="market_signals"
        sourceRows={[signal("production-agents")]} value="57,3%" comparison="опрос 1 300+ специалистов"
        description={signal("production-agents")?.scope} />
      <MetricCard id="signal-russia" title="Использование в России" queryId="market_signals"
        sourceRows={[signal("russia-agents")]} value="39%" comparison="агенты или ассистенты"
        description={signal("russia-agents")?.scope} />
      <MetricCard id="signal-hitl" title="Human verification" queryId="market_signals"
        sourceRows={[signal("human-verification")]} value="69%" comparison="агентных решений"
        description={signal("human-verification")?.scope} />
    </section>

    <ReportSection id="portfolio-table" queryId="agent_priorities"
      queryIds={["agent_priorities", "process_priorities"]}
      sourceRowsByQuery={{ agent_priorities: agents, process_priorities: processes }} showHeading={false}>
      <RichNarrative id="portfolio-table:intro" className="section-intro" label="Изменить пояснение сводной таблицы"
        value="## Сводная матрица агентов и процессов\n\nШкала 1–5 является порядковой. Итоговый балл — среднее популярности и необходимости сейчас, прогноза на 12 месяцев и соответствия Agat. Для продуктовой последовательности важнее `P0/P1/P2`, чем небольшая разница баллов внутри одной волны." />
      <div className="report-table" data-reviewed-rows>
        <DataTable rows={portfolio} columns={portfolioColumns} caption="Сводная матрица агентов и процессов"
          searchable pageSize={12} compactNumbers={false} />
      </div>
    </ReportSection>

    <ReportSection id="agents-table" queryId="agent_priorities" sourceRows={agents} showHeading={false}>
      <RichNarrative id="agents-table:intro" className="section-intro" label="Изменить пояснение к агентам"
        value="## Какие агенты нужны сейчас и в следующие 12 месяцев\n\nP0 — переиспользуемые роли и первый IT-oriented pack. P1 — domain packs, зависящие от коннекторов. P2 — расширение после подтверждения повторяемого спроса. Watch/Defer сохраняют рыночный сигнал, но не должны перехватывать roadmap." />
      <div className="report-table" data-reviewed-rows>
        <DataTable rows={agents} columns={agentColumns} caption="Приоритеты типов агентов"
          searchable pageSize={16} compactNumbers={false} />
      </div>
    </ReportSection>

    <ReportSection id="processes-table" queryId="process_priorities" sourceRows={processes} showHeading={false}>
      <RichNarrative id="processes-table:intro" className="section-intro" label="Изменить пояснение к процессам"
        value="## Какие процессы нужны сейчас и в следующие 12 месяцев\n\nПриоритет получают не самые длинные процессы, а те, где есть повторяемый вход, проверяемый результат, понятный владелец решения, измеримый KPI и безопасная точка human handoff." />
      <div className="report-table" data-reviewed-rows>
        <DataTable rows={processes} columns={processColumns} caption="Приоритеты процессов"
          searchable pageSize={16} compactNumbers={false} />
      </div>
    </ReportSection>

    <ReportSection id="waves-table" queryId="roadmap_waves" sourceRows={waves} showHeading={false}>
      <RichNarrative id="waves-table:intro" className="section-intro" label="Изменить план волн"
        value="## Рекомендуемая последовательность\n\nГоризонты — порядок productization, а не обещание сроков без оценки capacity. Каждая волна заканчивается бизнес-критерием, а не фактом появления карточки в каталоге." />
      <div className="report-table" data-reviewed-rows>
        <DataTable rows={waves} columns={waveColumns} caption="Волны продуктовой упаковки"
          searchable={false} pageSize={8} compactNumbers={false} />
      </div>
    </ReportSection>

    <ReportSection id="readiness-table" queryId="agat_readiness" sourceRows={readiness} showHeading={false}>
      <RichNarrative id="readiness-table:intro" className="section-intro" label="Изменить gap-анализ"
        value="## Что уже готово в Agat и чего не хватает\n\nRuntime, governance и enterprise data plane уже сильнее, чем каталог готовых решений. Поэтому следующий прирост ценности должен идти через упаковку, коннекторы, structured outputs и business KPI, а не через ещё один orchestration primitive." />
      <div className="report-table" data-reviewed-rows>
        <DataTable rows={readiness} columns={readinessColumns} caption="Готовность Agat к приоритетным сценариям"
          searchable={false} pageSize={12} compactNumbers={false} />
      </div>
    </ReportSection>

    <ReportSection id="recommendation" queryId="agat_readiness"
      queryIds={["agat_readiness", "roadmap_waves"]}
      sourceRowsByQuery={{ agat_readiness: readiness, roadmap_waves: waves }} showHeading={false}>
      <RichNarrative id="recommendation:body" className="report-recommendation" label="Изменить рекомендацию"
        value={`## Решение для roadmap

1. Ввести **solution-pack manifest**: версии агентов и процесса, required MCP tools, risk policy, knowledge collections, output schema, eval dataset, KPI и rollback/human-escape правила.
2. За первые 0–3 месяца выпустить **пять P0 packs** и шесть базовых ролей; использовать существующие process templates и immutable snapshots.
3. Делать коннекторы не «по рынку вообще», а пакетами: **ITSM+SIEM**, **ЭДО+1C/ERP**, **Git+CI**, затем **CRM/CPQ** и **HRIS+IdP**.
4. Добавить общий **structured business output contract** и pack-level KPI: cycle time, touchless rate, escalation rate, factual/field accuracy, approval reject rate, rollback rate и cost per successful outcome.
5. Voice, generic computer use и mass-content оставить в watchlist до повторяемого платного спроса.`} />
    </ReportSection>

    <ReportSection id="methodology" queryId="agent_priorities"
      queryIds={["agent_priorities", "process_priorities", "market_signals"]}
      sourceRowsByQuery={{ agent_priorities: agents, process_priorities: processes, market_signals: signals }} showHeading={false}>
      <RichNarrative id="methodology:body" className="report-method" label="Изменить методологию"
        sourcePreviews={SOURCE_PREVIEWS}
        value={`## Метод и ограничения

- **Популярность** — текущая частота внедрения/инвестиций и согласованность нескольких источников. **Необходимость** — повторяемость, business criticality, риск и ценность контролируемого исполнения. **Fit Agat** — соответствие shipped-возможностям 1.7.
- 1–5 — порядковая экспертная шкала; баллы не являются market share, forecast revenue или статистической вероятностью.
- Российский опрос автоматизации используется как индикатор process demand, но не доказывает, что все эти процессы уже агентные.
- Vendor surveys могут быть смещены к собственной клиентской базе. Выводы проверены на согласованность между [Microsoft](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization), [LangChain](https://www.langchain.com/state-of-agent-engineering), [McKinsey](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai), [Dynatrace](https://www.dynatrace.com/news/press-release/pulse-of-agentic-ai-2026/) и российскими данными.
- Прогноз предполагает отсутствие резкого регуляторного запрета или скачка model reliability. При ухудшении качества/безопасности растёт приоритет Reviewer, eval и human gates; при улучшении — сначала растёт объём task-specific и collaborative packs, а не безусловная автономия.`} />
    </ReportSection>
  </article>;
}
