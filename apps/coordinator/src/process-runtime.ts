import fs from "node:fs";

import {
  Client,
  Connection,
  type CalendarSpec,
  type ConnectionOptions,
  ScheduleAlreadyRunning,
  ScheduleNotFoundError,
  ScheduleOverlapPolicy,
  WorkflowExecutionAlreadyStartedError,
  WorkflowIdConflictPolicy,
  WorkflowIdReusePolicy,
} from "@temporalio/client";

import type { CoordinatorConfig } from "./config.js";

export interface DurableProcessStart {
  instanceId: string;
  processId: string;
  projectId: string;
  priority: number;
}

export interface ProcessRuntimeSnapshot {
  mode: "database" | "temporal";
  connected: boolean;
  namespace: string | null;
  taskQueue: string | null;
  target: "local" | "cloud" | "self-hosted" | null;
  tls: boolean;
}

export interface ProcessScheduleInput {
  processId: string;
  projectId: string;
  input: string;
  kind: "interval" | "cron" | "calendar";
  everySeconds: number;
  cronExpression: string;
  calendar: CalendarSpec | null;
  timezone: string;
  priority: number;
  paused: boolean;
  knowledgeCollectionIds: string[];
}

export interface ProcessScheduleSnapshot {
  scheduleId: string;
  processId: string;
  projectId: string;
  input: string;
  kind: "interval" | "cron" | "calendar";
  everySeconds: number;
  cronExpression: string;
  calendar: CalendarSpec | null;
  timezone: string;
  priority: number;
  paused: boolean;
  knowledgeCollectionIds: string[];
  nextActionTimes: string[];
  recentWorkflowIds: string[];
}

export interface ProcessRuntime {
  startProcess(input: DurableProcessStart): Promise<void>;
  notifyProcess(instanceId: string, reason: string): Promise<void>;
  getProcessSchedule(processId: string, projectId: string): Promise<ProcessScheduleSnapshot | null>;
  upsertProcessSchedule(input: ProcessScheduleInput): Promise<ProcessScheduleSnapshot>;
  deleteProcessSchedule(processId: string, projectId: string): Promise<boolean>;
  triggerProcessSchedule(processId: string, projectId: string): Promise<ProcessScheduleSnapshot>;
  close(): Promise<void>;
  snapshot(): ProcessRuntimeSnapshot;
}

class DatabaseProcessRuntime implements ProcessRuntime {
  async startProcess(): Promise<void> {}
  async notifyProcess(): Promise<void> {}
  async getProcessSchedule(): Promise<null> { return null; }
  async upsertProcessSchedule(): Promise<ProcessScheduleSnapshot> {
    throw new Error("Расписания процессов требуют включённого Temporal runtime");
  }
  async deleteProcessSchedule(): Promise<boolean> { return false; }
  async triggerProcessSchedule(): Promise<ProcessScheduleSnapshot> {
    throw new Error("Расписания процессов требуют включённого Temporal runtime");
  }
  async close(): Promise<void> {}

  snapshot(): ProcessRuntimeSnapshot {
    return { mode: "database", connected: true, namespace: null, taskQueue: null, target: null, tls: false };
  }
}

class TemporalProcessRuntime implements ProcessRuntime {
  constructor(
    private readonly connection: Connection,
    private readonly client: Client,
    private readonly namespace: string,
    private readonly taskQueue: string,
    private readonly target: "local" | "cloud" | "self-hosted",
    private readonly tls: boolean,
  ) {}

  async startProcess(input: DurableProcessStart): Promise<void> {
    try {
      await this.client.workflow.start("agatProcessWorkflow", {
        workflowId: workflowId(input.instanceId),
        workflowIdReusePolicy: WorkflowIdReusePolicy.REJECT_DUPLICATE,
        workflowIdConflictPolicy: WorkflowIdConflictPolicy.USE_EXISTING,
        taskQueue: this.taskQueue,
        args: [{
          instanceId: input.instanceId,
          processId: input.processId,
          projectId: input.projectId,
        }],
        memo: {
          agatInstanceId: input.instanceId,
          agatProcessId: input.processId,
          agatProjectId: input.projectId,
        },
        staticSummary: `АГАТ · процесс ${input.processId}`,
        priority: {
          priorityKey: temporalPriority(input.priority),
        },
      });
    } catch (error) {
      if (error instanceof WorkflowExecutionAlreadyStartedError) return;
      throw error;
    }
  }

