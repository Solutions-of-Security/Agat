import { useEffect, useState } from "react";

import type {
  AgatEvent,
  Agent,
  CredentialSummary,
  ProcessConditionOperator,
  ProcessDefinition,
  ProcessGraphNode,
  StageStatus,
  TestProcessNodeResult,
} from "../types";
import { AccessibleTabList, TabPanel, type TabDefinition } from "./AccessibleTabs";
import { Icon } from "./Icon";

const operatorLabels: Record<ProcessConditionOperator, string> = {
  always: "Всегда",
  contains: "Содержит",
  not_contains: "Не содержит",
  equals: "Равно",
  not_equals: "Не равно",
};

const statusLabels: Record<StageStatus | "visited", string> = {
  pending: "Ожидает",
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Ждёт решения",
  waiting_external: "Ждёт signal",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Остановлен",
  visited: "Пройден",
};

type InspectorTab = "parameters" | "input" | "output" | "logs";

export interface ProcessNodeExecutionDetails {
  status: StageStatus | "visited";
  input: unknown;
  output: string | null;
  events: AgatEvent[];
  attempt: number | null;
  startedAt: string | null;
  completedAt: string | null;
}

interface ProcessInspectorProps {
  node: ProcessGraphNode | null;
  agents: Agent[];
  credentials: CredentialSummary[];
  processes: ProcessDefinition[];
  currentProcessId: string | null;
  processNodes: ProcessGraphNode[];
  execution: ProcessNodeExecutionDetails | null;
  executionLoading: boolean;
  defaultInput: string;
  testResult: TestProcessNodeResult | null;
  testBusy: boolean;
  canRunFrom: boolean;
  onChange: (node: ProcessGraphNode) => void;
  onDelete: (nodeId: string) => void;
  onClose: () => void;
  onTest: (node: ProcessGraphNode, input: string) => void;
  onRunFrom: (node: ProcessGraphNode, input: string) => void;
  onOpenRun: (runId: string) => void;
  onManageCredentials: () => void;
}

function nodeTypeLabel(node: ProcessGraphNode): string {
  if (node.type === "start") return "Старт процесса";
  if (node.type === "agent") return "ИИ-агент";
  if (node.type === "http") return "Интеграция · HTTP";
  if (node.type === "transform") return "Данные · преобразование";
  if (node.type === "wait") return "Управление потоком · ожидание";
  if (node.type === "approval") return "Согласование с человеком";
  if (node.type === "artifact") return "Результат · артефакт";
  if (node.type === "condition") return "Условие";
  if (node.type === "loop") return "Цикл";
  if (node.type === "parallel_fork") return "Параллельные ветки";
  if (node.type === "parallel_join") return "Объединение веток";
  if (node.type === "signal") return "Ожидание внешнего события";
  if (node.type === "subprocess") return "Вложенный процесс";
  return "Завершение процесса";
}

function formatExecutionValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "Данные отсутствуют";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function ParametersTab({
  node,
  agents,
  credentials,
  processes,
  currentProcessId,
  processNodes,
  onManageCredentials,
  onChange,
}: {
  node: ProcessGraphNode;
  agents: Agent[];
  credentials: CredentialSummary[];
  processes: ProcessDefinition[];
  currentProcessId: string | null;
  processNodes: ProcessGraphNode[];
  onManageCredentials: () => void;
  onChange: (node: ProcessGraphNode) => void;
}) {
  const condition = node.config.condition ?? {
    source: "last_output" as const,
    operator: "contains" as const,
    value: "",
    caseSensitive: false,
  };
  const assignedAgent = agents.find((agent) => agent.id === node.config.agentId);

  function updateConfig(config: ProcessGraphNode["config"]) {
    onChange({ ...node, config });
  }

  return (
    <div className="process-inspector__parameters">
      <label className="field">
        <span>Название</span>
        <input value={node.name} maxLength={80} onChange={(event) => onChange({ ...node, name: event.target.value })} />
      </label>

      {node.type === "start" ? (
        <label className="field">
          <span>Шаблон входных данных</span>
          <textarea
            rows={16}
            maxLength={100_000}
            value={node.config.inputTemplate ?? ""}
            placeholder="Параметры, которые нужно заполнить перед запуском процесса"
            onChange={(event) => updateConfig({ ...node.config, inputTemplate: event.target.value })}
          />
          <small>Этот текст подставляется в поле «Входные данные» при запуске. Укажите значения по умолчанию и отметьте параметры для заполнения. Для доступов используйте ссылки на сохранённые секреты.</small>
        </label>
      ) : null}

      {node.type === "agent" ? (
        <>
          <label className="field">
            <span>Агент</span>
            <select
              value={node.config.agentId ?? ""}
              onChange={(event) => updateConfig({ ...node.config, agentId: event.target.value })}
            >
              <option value="" disabled>Выберите агента</option>
              {agents.map((agent) => (
                <option value={agent.id} key={agent.id}>{agent.name} · {agent.runtime}</option>
              ))}
            </select>
          </label>
          {assignedAgent ? <div className="process-agent-context"><p>{assignedAgent.role}</p><span>Модель: <strong>{assignedAgent.model ?? "Автовыбор"}</strong></span><details><summary>Инструкция агента</summary><p>{assignedAgent.systemPrompt}</p></details></div> : <p className="process-field-help">Выберите агента, который выполнит этот шаг.</p>}
          <label className="approval-toggle process-approval-toggle">
            <input
              type="checkbox"
              checked={node.config.approvalRequired === true}
              onChange={(event) => updateConfig({ ...node.config, approvalRequired: event.target.checked })}
            />
            <span><strong>Подтверждение оператора</strong><small>Шаг попадёт в очередь только после решения.</small></span>
          </label>
        </>
      ) : null}

      {node.type === "http" ? (
        <>
          <div className="process-field-grid process-field-grid--method">
            <label className="field">
              <span>Метод</span>
              <select value={node.config.method ?? "GET"} onChange={(event) => updateConfig({ ...node.config, method: event.target.value as NonNullable<ProcessGraphNode["config"]["method"]> })}>
                {(["GET", "POST", "PUT", "PATCH", "DELETE"] as const).map((method) => <option value={method} key={method}>{method}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Тайм-аут, сек.</span>
              <input type="number" min={1} max={120} value={node.config.timeoutSeconds ?? 30} onChange={(event) => updateConfig({ ...node.config, timeoutSeconds: Number(event.target.value) })} />
            </label>
          </div>
          <label className="field">
            <span>Публичный URL</span>
            <input value={node.config.url ?? ""} maxLength={4_000} placeholder="https://api.example.com/items/{{ json.id }}" onChange={(event) => updateConfig({ ...node.config, url: event.target.value })} />
          </label>
          <label className="field">
            <span>Заголовок защиты от повторов</span>
            <input
              value={node.config.idempotencyHeader ?? "Idempotency-Key"}
              maxLength={80}
              onChange={(event) => updateConfig({ ...node.config, idempotencyHeader: event.target.value })}
            />
            <small>Ключ стабилен для повторной попытки одного шага.</small>
          </label>
          <label className="field">
            <span>Доступ к сервису</span>
            <select value={node.config.credentialId ?? ""} onChange={(event) => updateConfig({ ...node.config, credentialId: event.target.value || undefined })}>
              <option value="">Без авторизации</option>
              {credentials.filter((credential) => credential.scope.kind === "project").map((credential) => <option value={credential.id} key={credential.id}>{credential.name}</option>)}
            </select>
          </label>
          <button className="process-inline-action" type="button" onClick={onManageCredentials}><Icon name="shield" size={15} />Настроить доступ</button>
          <HttpHeadersEditor node={node} onChange={onChange} />
          {!(["GET", "DELETE"] as string[]).includes(node.config.method ?? "GET") ? (
            <label className="field">
              <span>Тело запроса</span>
              <textarea rows={6} value={node.config.body ?? ""} maxLength={100_000} placeholder={'{"text":"{{ lastOutput }}"}'} onChange={(event) => updateConfig({ ...node.config, body: event.target.value })} />
            </label>
          ) : null}
          <ExpressionHelp />
          <label className="approval-toggle process-approval-toggle">
            <input
              type="checkbox"
              checked={Boolean(node.config.compensation)}
              onChange={(event) => updateConfig({
                ...node.config,
                compensation: event.target.checked ? {
                  method: "POST",
                  url: "",
                  headers: {},
                  body: "",
                  timeoutSeconds: 30,
                } : undefined,
              })}
            />
            <span><strong>Действие при откате</strong><small>Выполнится в обратном порядке при ошибке или отмене.</small></span>
          </label>
          {node.config.compensation ? (
            <fieldset className="process-compensation-fields">
              <legend>Компенсирующий HTTP-вызов</legend>
              <div className="process-field-grid process-field-grid--method">
                <label className="field">
                  <span>Метод</span>
                  <select
                    value={node.config.compensation.method}
                    onChange={(event) => updateConfig({
                      ...node.config,
                      compensation: { ...node.config.compensation!, method: event.target.value as NonNullable<ProcessGraphNode["config"]["method"]> },
                    })}
                  >
                    {(["GET", "POST", "PUT", "PATCH", "DELETE"] as const).map((method) => <option value={method} key={method}>{method}</option>)}
                  </select>
                </label>
                <label className="field">
                  <span>Тайм-аут, сек.</span>
                  <input type="number" min={1} max={120} value={node.config.compensation.timeoutSeconds} onChange={(event) => updateConfig({
                    ...node.config,
                    compensation: { ...node.config.compensation!, timeoutSeconds: Number(event.target.value) },
                  })} />
                </label>
              </div>
              <label className="field">
                <span>URL</span>
                <input value={node.config.compensation.url} maxLength={4_000} onChange={(event) => updateConfig({
                  ...node.config,
                  compensation: { ...node.config.compensation!, url: event.target.value },
                })} />
              </label>
              <label className="field">
                <span>Доступ к сервису</span>
                <select value={node.config.compensation.credentialId ?? ""} onChange={(event) => updateConfig({
                  ...node.config,
                  compensation: { ...node.config.compensation!, credentialId: event.target.value || undefined },
                })}>
                  <option value="">Без авторизации</option>
                  {credentials.filter((credential) => credential.scope.kind === "project").map((credential) => <option value={credential.id} key={credential.id}>{credential.name}</option>)}
                </select>
              </label>
              <CompensationHeadersEditor node={node} onChange={onChange} />
              {!(["GET", "DELETE"] as string[]).includes(node.config.compensation.method) ? (
                <label className="field">
                  <span>Тело запроса</span>
                  <textarea rows={5} value={node.config.compensation.body} maxLength={100_000} onChange={(event) => updateConfig({
                    ...node.config,
                    compensation: { ...node.config.compensation!, body: event.target.value },
                  })} />
                </label>
              ) : null}
            </fieldset>
          ) : null}
        </>
      ) : null}

      {node.type === "transform" ? (
        <>
          <label className="field">
            <span>Шаблон результата</span>
            <textarea rows={9} value={node.config.template ?? ""} maxLength={100_000} placeholder="{{ lastOutput }}" onChange={(event) => updateConfig({ ...node.config, template: event.target.value })} />
          </label>
          <ExpressionHelp />
        </>
      ) : null}

      {node.type === "wait" ? (
        <label className="field">
          <span>Ожидание, секунд</span>
          <input type="number" min={1} max={604_800} value={node.config.waitSeconds ?? 60} onChange={(event) => updateConfig({ ...node.config, waitSeconds: Number(event.target.value) })} />
        </label>
      ) : null}

      {node.type === "approval" ? (
        <label className="field">
          <span>Сообщение оператору</span>
          <textarea rows={5} maxLength={2_000} value={node.config.approvalMessage ?? ""} onChange={(event) => updateConfig({ ...node.config, approvalMessage: event.target.value })} />
        </label>
      ) : null}

      {node.type === "artifact" ? (
        <>
          <label className="field">
            <span>Имя файла</span>
            <input maxLength={160} value={node.config.artifactName ?? ""} placeholder="result.md" onChange={(event) => updateConfig({ ...node.config, artifactName: event.target.value })} />
          </label>
          <label className="field">
            <span>Media type</span>
            <input maxLength={120} value={node.config.artifactMediaType ?? ""} placeholder="text/markdown; charset=utf-8" onChange={(event) => updateConfig({ ...node.config, artifactMediaType: event.target.value })} />
          </label>
          <label className="field">
            <span>Содержимое</span>
            <textarea rows={8} maxLength={100_000} value={node.config.artifactContent ?? ""} placeholder="{{ lastOutput }}" onChange={(event) => updateConfig({ ...node.config, artifactContent: event.target.value })} />
          </label>
          <ExpressionHelp />
        </>
      ) : null}

      {node.type === "condition" || node.type === "loop" ? (
        <>
          <label className="field">
            <span>{node.type === "loop" ? "Режим" : "Проверять"}</span>
            <select disabled><option>{node.type === "loop" ? "Пока условие истинно" : "Последний результат"}</option></select>
          </label>
          <label className="field">
            <span>Условие</span>
            <select
              value={condition.operator}
              onChange={(event) => updateConfig({
                ...node.config,
                condition: { ...condition, operator: event.target.value as ProcessConditionOperator },
              })}
            >
              {Object.entries(operatorLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          {condition.operator !== "always" ? (
            <label className="field">
              <span>Значение</span>
              <input
                value={condition.value}
                maxLength={2_000}
                placeholder="Например, доработать"
                onChange={(event) => updateConfig({
                  ...node.config,
                  condition: { ...condition, value: event.target.value },
                })}
              />
            </label>
          ) : null}
          <label className="approval-toggle process-approval-toggle">
            <input
              type="checkbox"
              checked={condition.caseSensitive}
              onChange={(event) => updateConfig({
                ...node.config,
                condition: { ...condition, caseSensitive: event.target.checked },
              })}
            />
            <span><strong>Учитывать регистр</strong><small>«Готово» и «готово» будут различаться.</small></span>
          </label>
        </>
      ) : null}

      {node.type === "loop" ? (
        <>
          <label className="field">
            <span>Максимум итераций</span>
            <input
              type="number"
              min={1}
              max={50}
              value={node.config.maxIterations ?? 3}
              onChange={(event) => updateConfig({ ...node.config, maxIterations: Number(event.target.value) })}
            />
          </label>
          <div className="process-loop-warning"><Icon name="warning" size={18} /><span>Лимит защищает процесс от бесконечного цикла.</span></div>
        </>
      ) : null}

      {node.type === "parallel_fork" ? (
        <div className="process-loop-warning"><Icon name="network" size={18} /><span>Соедините fork минимум с двумя ветками default.</span></div>
      ) : null}

      {node.type === "parallel_join" ? (
        <label className="field">
          <span>Парный fork gateway</span>
          <select value={node.config.forkId ?? ""} onChange={(event) => updateConfig({ ...node.config, forkId: event.target.value })}>
            <option value="" disabled>Выберите начало веток</option>
            {processNodes.filter((candidate) => candidate.type === "parallel_fork").map((candidate) => (
              <option value={candidate.id} key={candidate.id}>{candidate.name}</option>
            ))}
          </select>
        </label>
      ) : null}

      {node.type === "signal" ? (
        <>
          <label className="field">
            <span>Имя события</span>
            <input value={node.config.signalName ?? ""} maxLength={128} placeholder="order.confirmed" onChange={(event) => updateConfig({ ...node.config, signalName: event.target.value })} />
          </label>
          <label className="field">
            <span>Ключ сопоставления</span>
            <input value={node.config.signalCorrelationKey ?? ""} maxLength={1_000} placeholder="{{ json.orderId }}" onChange={(event) => updateConfig({ ...node.config, signalCorrelationKey: event.target.value })} />
          </label>
          <label className="field">
            <span>Тайм-аут, сек. · 0 без ограничения</span>
            <input type="number" min={0} max={31_536_000} value={node.config.signalTimeoutSeconds ?? 0} onChange={(event) => updateConfig({ ...node.config, signalTimeoutSeconds: Number(event.target.value) })} />
          </label>
          <ExpressionHelp />
        </>
      ) : null}

      {node.type === "subprocess" ? (
        <>
          <label className="field">
            <span>Опубликованный процесс</span>
            <select value={node.config.subprocessProcessId ?? ""} onChange={(event) => updateConfig({
              ...node.config,
              subprocessProcessId: event.target.value,
              subprocessVersion: undefined,
            })}>
              <option value="" disabled>Выберите процесс</option>
              {processes.filter((process) => process.publishedVersion > 0 && process.id !== currentProcessId).map((process) => (
                <option value={process.id} key={process.id}>{process.name} · v{process.publishedVersion}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Вход subprocess</span>
            <textarea rows={6} value={node.config.subprocessInputTemplate ?? "{{ lastOutput }}"} maxLength={100_000} onChange={(event) => updateConfig({ ...node.config, subprocessInputTemplate: event.target.value })} />
          </label>
          {node.config.subprocessVersion ? <div className="process-loop-warning"><Icon name="shield" size={18} /><span>При публикации закреплена версия v{node.config.subprocessVersion}.</span></div> : null}
          <ExpressionHelp />
        </>
      ) : null}
    </div>
  );
}

function ExpressionHelp() {
  return (
    <div className="process-expression-help">
      <strong>Выражения</strong>
      <code>{"{{ input }}"}</code>
      <code>{"{{ lastOutput }}"}</code>
      <code>{"{{ json.field }}"}</code>
      <small>JavaScript не выполняется; доступны только безопасные пути.</small>
    </div>
  );
}

function HttpHeadersEditor({ node, onChange }: { node: ProcessGraphNode; onChange: (node: ProcessGraphNode) => void }) {
  const serialized = JSON.stringify(node.config.headers ?? {}, null, 2);
  const [value, setValue] = useState(serialized);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setValue(serialized), [node.id, serialized]);

  function commit() {
    try {
      const parsed = JSON.parse(value || "{}") as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      const headers = Object.fromEntries(Object.entries(parsed).map(([name, field]) => [name, String(field)]));
      onChange({ ...node, config: { ...node.config, headers } });
      setError(null);
    } catch {
      setError("Нужен JSON-объект заголовков");
    }
  }

  return (
    <label className={`field${error ? " field--error" : ""}`}>
      <span>Заголовки JSON</span>
      <textarea rows={5} value={value} maxLength={20_000} onChange={(event) => setValue(event.target.value)} onBlur={commit} />
      {error ? <small>{error}</small> : null}
    </label>
  );
}

function CompensationHeadersEditor({ node, onChange }: { node: ProcessGraphNode; onChange: (node: ProcessGraphNode) => void }) {
  const serialized = JSON.stringify(node.config.compensation?.headers ?? {}, null, 2);
  const [value, setValue] = useState(serialized);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setValue(serialized), [node.id, serialized]);

  function commit() {
    try {
      const parsed = JSON.parse(value || "{}") as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      const headers = Object.fromEntries(Object.entries(parsed).map(([name, field]) => [name, String(field)]));
      onChange({
        ...node,
        config: {
          ...node.config,
          compensation: node.config.compensation ? { ...node.config.compensation, headers } : undefined,
        },
      });
      setError(null);
    } catch {
      setError("Нужен JSON-объект заголовков");
    }
  }

  return (
    <label className={`field${error ? " field--error" : ""}`}>
      <span>Заголовки JSON</span>
      <textarea rows={4} value={value} maxLength={20_000} onChange={(event) => setValue(event.target.value)} onBlur={commit} />
      {error ? <small>{error}</small> : null}
    </label>
  );
}

export function ProcessInspector({
  node,
  agents,
  credentials,
  processes,
  currentProcessId,
  processNodes,
  execution,
  executionLoading,
  defaultInput,
  testResult,
  testBusy,
  canRunFrom,
  onChange,
  onDelete,
  onClose,
  onTest,
  onRunFrom,
  onOpenRun,
  onManageCredentials,
}: ProcessInspectorProps) {
  const [tab, setTab] = useState<InspectorTab>("parameters");
  const [testInput, setTestInput] = useState(defaultInput);

  useEffect(() => {
    setTab("parameters");
    setTestInput(defaultInput);
  }, [node?.id]);

  useEffect(() => {
    if (!testInput && defaultInput) setTestInput(defaultInput);
  }, [defaultInput, testInput]);

  if (!node) return null;

  const inspectorTabs: readonly TabDefinition<InspectorTab>[] = [
    { id: "parameters", label: "Настройка" },
    { id: "input", label: "Тест" },
    { id: "output", label: "Результат" },
    { id: "logs", label: `Журнал${execution?.events.length ? ` · ${execution.events.length}` : ""}` },
  ];

  return (
    <aside className={`process-inspector process-inspector--${node.type}`}>
      <header>
        <div>
          <h2>{node.name || "Без названия"}</h2>
          <p>{nodeTypeLabel(node)}</p>
        </div>
        <div className="process-inspector__head-actions">
          {execution ? (
            <span className={`process-inspector__status process-inspector__status--${execution.status}`}>
              <i />{statusLabels[execution.status]}
            </span>
          ) : null}
          <button className="icon-button process-inspector__close" type="button" aria-label="Закрыть настройки" onClick={onClose}>
            <Icon name="close" size={18} />
          </button>
        </div>
      </header>

      <AccessibleTabList activeTab={tab} ariaLabel="Данные шага" className="process-inspector__tabs" idPrefix="process-inspector" tabs={inspectorTabs} onChange={setTab} />

      <div className="process-inspector__body">
        <TabPanel active={tab === "parameters"} idPrefix="process-inspector" tabId="parameters">
          <ParametersTab
            node={node}
            agents={agents}
            credentials={credentials}
            processes={processes}
            currentProcessId={currentProcessId}
            processNodes={processNodes}
            onManageCredentials={onManageCredentials}
            onChange={onChange}
          />
        </TabPanel>
        <TabPanel active={tab === "input"} idPrefix="process-inspector" tabId="input">
          <div className="process-inspector__io">
            <label className="field">
              <span>Входные данные для проверки</span>
              <textarea
                rows={8}
                maxLength={100_000}
                value={testInput}
                placeholder="Введите данные для этого шага"
                onChange={(event) => setTestInput(event.target.value)}
              />
            </label>
            <section>
              <header><strong>Фактический вход</strong>{executionLoading ? <small>Загрузка…</small> : null}</header>
              <pre>{formatExecutionValue(execution?.input)}</pre>
            </section>
          </div>
        </TabPanel>
        <TabPanel active={tab === "output"} idPrefix="process-inspector" tabId="output">
          <div className="process-inspector__io">
            {testResult ? (
              <section className="process-inspector__test-result">
                <header><strong>Результат теста</strong><small>{testResult.branch && testResult.branch !== "default" ? `ветка: ${testResult.branch === "true" ? "да" : testResult.branch === "false" ? "нет" : testResult.branch === "repeat" ? "повтор" : "выход"}` : "Готово"}</small></header>
                <pre>{testResult.output || "Тестовый запуск добавлен в очередь"}</pre>
                {testResult.runId ? <button type="button" onClick={() => onOpenRun(testResult.runId!)}>Открыть тестовый запуск <Icon name="chevron" size={14} /></button> : null}
              </section>
            ) : null}
            <section>
              <header><strong>Фактический выход</strong>{execution?.attempt ? <small>попытка {execution.attempt}</small> : null}</header>
              <pre>{formatExecutionValue(execution?.output)}</pre>
            </section>
          </div>
        </TabPanel>
        <TabPanel active={tab === "logs"} idPrefix="process-inspector" tabId="logs">
          <div className="process-inspector__logs">
            {executionLoading ? <p>Загружаем журнал шага…</p> : null}
            {!executionLoading && !execution?.events.length ? <p>Для этого шага пока нет событий.</p> : null}
            {execution?.events.map((event) => (
              <details key={event.id}>
                <summary><time>{new Date(event.createdAt).toLocaleTimeString("ru-RU")}</time><span>{event.message}</span><Icon name="chevron" size={13} /></summary>
                <pre>{JSON.stringify({ type: event.type, level: event.level, data: event.data }, null, 2)}</pre>
              </details>
            ))}
          </div>
        </TabPanel>
      </div>

      <footer className="process-inspector__footer">
        <button className="process-delete-step" type="button" onClick={() => onDelete(node.id)} title="Удалить шаг">
          <Icon name="trash" size={16} />
        </button>
        <div>
          <button className="button button--secondary" type="button" disabled={testBusy} onClick={() => { if (!testInput.trim()) setTab("input"); else { onTest(node, testInput); setTab("output"); } }}>
            <Icon name="play" size={15} />{testBusy ? "Тестируем…" : "Тестировать шаг"}
          </button>
          <button
            className="button button--secondary"
            type="button"
            disabled={testBusy || !canRunFrom || !testInput.trim()}
            title={canRunFrom ? "Запустить опубликованный процесс с этого шага" : "Сначала сохраните и опубликуйте процесс"}
            onClick={() => onRunFrom(node, testInput)}
          >
            <Icon name="runs" size={15} />Запустить отсюда
          </button>
        </div>
      </footer>
    </aside>
  );
}
