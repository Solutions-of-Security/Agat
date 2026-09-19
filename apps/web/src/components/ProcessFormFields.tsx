import { useId } from "react";
import type { ProcessApprovalForm, ProcessFormData } from "../types";

export function ProcessFormFields({ form, values, errors = {}, disabled = false, onChange }: {
  form: ProcessApprovalForm;
  values: ProcessFormData;
  errors?: Record<string, string>;
  disabled?: boolean;
  onChange: (values: ProcessFormData) => void;
}) {
  const prefix = useId();
  return (
    <fieldset className="process-human-form" disabled={disabled}>
      <legend>{form.title || "Данные для продолжения"}</legend>
      {form.description ? <p>{form.description}</p> : null}
      {form.fields.map((field, index) => {
        const id = `${prefix}-${index}`;
        const error = Object.prototype.hasOwnProperty.call(errors, field.id) ? errors[field.id] : undefined;
        const value = Object.prototype.hasOwnProperty.call(values, field.id) ? values[field.id] : undefined;
        const attributes = { id, required: field.required, "aria-invalid": error ? true as const : undefined, "aria-describedby": error ? `${id}-error` : undefined };
        const update = (value: string | boolean) => onChange({ ...values, [field.id]: value });
        return (
          <div className={`process-human-form__field${field.type === "checkbox" ? " process-human-form__field--checkbox" : ""}`} key={index}>
            {field.type === "checkbox" ? <input {...attributes} type="checkbox" checked={value === true} onChange={(event) => update(event.target.checked)} /> : null}
            <label htmlFor={id}>{field.label || "Без названия"}{field.required ? <span title="Обязательное поле"> *</span> : null}</label>
            {field.type === "textarea" ? (
              <textarea {...attributes} rows={4} maxLength={10_000} placeholder={field.placeholder} value={String(value ?? "")} onChange={(event) => update(event.target.value)} />
            ) : field.type === "select" ? (
              <select {...attributes} value={String(value ?? "")} onChange={(event) => update(event.target.value)}>
                <option value="">Выберите вариант</option>
                {field.options?.map((option, optionIndex) => <option value={option} key={optionIndex}>{option}</option>)}
              </select>
            ) : field.type !== "checkbox" ? (
              <input {...attributes} type={field.type} step={field.type === "number" ? "any" : undefined} maxLength={10_000} placeholder={field.placeholder} value={String(value ?? "")} onChange={(event) => update(event.target.value)} />
            ) : null}
            {error ? <small id={`${id}-error`} className="process-human-form__error" role="alert">{error}</small> : null}
          </div>
        );
      })}
    </fieldset>
  );
}