  async notifyProcess(instanceId: string, reason: string): Promise<void> {
    const normalizedReason = reason.trim().slice(0, 120) || "state-changed";
    const handle = this.client.workflow.getHandle(workflowId(instanceId));
    try {
      await handle.executeUpdate("processChangedV1", { args: [normalizedReason] });
    } catch {
      // Workflows pinned to a pre-1.0 worker do not expose the acknowledged Update handler.
      await handle.signal("processChanged", normalizedReason);
    }
  }

  async getProcessSchedule(processId: string, projectId: string): Promise<ProcessScheduleSnapshot | null> {
    const scheduleId = processScheduleId(processId);
    try {
      const description = await this.client.schedule.getHandle(scheduleId).describe();
      if (!processScheduleBelongsTo(description, processId, projectId)) return null;
      return scheduleSnapshot(description, processId, projectId);
    } catch (error) {
      if (error instanceof ScheduleNotFoundError) return null;
      throw error;
    }
  }

  async upsertProcessSchedule(input: ProcessScheduleInput): Promise<ProcessScheduleSnapshot> {
    const scheduleId = processScheduleId(input.processId);
    const definition = processScheduleDefinition(input, this.taskQueue);
    const handle = this.client.schedule.getHandle(scheduleId);
    try {
      await this.client.schedule.create({
        scheduleId,
        ...definition,
        memo: {
          agatProcessId: input.processId,
          agatProjectId: input.projectId,
          agatManaged: true,
        },
      });
    } catch (error) {
      if (!(error instanceof ScheduleAlreadyRunning)) throw error;
      const existing = await handle.describe();
      if (!processScheduleBelongsTo(existing, input.processId, input.projectId)) {
        throw new Error(`Temporal Schedule ${scheduleId} уже занят другим владельцем`);
      }
      await handle.update(() => ({
        ...definition,
        state: {
          paused: input.paused,
          note: input.paused ? "Приостановлено оператором АГАТ" : "Управляется АГАТ",
        },
      }));
    }
    const description = await handle.describe();
    return scheduleSnapshot(description, input.processId, input.projectId);
  }

  async deleteProcessSchedule(processId: string, projectId: string): Promise<boolean> {
    const handle = this.client.schedule.getHandle(processScheduleId(processId));
    try {
      const existing = await handle.describe();
      if (!processScheduleBelongsTo(existing, processId, projectId)) return false;
      const snapshot = scheduleSnapshot(existing, processId, projectId);
      if (snapshot.projectId !== projectId || snapshot.processId !== processId) return false;
      await handle.delete();
      return true;
    } catch (error) {
      if (error instanceof ScheduleNotFoundError) return false;
      throw error;
    }
  }

  async triggerProcessSchedule(processId: string, projectId: string): Promise<ProcessScheduleSnapshot> {
    const handle = this.client.schedule.getHandle(processScheduleId(processId));
    try {
      const before = await handle.describe();
      if (!processScheduleBelongsTo(before, processId, projectId)) {
        throw new Error("Расписание не принадлежит активному проекту");
      }
      const snapshot = scheduleSnapshot(before, processId, projectId);
      if (snapshot.projectId !== projectId || snapshot.processId !== processId) {
        throw new Error("Расписание не принадлежит активному проекту");
      }
      await handle.trigger(ScheduleOverlapPolicy.SKIP);
      return scheduleSnapshot(await handle.describe(), processId, projectId);
    } catch (error) {
      if (error instanceof ScheduleNotFoundError) throw new Error("Расписание процесса не найдено");
      throw error;
    }
  }

  async close(): Promise<void> {
    await this.connection.close();
  }

  snapshot(): ProcessRuntimeSnapshot {
    return {
      mode: "temporal",
      connected: true,
      namespace: this.namespace,
      taskQueue: this.taskQueue,
      target: this.target,
      tls: this.tls,
    };
  }
}

function workflowId(instanceId: string): string {
  return `agat-process-${instanceId}`;
}

export function processScheduleId(processId: string): string {
  const normalized = processId.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(normalized)) {
    throw new Error("Некорректный ID процесса для Temporal Schedule");
  }
  return `agat-process-schedule-${normalized}`;
}

