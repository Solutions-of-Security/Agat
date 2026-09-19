import type { ProcessApprovalForm, ProcessFormData, ProcessFormFieldType } from "./types.js";

const FIELD_TYPES = new Set<ProcessFormFieldType>(["text", "textarea", "number", "date", "select", "checkbox"]);
const RESERVED_IDS = new Set(["__proto__", "prototype", "constructor"]);

function record(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field}: нужен объект`);
  return value as Record<string, unknown>;
}

function text(value: unknown, field: string, max: number, required = false): string {
  if (value === undefined && !required) return "";
  if (typeof value !== "string") throw new Error(`${field}: нужна строка`);
  const result = value.trim();
  if (required && !result) throw new Error(`${field}: заполните значение`);
  if (result.length > max) throw new Error(`${field}: максимум ${max} символов`);
  return result;
}

export function normalizeProcessApprovalForm(raw: unknown, strict = true): ProcessApprovalForm {
  const form = record(raw, "Форма");
  if (!Array.isArray(form.fields) || form.fields.length > 20 || (strict && !form.fields.length)) {
    throw new Error("Форма должна содержать от 1 до 20 полей");
  }
  const ids = new Set<string>();
  const fields = form.fields.map((rawField, index) => {
    const field = record(rawField, `Поле ${index + 1}`);
    const id = text(field.id, "Ключ поля", 64, true);
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(id) || RESERVED_IDS.has(id)) throw new Error(`Недопустимый ключ поля: ${id}`);
    if (ids.has(id)) throw new Error(`Ключи полей должны быть уникальны: ${id}`);
    ids.add(id);
    const type = field.type as ProcessFormFieldType;
    if (!FIELD_TYPES.has(type)) throw new Error(`Поле ${id}: неизвестный тип`);
    if (field.required !== undefined && typeof field.required !== "boolean") throw new Error(`Поле ${id}: required должен быть boolean`);
    let options: string[] | undefined;
    if (type === "select") {
      if (!Array.isArray(field.options) || field.options.length > 50 || (strict && !field.options.length)) {
        throw new Error(`Поле ${id}: укажите от 1 до 50 вариантов`);
      }
      options = field.options.map((option) => text(option, `Поле ${id}: вариант`, 200, strict));
      if (strict && new Set(options).size !== options.length) throw new Error(`Поле ${id}: варианты должны быть уникальны`);
    }
    return {
      id, type,
      label: text(field.label, `Поле ${id}: название`, 160, strict),
      required: field.required === true,
      placeholder: text(field.placeholder, `Поле ${id}: подсказка`, 300),
      ...(options ? { options } : {}),
    };
  });
  return { title: text(form.title, "Название формы", 160), description: text(form.description, "Описание формы", 2_000), fields };
}

/** Validate the transport independently of the immutable stage's form schema. */
export function parseProcessFormData(raw: unknown): ProcessFormData {
  const data = record(raw, "Ответы формы");
  if (Object.keys(data).length > 20 || JSON.stringify(data).length > 100_000) throw new Error("Ответы формы слишком велики");
  for (const [key, value] of Object.entries(data)) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key) || RESERVED_IDS.has(key)) throw new Error(`Недопустимый ключ поля: ${key}`);
    if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") throw new Error(`Поле ${key}: недопустимый ответ`);
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error(`Поле ${key}: нужно конечное число`);
    if (typeof value === "string" && value.length > 10_000) throw new Error(`Поле ${key}: максимум 10000 символов`);
  }
  return data as ProcessFormData;
}

export function validateProcessFormData(form: ProcessApprovalForm | undefined, raw: unknown): ProcessFormData {
  const data = parseProcessFormData(raw ?? {});
  const fields = form?.fields ?? [];
  const allowed = new Set(fields.map((field) => field.id));
  for (const key of Object.keys(data)) if (!allowed.has(key)) throw new Error(`Неизвестное поле формы: ${key}`);
  const result: ProcessFormData = {};
  for (const field of fields) {
    const value = Object.prototype.hasOwnProperty.call(data, field.id) ? data[field.id] : undefined;
    const absent = value === undefined || (typeof value === "string" && !value.trim());
    if (absent) {
      if (field.required) throw new Error(`Заполните поле «${field.label}»`);
      continue;
    }
    if (field.type === "number") {
      if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`Поле «${field.label}»: нужно число`);
    } else if (field.type === "checkbox") {
      if (typeof value !== "boolean") throw new Error(`Поле «${field.label}»: нужен флажок`);
      if (field.required && !value) throw new Error(`Подтвердите поле «${field.label}»`);
    } else {
      if (typeof value !== "string") throw new Error(`Поле «${field.label}»: нужна строка`);
      if (field.type === "select" && !field.options?.includes(value)) throw new Error(`Поле «${field.label}»: выберите вариант из списка`);
      if (field.type === "date" && (!/^\d{4}-\d{2}-\d{2}$/.test(value)
        || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value)) {
        throw new Error(`Поле «${field.label}»: нужна дата ГГГГ-ММ-ДД`);
      }
    }
    result[field.id] = value;
  }
  return result;
}

export function processFormOutput(input: string | null, data: ProcessFormData): string {
  let context: unknown = input ?? "";
  try { context = JSON.parse(input ?? ""); } catch { /* Plain text remains plain text. */ }
  const output = JSON.stringify({ input: context, form: data });
  if (output.length > 200_000) throw new Error("Результат формы превышает 200000 символов");
  return output;
}
