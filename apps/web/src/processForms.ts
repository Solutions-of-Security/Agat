import type { ProcessApprovalForm, ProcessFormData, ProcessFormFieldType } from "./types";

export const formFieldLabels: Record<ProcessFormFieldType, string> = {
  text: "Текст", textarea: "Многострочный текст", number: "Число", date: "Дата", select: "Список выбора", checkbox: "Флажок",
};

export function formDefinitionIssues(form: ProcessApprovalForm): string[] {
  const issues: string[] = [];
  const keys = new Set<string>();
  if (!form.fields.length || form.fields.length > 20) issues.push("добавьте от 1 до 20 полей формы.");
  for (const field of form.fields) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(field.id) || ["constructor", "prototype"].includes(field.id) || keys.has(field.id)) {
      issues.push(`задайте уникальный ключ для поля «${field.label || field.id}» (латиница, цифры, _).`);
    }
    keys.add(field.id);
    if (!field.label.trim()) issues.push(`задайте название поля «${field.id}».`);
    if (field.type === "select" && (!field.options?.length || field.options.length > 50 || field.options.some((option) => !option.trim())
      || new Set(field.options.map((option) => option.trim())).size !== field.options.length)) {
      issues.push(`укажите от 1 до 50 непустых, разных вариантов поля «${field.label}».`);
    }
  }
  return issues;
}

export function prepareProcessFormData(form: ProcessApprovalForm, values: ProcessFormData): { data: ProcessFormData; errors: Record<string, string> } {
  const data: ProcessFormData = {};
  const errors: Record<string, string> = {};
  for (const field of form.fields) {
    const value = Object.prototype.hasOwnProperty.call(values, field.id) ? values[field.id] : undefined;
    if (value === undefined || (typeof value === "string" && !value.trim())) {
      if (field.required) errors[field.id] = "Заполните обязательное поле";
      continue;
    }
    if (field.type === "checkbox") {
      if (typeof value !== "boolean" || (field.required && !value)) errors[field.id] = "Установите флажок для подтверждения";
      else data[field.id] = value;
    } else if (field.type === "number") {
      const number = typeof value === "boolean" ? Number.NaN : Number(value);
      if (!Number.isFinite(number)) errors[field.id] = "Введите число";
      else data[field.id] = number;
    } else if (typeof value !== "string") {
      errors[field.id] = "Введите текст";
    } else if (field.type === "select" && !field.options?.includes(value)) {
      errors[field.id] = "Выберите вариант из списка";
    } else if (field.type === "date" && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value)) {
      errors[field.id] = "Укажите корректную дату";
    } else if (value.length > 10_000) {
      errors[field.id] = "Максимум 10000 символов";
    } else data[field.id] = value;
  }
  return { data, errors };
}