export function normalizeProcessScheduleInput(
  raw: unknown,
  processId: string,
  projectId: string,
): ProcessScheduleInput {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Параметры расписания должны быть объектом");
  const input = raw as Record<string, unknown>;
  const processInput = typeof input.input === "string" ? input.input.trim() : "";
  if (!processInput || processInput.length > 100_000) {
    throw new Error("Вход расписания должен содержать от 1 до 100000 символов");
  }
  const kind = input.kind === undefined ? "interval" : input.kind;
  if (kind !== "interval" && kind !== "cron" && kind !== "calendar") {
    throw new Error("kind должен быть interval, cron или calendar");
  }
  const everySeconds = kind === "interval" && typeof input.everySeconds === "number" ? input.everySeconds : 0;
  if (kind === "interval" && (
    typeof everySeconds !== "number"
    || !Number.isInteger(everySeconds)
    || everySeconds < 60
    || everySeconds > 31_536_000
  )) {
    throw new Error("everySeconds должен быть целым числом от 60 до 31536000");
  }
  const cronExpression = kind === "cron" ? normalizeCronExpression(input.cronExpression) : "";
  const calendar = kind === "calendar" ? normalizeCalendarSpec(input.calendar) : null;
  const timezone = normalizeScheduleTimezone(input.timezone);
  const priority = input.priority ?? 50;
  if (typeof priority !== "number" || !Number.isInteger(priority) || priority < 0 || priority > 100) {
    throw new Error("priority должен быть целым числом от 0 до 100");
  }
  const rawCollectionIds = input.knowledgeCollectionIds ?? [];
  if (!Array.isArray(rawCollectionIds) || rawCollectionIds.length > 50) {
    throw new Error("knowledgeCollectionIds должен быть массивом максимум из 50 элементов");
  }
  const knowledgeCollectionIds = [...new Set(rawCollectionIds.map((value) => {
    if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) {
      throw new Error("knowledgeCollectionIds содержит некорректный ID");
    }
    return value;
  }))];
  if (input.paused !== undefined && typeof input.paused !== "boolean") {
    throw new Error("paused должен быть boolean");
  }
  processScheduleId(processId);
  return {
    processId,
    projectId,
    input: processInput,
    kind,
    everySeconds,
    cronExpression,
    calendar,
    timezone,
    priority,
    paused: input.paused === true,
    knowledgeCollectionIds,
  };
}

export function processScheduleDefinition(input: ProcessScheduleInput, taskQueue: string) {
  const spec = input.kind === "interval"
    ? { intervals: [{ every: `${input.everySeconds} seconds` }] }
    : input.kind === "cron"
      ? { cronExpressions: [input.cronExpression], timezone: input.timezone }
      : { calendars: [input.calendar!], timezone: input.timezone };
  return {
    spec,
    action: {
      type: "startWorkflow" as const,
      workflowType: "agatScheduledProcessWorkflow",
      taskQueue,
      args: [{
        processId: input.processId,
        projectId: input.projectId,
        input: input.input,
        kind: input.kind,
        everySeconds: input.everySeconds,
        cronExpression: input.cronExpression,
        calendar: input.calendar,
        timezone: input.timezone,
        priority: input.priority,
        paused: input.paused,
        knowledgeCollectionIds: input.knowledgeCollectionIds,
      }],
      staticSummary: `АГАТ · расписание процесса ${input.processId}`,
    },
    policies: {
      overlap: ScheduleOverlapPolicy.SKIP,
      catchupWindow: "5 minutes",
      pauseOnFailure: true,
    },
    state: {
      paused: input.paused,
      note: input.paused ? "Приостановлено оператором АГАТ" : "Управляется АГАТ",
    },
  };
}

type ScheduleDescription = Awaited<
  ReturnType<ReturnType<Client["schedule"]["getHandle"]>["describe"]>
>;

function processScheduleBelongsTo(
  description: ScheduleDescription,
  processId: string,
  projectId: string,
): boolean {
  if (description.action.type !== "startWorkflow") return false;
  const input = description.action.args?.[0];
  if (!input || typeof input !== "object" || Array.isArray(input)) return false;
  const identity = input as Partial<ProcessScheduleInput>;
  return identity.processId === processId && identity.projectId === projectId;
}

