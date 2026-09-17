import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ApiError, api } from "../lib/api";
import type {
  ProcessDefinition,
  ProcessSchedule,
  ProcessVersionDiff,
  ProcessWebhook,
  ProcessWebhookSecret,
  SaveProcessScheduleRequest,
} from "../types";
import { AccessibleTabList, TabPanel, type TabDefinition } from "./AccessibleTabs";
import { useActionDialog } from "./ActionDialog";
import { Icon } from "./Icon";

type ReleaseTab = "triggers" | "versions" | "bpmn";

const releaseTabs: readonly TabDefinition<ReleaseTab>[] = [
  { id: "triggers", label: "Триггеры" },
  { id: "versions", label: "Версии" },
  { id: "bpmn", label: "BPMN 2.0" },
];

interface ProcessReleasePanelProps {
  embedded?: boolean;
  initialTab?: ReleaseTab;
  open: boolean;
  process: ProcessDefinition | null;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}

const defaultSchedule: SaveProcessScheduleRequest = {
  input: "",
  kind: "interval",
  everySeconds: 3_600,
  cronExpression: "0 8 * * MON-FRI",
  calendar: { minute: 0, hour: 8 },
  timezone: "UTC",
  priority: 50,
  paused: false,
  knowledgeCollectionIds: [],
};

function scheduleRequest(schedule: ProcessSchedule | null): SaveProcessScheduleRequest {
  if (!schedule) return structuredClone(defaultSchedule);
  return {
    input: schedule.input,
    kind: schedule.kind,
    everySeconds: schedule.everySeconds || 3_600,
    cronExpression: schedule.cronExpression || "0 8 * * MON-FRI",
    calendar: schedule.calendar ?? { minute: 0, hour: 8 },
    timezone: schedule.timezone || "UTC",
    priority: schedule.priority,
    paused: schedule.paused,
    knowledgeCollectionIds: schedule.knowledgeCollectionIds,
  };
}