function scheduleSnapshot(
  description: ScheduleDescription,
  expectedProcessId: string,
  expectedProjectId: string,
): ProcessScheduleSnapshot {
  const actionInput = description.action.type === "startWorkflow"
    ? description.action.args?.[0] as Partial<ProcessScheduleInput> | undefined
    : undefined;
  const processId = typeof actionInput?.processId === "string" ? actionInput.processId : expectedProcessId;
  const projectId = typeof actionInput?.projectId === "string" ? actionInput.projectId : expectedProjectId;
  const interval = description.spec.intervals?.[0]?.every;
  const kind = actionInput?.kind === "cron" || actionInput?.kind === "calendar"
    ? actionInput.kind
    : "interval";
  return {
    scheduleId: description.scheduleId,
    processId,
    projectId,
    input: typeof actionInput?.input === "string" ? actionInput.input : "",
    kind,
    everySeconds: typeof interval === "number" ? Math.round(interval / 1_000) : 0,
    cronExpression: kind === "cron" && typeof actionInput?.cronExpression === "string"
      ? actionInput.cronExpression
      : "",
    calendar: kind === "calendar" && actionInput?.calendar && typeof actionInput.calendar === "object"
      ? actionInput.calendar as CalendarSpec
      : null,
    timezone: typeof actionInput?.timezone === "string" ? actionInput.timezone : "UTC",
    priority: typeof actionInput?.priority === "number" ? actionInput.priority : 50,
    paused: description.state.paused,
    knowledgeCollectionIds: Array.isArray(actionInput?.knowledgeCollectionIds)
      ? actionInput.knowledgeCollectionIds.filter((value): value is string => typeof value === "string")
      : [],
    nextActionTimes: description.info.nextActionTimes.map((value) => value.toISOString()),
    recentWorkflowIds: description.info.recentActions
      .map((value) => value.action.type === "startWorkflow" ? value.action.workflow.workflowId : "")
      .filter(Boolean),
  };
}

function normalizeCronExpression(value: unknown): string {
  if (typeof value !== "string") throw new Error("cronExpression обязателен для cron-расписания");
  const expression = value.trim().replace(/\s+/g, " ");
  if (!expression || expression.length > 256 || /[\u0000-\u001f\u007f]/u.test(expression)) {
    throw new Error("cronExpression задан некорректно");
  }
  if (/^(?:CRON_TZ|TZ)=/iu.test(expression)) {
    throw new Error("Timezone задаётся отдельным полем timezone");
  }
  const shorthand = new Set(["@yearly", "@monthly", "@weekly", "@daily", "@hourly"]);
  const fields = expression.split(" ");
  if (!shorthand.has(expression.toLowerCase()) && (fields.length < 5 || fields.length > 7)) {
    throw new Error("cronExpression должен содержать 5–7 полей или стандартный @shortcut");
  }
  if (!shorthand.has(expression.toLowerCase()) && fields.some((field) => !/^[A-Za-z0-9*?,/#LWed-]+$/u.test(field))) {
    throw new Error("cronExpression содержит неподдерживаемые символы");
  }
  return expression;
}

function normalizeScheduleTimezone(value: unknown): string {
  if (value === undefined || value === null || value === "") return "UTC";
  if (typeof value !== "string" || value.length > 100 || !/^[A-Za-z0-9_+./-]+$/u.test(value)) {
    throw new Error("timezone должен быть корректным IANA timezone");
  }
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format(new Date(0));
  } catch {
    throw new Error("timezone должен быть корректным IANA timezone");
  }
  return value;
}

function normalizeCalendarSpec(value: unknown): CalendarSpec {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("calendar обязателен для calendar-расписания");
  }
  const source = value as Record<string, unknown>;
  const allowed = new Set(["second", "minute", "hour", "dayOfMonth", "month", "year", "dayOfWeek", "comment"]);
  if (Object.keys(source).some((key) => !allowed.has(key))) {
    throw new Error("calendar содержит неизвестное поле");
  }
  const result: Record<string, unknown> = {};
  const numeric: Array<[string, number, number]> = [
    ["second", 0, 59],
    ["minute", 0, 59],
    ["hour", 0, 23],
    ["dayOfMonth", 1, 31],
    ["year", 1970, 2199],
  ];
  for (const [field, min, max] of numeric) {
    if (source[field] !== undefined) result[field] = normalizeCalendarValue(source[field], min, max, field);
  }
  const months = new Set(["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]);
  const days = new Set(["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]);
  if (source.month !== undefined) result.month = normalizeCalendarNameValue(source.month, months, "month");
  if (source.dayOfWeek !== undefined) result.dayOfWeek = normalizeCalendarNameValue(source.dayOfWeek, days, "dayOfWeek");
  if (source.comment !== undefined) {
    if (typeof source.comment !== "string" || source.comment.length > 200) throw new Error("calendar.comment: максимум 200 символов");
    result.comment = source.comment;
  }
  if (Object.keys(result).length === 0) throw new Error("calendar должен содержать хотя бы одно поле");
  return result as CalendarSpec;
}

function normalizeCalendarValue(value: unknown, min: number, max: number, field: string): unknown {
  if (value === "*") return value;
  if (Array.isArray(value)) {
    if (value.length === 0 || value.length > 32) throw new Error(`calendar.${field}: массив должен содержать 1–32 значения`);
    return value.map((item) => normalizeCalendarValue(item, min, max, field));
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= min && value <= max) return value;
  if (value && typeof value === "object") {
    const range = value as Record<string, unknown>;
    if (Object.keys(range).some((key) => !["start", "end", "step"].includes(key))) {
      throw new Error(`calendar.${field}: диапазон содержит неизвестное поле`);
    }
    const start = normalizeCalendarValue(range.start, min, max, field);
    const end = range.end === undefined ? undefined : normalizeCalendarValue(range.end, min, max, field);
    const step = range.step;
    if (step !== undefined && (typeof step !== "number" || !Number.isInteger(step) || step < 1 || step > max - min + 1)) {
      throw new Error(`calendar.${field}.step задан некорректно`);
    }
    return { start, ...(end === undefined ? {} : { end }), ...(step === undefined ? {} : { step }) };
  }
  throw new Error(`calendar.${field} задан некорректно`);
}

function normalizeCalendarNameValue(value: unknown, allowed: Set<string>, field: string): unknown {
  if (value === "*") return value;
  if (Array.isArray(value)) {
    if (value.length === 0 || value.length > 32) throw new Error(`calendar.${field}: массив должен содержать 1–32 значения`);
    return value.map((item) => normalizeCalendarNameValue(item, allowed, field));
  }
  if (typeof value === "string" && allowed.has(value.toUpperCase())) return value.toUpperCase();
  if (value && typeof value === "object") {
    const range = value as Record<string, unknown>;
    if (Object.keys(range).some((key) => !["start", "end", "step"].includes(key))) {
      throw new Error(`calendar.${field}: диапазон содержит неизвестное поле`);
    }
    const start = normalizeCalendarNameValue(range.start, allowed, field);
    const end = range.end === undefined ? undefined : normalizeCalendarNameValue(range.end, allowed, field);
    const step = range.step;
    if (step !== undefined && (typeof step !== "number" || !Number.isInteger(step) || step < 1 || step > allowed.size)) {
      throw new Error(`calendar.${field}.step задан некорректно`);
    }
    return { start, ...(end === undefined ? {} : { end }), ...(step === undefined ? {} : { step }) };
  }
  throw new Error(`calendar.${field} задан некорректно`);
}

function temporalPriority(priority: number): number {
  const normalized = Math.min(100, Math.max(0, Math.trunc(priority)));
  return Math.min(5, Math.max(1, 5 - Math.floor(normalized / 25)));
}

export async function createProcessRuntime(config: CoordinatorConfig): Promise<ProcessRuntime> {
  if (!config.temporalEnabled) return new DatabaseProcessRuntime();
  const connection = await Connection.connect(temporalConnectionOptions(config));
  const client = new Client({ connection, namespace: config.temporalNamespace });
  return new TemporalProcessRuntime(
    connection,
    client,
    config.temporalNamespace,
    config.temporalTaskQueue,
    config.temporalTarget,
    config.temporalTls,
  );
}

export function createDatabaseProcessRuntime(): ProcessRuntime {
  return new DatabaseProcessRuntime();
}

function readTlsFile(filePath: string, field: string): Buffer {
  try {
    const content = fs.readFileSync(filePath);
    if (content.length === 0) throw new Error("файл пуст");
    return content;
  } catch (error) {
    throw new Error(`${field}: не удалось прочитать TLS-файл ${filePath}`, { cause: error });
  }
}

export function temporalConnectionOptions(config: CoordinatorConfig): ConnectionOptions {
  let tls: ConnectionOptions["tls"] = config.temporalTls;
  if (
    config.temporalTls
    && (config.temporalCaCertPath || config.temporalClientCertPath || config.temporalServerNameOverride)
  ) {
    tls = {
      ...(config.temporalServerNameOverride ? { serverNameOverride: config.temporalServerNameOverride } : {}),
      ...(config.temporalCaCertPath
        ? { serverRootCACertificate: readTlsFile(config.temporalCaCertPath, "AGAT_TEMPORAL_CA_CERT_PATH") }
        : {}),
      ...(config.temporalClientCertPath
        ? {
            clientCertPair: {
              crt: readTlsFile(config.temporalClientCertPath, "AGAT_TEMPORAL_CLIENT_CERT_PATH"),
              key: readTlsFile(config.temporalClientKeyPath, "AGAT_TEMPORAL_CLIENT_KEY_PATH"),
            },
          }
        : {}),
    };
  }
  return {
    address: config.temporalAddress,
    tls,
    ...(config.temporalApiKey ? { apiKey: config.temporalApiKey } : {}),
  };
}