export function ProcessReleasePanel({ open, process, onClose, onChanged, embedded = false, initialTab = "triggers" }: ProcessReleasePanelProps) {
  const requestAction = useActionDialog();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<ReleaseTab>("triggers");
  const [schedule, setSchedule] = useState<ProcessSchedule | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<SaveProcessScheduleRequest>(defaultSchedule);
  const [calendarText, setCalendarText] = useState(JSON.stringify(defaultSchedule.calendar, null, 2));
  const [webhooks, setWebhooks] = useState<ProcessWebhook[]>([]);
  const [webhookKind, setWebhookKind] = useState("start");
  const [secret, setSecret] = useState<ProcessWebhookSecret | null>(null);
  const [diff, setDiff] = useState<ProcessVersionDiff | null>(null);
  const [diffFrom, setDiffFrom] = useState<number | "draft">("draft");
  const [diffTo, setDiffTo] = useState<number | "draft">("draft");
  const [bpmnVersion, setBpmnVersion] = useState<number | "draft">("draft");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduleUnavailable, setScheduleUnavailable] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { if (open) setTab(initialTab); }, [initialTab, open]);

  const signalNames = useMemo(() => [...new Set((process?.draftGraph.nodes ?? [])
    .filter((node) => node.type === "signal" && node.config.signalName)
    .map((node) => node.config.signalName!))], [process?.draftGraph]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (!open || !process) return;
    const latest = process.versions[0]?.version;
    setDiffFrom(latest ?? "draft");
    setDiffTo("draft");
    setBpmnVersion("draft");
    if (initialTab !== "triggers") return;
    let active = true;
    setLoading(true);
    setError(null);
    setSecret(null);
    setScheduleUnavailable(null);
    const schedulePromise = api.processSchedule(process.id).catch((requestError: unknown) => {
      if (requestError instanceof ApiError && requestError.status === 404) return null;
      if (requestError instanceof ApiError && requestError.status === 503) {
        if (active) setScheduleUnavailable("Служба расписаний не подключена. Администратор может включить её в настройках системы.");
        return null;
      }
      throw requestError;
    });
    void Promise.all([api.processWebhooks(process.id), schedulePromise])
      .then(([webhookResult, loadedSchedule]) => {
        if (!active) return;
        setWebhooks(webhookResult.webhooks);
        setSchedule(loadedSchedule);
        const next = scheduleRequest(loadedSchedule);
        setScheduleDraft(next);
        setCalendarText(JSON.stringify(next.calendar ?? {}, null, 2));
        const latest = process.versions[0]?.version;
        setDiffFrom(latest ?? "draft");
        setDiffTo("draft");
        setBpmnVersion("draft");
      })
      .catch((requestError: unknown) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить настройки релиза");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [initialTab, open, process?.id]);

  useEffect(() => {
    if (!open || !process || tab !== "versions") return;
    let active = true;
    setError(null);
    void api.diffProcessVersions(process.id, diffFrom, diffTo)
      .then((result) => { if (active) setDiff(result); })
      .catch((requestError: unknown) => {
        if (active) {
          setDiff(null);
          setError(requestError instanceof Error ? requestError.message : "Не удалось сравнить версии");
        }
      });
    return () => { active = false; };
  }, [diffFrom, diffTo, open, process?.id, tab]);

  async function saveSchedule(event: FormEvent) {
    event.preventDefault();
    if (!process) return;
    setBusy(true);
    setError(null);
    try {
      const calendar = scheduleDraft.kind === "calendar"
        ? JSON.parse(calendarText) as Record<string, unknown>
        : scheduleDraft.calendar;
      const saved = await api.saveProcessSchedule(process.id, { ...scheduleDraft, calendar });
      setSchedule(saved);
      setScheduleDraft(scheduleRequest(saved));
      setScheduleUnavailable(null);
      setNotice(saved.paused ? "Расписание сохранено на паузе." : "Расписание сохранено. Автоматический запуск включён.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить расписание");
    } finally {
      setBusy(false);
    }
  }

  async function removeSchedule() {
    if (!process || !schedule) return;
    const decision = await requestAction({
      title: "Удалить расписание?",
      description: "Процесс больше не будет запускаться автоматически по текущему правилу.",
      subject: process.name,
      subjectLabel: "Процесс",
      impact: "Будущие срабатывания расписания отменятся. Уже созданные и выполняющиеся запуски продолжат работу.",
      recovery: "Расписание можно создать заново, но его параметры потребуется настроить повторно.",
      confirmLabel: "Удалить расписание",
      tone: "danger",
    });
    if (!decision.confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProcessSchedule(process.id);
      setSchedule(null);
      setScheduleDraft(structuredClone(defaultSchedule));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось удалить расписание");
    } finally {
      setBusy(false);
    }
  }

  async function triggerSchedule() {
    if (!process) return;
    setBusy(true);
    setError(null);
    try {
      setSchedule(await api.triggerProcessSchedule(process.id));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось запустить расписание");
    } finally {
      setBusy(false);
    }
  }

  async function createWebhook(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!process) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const kind = data.get("kind") === "signal" ? "signal" : "start";
    setBusy(true);
    setError(null);
    try {
      const created = await api.createProcessWebhook(process.id, {
        name: String(data.get("name") ?? ""),
        kind,
        signalName: kind === "signal" ? String(data.get("signalName") ?? "") : undefined,
        defaultInput: String(data.get("defaultInput") ?? ""),
      });
      setSecret(created);
      setWebhooks((current) => [created, ...current]);
      form.reset();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось создать webhook");
    } finally {
      setBusy(false);
    }
  }

  async function rotateWebhook(webhook: ProcessWebhook) {
    const decision = await requestAction({
      title: "Ротировать token webhook?",
      description: "Все отправители должны перейти на новый token без задержки.",
      subject: webhook.name,
      subjectLabel: "Webhook",
      impact: "Старый token перестанет работать сразу после ротации. Новый секрет будет показан только один раз.",
      recovery: "Старый token вернуть нельзя. Скопируйте новый и обновите каждый вызывающий сервис.",
      confirmLabel: "Ротировать token",
      tone: "warning",
    });
    if (!decision.confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const rotated = await api.rotateProcessWebhook(webhook.id);
      setSecret(rotated);
      setWebhooks((current) => current.map((item) => item.id === rotated.id ? rotated : item));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось ротировать webhook");
    } finally {
      setBusy(false);
    }
  }

  async function removeWebhook(webhook: ProcessWebhook) {
    const decision = await requestAction({
      title: "Удалить webhook?",
      description: "Входящие вызовы на этот endpoint больше не будут запускать процесс или отправлять signal.",
      subject: webhook.name,
      subjectLabel: "Webhook",
      impact: "Endpoint и его token будут удалены. История уже созданных запусков останется в АГАТ.",
      recovery: "Создайте новый webhook и обновите URL и token во всех вызывающих системах.",
      confirmLabel: "Удалить webhook",
      tone: "danger",
    });
    if (!decision.confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProcessWebhook(webhook.id);
      setWebhooks((current) => current.filter((item) => item.id !== webhook.id));
      if (secret?.id === webhook.id) setSecret(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось удалить webhook");
    } finally {
      setBusy(false);
    }
  }

  async function exportBpmn(version: number | "draft") {
    if (!process) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await api.exportProcessBpmn(process.id, version);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `agat-${process.name.replace(/[^\p{L}\p{N}_-]+/gu, "-")}-${version}.bpmn`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось экспортировать BPMN");
    } finally {
      setBusy(false);
    }
  }

  async function importBpmn(file: File) {
    setBusy(true);
    setError(null);
    try {
      await api.importProcessBpmn(await file.text());
      await onChanged();
      onClose();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось импортировать BPMN");
    } finally {
      setBusy(false);
    }
  }

  if (!process) return null;

  const versionOptions: Array<number | "draft"> = ["draft", ...process.versions.map((item) => item.version)];
  const content = (
      <section>
        <header className="process-release-dialog__head">
          <div><h2 id={embedded && initialTab === "triggers" ? "process-release-triggers-tab-triggers" : `process-release-${initialTab}-title`}>{initialTab === "triggers" ? "Триггеры запуска" : "Версии и экспорт"}</h2><p>{initialTab === "triggers" ? "Определите, когда процесс должен запускаться: по расписанию или по входящему событию." : "Сравните изменения и перенесите схему в формате BPMN."}</p></div>
          {!embedded ? <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button> : null}
        </header>
        {(!embedded || initialTab !== "triggers") ? <AccessibleTabList activeTab={tab} ariaLabel="Настройки выпуска процесса" className="process-release-dialog__tabs" idPrefix={`process-release-${initialTab}`} tabs={embedded ? releaseTabs.filter((item) => item.id !== "triggers") : releaseTabs} onChange={setTab} /> : null}
        {error ? <p className="process-release-dialog__error" role="alert">{error}</p> : null}
        {notice ? <p className="process-release-note" role="status">{notice}</p> : null}
        <div className="process-release-dialog__body">
          {loading ? <p className="process-release-dialog__loading">Загружаем настройки…</p> : null}
          <TabPanel active={tab === "triggers"} idPrefix={`process-release-${initialTab}`} tabId="triggers">
          {!loading ? (
            <>
            {process.publishedVersion < 1 ? <p className="process-trigger-prerequisite"><Icon name="publish" size={18} />Сначала опубликуйте процесс на вкладке «Схема», чтобы подключить автоматический запуск.</p> : null}
            <div className="process-release-grid">
              <form className="process-release-card" onSubmit={(event) => void saveSchedule(event)}>
                <header><div className="trigger-card-heading"><span className="trigger-type-icon"><Icon name="clock" size={22} /></span><div><h3>По расписанию</h3><p>Запускайте процесс с нужной периодичностью.</p></div></div><span className={`trigger-state${schedule && !schedule.paused ? " is-active" : ""}`}>{schedule ? schedule.paused ? "На паузе" : "Включено" : "Не настроено"}</span></header>
                {scheduleUnavailable ? <p className="process-release-note">{scheduleUnavailable}</p> : null}
                <label className="field"><span>Вход процесса</span><textarea required rows={4} maxLength={100_000} value={scheduleDraft.input} onChange={(event) => setScheduleDraft((current) => ({ ...current, input: event.target.value }))} /></label>
                <div className="process-field-grid">
                  <label className="field"><span>Периодичность</span><select value={scheduleDraft.kind} onChange={(event) => setScheduleDraft((current) => ({ ...current, kind: event.target.value as SaveProcessScheduleRequest["kind"] }))}><option value="interval">Через интервал</option><option value="cron">По правилу cron</option><option value="calendar">По календарю</option></select></label>
                  <label className="field"><span>Часовой пояс</span><input required list="process-timezones" value={scheduleDraft.timezone ?? "UTC"} onChange={(event) => setScheduleDraft((current) => ({ ...current, timezone: event.target.value }))} /><datalist id="process-timezones"><option value="Europe/Moscow" /><option value="UTC" /><option value="Asia/Yekaterinburg" /><option value="Asia/Novosibirsk" /></datalist></label>
                </div>
                {scheduleDraft.kind === "interval" ? <label className="field"><span>Каждые, секунд</span><input type="number" min={60} max={31_536_000} value={scheduleDraft.everySeconds ?? 3_600} onChange={(event) => setScheduleDraft((current) => ({ ...current, everySeconds: Number(event.target.value) }))} /></label> : null}
                {scheduleDraft.kind === "cron" ? <label className="field"><span>Правило cron</span><input required value={scheduleDraft.cronExpression ?? ""} placeholder="0 8 * * MON-FRI" onChange={(event) => setScheduleDraft((current) => ({ ...current, cronExpression: event.target.value }))} /><small>Например, 0 8 * * MON-FRI — по будням в 08:00 в выбранном часовом поясе.</small></label> : null}
                {scheduleDraft.kind === "calendar" ? <label className="field"><span>Правило календаря (JSON)</span><textarea rows={6} value={calendarText} onChange={(event) => setCalendarText(event.target.value)} /></label> : null}
                <label className="approval-toggle"><input type="checkbox" checked={scheduleDraft.paused} onChange={(event) => setScheduleDraft((current) => ({ ...current, paused: event.target.checked }))} /><span><strong>Сохранить на паузе</strong><small>Автоматические запуски начнутся после снятия паузы.</small></span></label>
                <p className="process-release-note">Если предыдущее выполнение ещё идёт, очередное срабатывание будет пропущено.</p>
                {schedule?.nextActionTimes[0] ? <p className="process-release-note">Следующий запуск: {new Date(schedule.nextActionTimes[0]).toLocaleString("ru-RU")}</p> : null}
                <footer><button className="button button--secondary" type="button" disabled={busy || !schedule} onClick={() => void removeSchedule()}>Удалить</button><button className="button button--secondary" type="button" disabled={busy || !schedule} onClick={() => void triggerSchedule()}>Запустить сейчас</button><button className="button button--primary" disabled={busy || !!scheduleUnavailable || process.publishedVersion < 1} type="submit">Сохранить расписание</button></footer>
              </form>

              <div className="process-release-card">
                <header><div className="trigger-card-heading"><span className="trigger-type-icon trigger-type-icon--event"><Icon name="network" size={22} /></span><div><h3>По входящему событию</h3><p>Принимайте обращения из внешней системы через webhook.</p></div></div><span className="trigger-state">{webhooks.length} подключено</span></header>
                {secret ? <div className="process-webhook-secret"><strong>Сохраните токен доступа — он показан один раз</strong><code>{secret.token}</code><small>POST /api/v1/process-webhooks/{secret.id}</small></div> : null}
                <form className="process-webhook-form" onSubmit={(event) => void createWebhook(event)}>
                  <label className="field"><span>Название</span><input name="name" required maxLength={100} /></label>
                  <div className="process-field-grid"><label className="field"><span>Действие</span><select name="kind" value={webhookKind} onChange={(event) => setWebhookKind(event.target.value)}><option value="start">Запустить процесс</option><option value="signal" disabled={!signalNames.length}>Продолжить по событию</option></select></label>{webhookKind === "signal" ? <label className="field"><span>Ожидаемое событие</span><select name="signalName" defaultValue={signalNames[0] ?? ""}><option value="">Для продолжения процесса</option>{signalNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></label> : null}</div>
                  <label className="field"><span>Входные данные по умолчанию</span><textarea name="defaultInput" rows={3} maxLength={100_000} /></label>
                  <button className="button button--secondary" type="submit" disabled={busy || process.publishedVersion < 1}><Icon name="plus" size={15} />Создать webhook</button>
                </form>
                <details className="process-trigger-help"><summary>Как подключить внешнюю систему</summary><p>После создания сохраните выданный токен. Отправляйте POST-запрос на адрес webhook с заголовком Authorization: Bearer. Для запуска передавайте уникальный Idempotency-Key, чтобы повторная доставка события не создала дубликат.</p></details>
                <div className="process-webhook-list">{webhooks.map((webhook) => <article key={webhook.id}><span><strong>{webhook.name}</strong><small>{webhook.kind === "start" ? "Запуск процесса" : "Продолжение по событию"}{webhook.signalName ? ` · ${webhook.signalName}` : ""}</small></span><button type="button" disabled={busy} onClick={() => void rotateWebhook(webhook)} title="Обновить токен"><Icon name="repeat" size={14} /></button><button type="button" disabled={busy} onClick={() => void removeWebhook(webhook)} title="Удалить"><Icon name="trash" size={14} /></button></article>)}</div>
              </div>
            </div>
            </>
          ) : null}
          </TabPanel>

          <TabPanel active={tab === "versions"} idPrefix={`process-release-${initialTab}`} tabId="versions">
          {!loading ? (
            <div className="process-release-card process-release-card--wide">
              <header><div><h3>Изменения между версиями</h3><p>Сравните шаги, настройки и связи. Опубликованные версии сохраняются неизменными.</p></div></header>
              <div className="process-version-selectors"><label className="field"><span>Откуда</span><select value={diffFrom} onChange={(event) => setDiffFrom(event.target.value === "draft" ? "draft" : Number(event.target.value))}>{versionOptions.map((version) => <option value={version} key={version}>{version === "draft" ? "Черновик" : `v${version}`}</option>)}</select></label><Icon name="chevron" /><label className="field"><span>Куда</span><select value={diffTo} onChange={(event) => setDiffTo(event.target.value === "draft" ? "draft" : Number(event.target.value))}>{versionOptions.map((version) => <option value={version} key={version}>{version === "draft" ? "Черновик" : `v${version}`}</option>)}</select></label></div>
              {diff ? <><div className="process-diff-summary"><span>+{diff.summary.addedNodes} шагов</span><span>−{diff.summary.removedNodes} шагов</span><span>~{diff.summary.changedNodes} шагов</span><span>+{diff.summary.addedEdges}/−{diff.summary.removedEdges} связей</span></div><div className="process-diff-list">{diff.entries.length ? diff.entries.map((entry) => <details key={`${entry.kind}:${entry.id}`}><summary><strong>{entry.kind.replaceAll("_", " ")}</strong><code>{entry.id}</code></summary><pre>{JSON.stringify({ before: entry.before, after: entry.after }, null, 2)}</pre></details>) : <p>Структурных изменений нет.</p>}</div></> : null}
            </div>
          ) : null}
          </TabPanel>

          <TabPanel active={tab === "bpmn"} idPrefix={`process-release-${initialTab}`} tabId="bpmn">
          {!loading ? (
            <div className="process-release-grid">
              <div className="process-release-card"><header><div><h3>Экспорт BPMN 2.0</h3><p>Скачайте схему с настройками шагов и расположением элементов.</p></div></header><label className="field"><span>Версия</span><select value={bpmnVersion} onChange={(event) => setBpmnVersion(event.target.value === "draft" ? "draft" : Number(event.target.value))}>{versionOptions.map((version) => <option value={version} key={version}>{version === "draft" ? "Черновик" : `v${version}`}</option>)}</select></label><button className="button button--primary" type="button" disabled={busy} onClick={() => void exportBpmn(bpmnVersion)}><Icon name="publish" size={15} />Скачать .bpmn</button></div>
              <div className="process-release-card"><header><div><h3>Импорт BPMN 2.0</h3><p>Создайте новый черновик из файла. Перед публикацией проверьте импортированные шаги и связи.</p></div></header><label className="process-bpmn-drop"><Icon name="workflow" size={26} /><strong>Выберите .bpmn или .xml</strong><small>До 1 МБ</small><input type="file" accept=".bpmn,.xml,application/xml,text/xml" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importBpmn(file); }} /></label></div>
            </div>
          ) : null}
          </TabPanel>
        </div>
      </section>
  );
  return embedded ? <div className="process-release-embedded">{content}</div> : <dialog className="process-release-dialog" ref={dialogRef} aria-labelledby={`process-release-${initialTab}-title`} onCancel={onClose} onClose={onClose}>{content}</dialog>;
}
