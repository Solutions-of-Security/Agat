import { useState } from "react";
import { formDefinitionIssues, formFieldLabels } from "../processForms";
import type { ProcessApprovalForm, ProcessFormData, ProcessFormField, ProcessFormFieldType } from "../types";
import { ProcessFormFields } from "./ProcessFormFields";

export function ProcessFormBuilder({ form, onChange }: { form: ProcessApprovalForm | undefined; onChange: (form: ProcessApprovalForm | undefined) => void }) {
  const [previewValues, setPreviewValues] = useState<ProcessFormData>({});
  function addField() {
    const fields = form?.fields ?? [];
    let number = fields.length + 1;
    while (fields.some((field) => field.id === `field${number}`)) number++;
    onChange({ title: form?.title ?? "", description: form?.description ?? "", fields: [...fields, { id: `field${number}`, label: "Новое поле", type: "text", required: false }] });
  }
  function update(index: number, patch: Partial<ProcessFormField>) {
    if (form) onChange({ ...form, fields: form.fields.map((field, itemIndex) => itemIndex === index ? { ...field, ...patch } : field) });
  }
  function move(index: number, offset: number) {
    if (!form) return;
    const fields = [...form.fields];
    [fields[index], fields[index + offset]] = [fields[index + offset]!, fields[index]!];
    onChange({ ...form, fields });
  }
  return (
    <section className="process-form-builder" aria-label="Конструктор формы">
      <h3>Форма для человека</h3>
      <p className="process-field-help">Соберите поля, которые оператор заполнит перед продолжением процесса.</p>
      {form ? <>
        <label className="field"><span>Заголовок формы</span><input maxLength={160} value={form.title} onChange={(event) => onChange({ ...form, title: event.target.value })} /></label>
        <label className="field"><span>Описание формы</span><textarea rows={2} maxLength={2_000} value={form.description} onChange={(event) => onChange({ ...form, description: event.target.value })} /></label>
        {form.fields.map((field, index) => (
          <fieldset className="process-form-builder__field" key={index}>
            <legend>Поле {index + 1}</legend>
            <label className="field"><span>Название поля {index + 1}</span><input maxLength={160} value={field.label} onChange={(event) => update(index, { label: event.target.value })} /></label>
            <div className="process-field-grid">
              <label className="field"><span>Тип поля {index + 1}</span><select value={field.type} onChange={(event) => update(index, { type: event.target.value as ProcessFormFieldType, options: event.target.value === "select" ? ["Да", "Нет"] : undefined })}>
                {Object.entries(formFieldLabels).map(([type, label]) => <option key={type} value={type}>{label}</option>)}
              </select></label>
              <label className="field"><span>Ключ поля {index + 1}</span><input maxLength={64} value={field.id} onChange={(event) => update(index, { id: event.target.value })} /></label>
            </div>
            {field.type === "select" ? <label className="field"><span>Варианты поля {index + 1}, по одному в строке</span><textarea rows={4} value={field.options?.join("\n") ?? ""} onChange={(event) => update(index, { options: event.target.value.split("\n") })} /></label> : null}
            {["text", "textarea", "number"].includes(field.type) ? <label className="field"><span>Подсказка поля {index + 1}</span><input maxLength={300} value={field.placeholder ?? ""} onChange={(event) => update(index, { placeholder: event.target.value })} /></label> : null}
            <label className="approval-toggle"><input type="checkbox" checked={field.required} onChange={(event) => update(index, { required: event.target.checked })} /><span>Обязательное поле {index + 1}</span></label>
            <div className="process-form-builder__actions">
              <button type="button" disabled={index === 0} onClick={() => move(index, -1)} aria-label={`Переместить поле ${index + 1} вверх`}>↑</button>
              <button type="button" disabled={index === form.fields.length - 1} onClick={() => move(index, 1)} aria-label={`Переместить поле ${index + 1} вниз`}>↓</button>
              <button type="button" onClick={() => onChange({ ...form, fields: form.fields.filter((_, itemIndex) => itemIndex !== index) })}>Удалить поле {index + 1}</button>
            </div>
            <small className="process-field-help">Ответ: <code>{`{{ json.form.${field.id} }}`}</code></small>
          </fieldset>
        ))}
        {formDefinitionIssues(form).map((issue, index) => <p className="process-human-form__error" key={index}>{issue}</p>)}
      </> : null}
      <div className="process-form-builder__actions">
        <button className="process-inline-action" type="button" disabled={(form?.fields.length ?? 0) >= 20} onClick={addField}>Добавить поле</button>
        {form ? <button className="process-inline-action" type="button" onClick={() => onChange(undefined)}>Убрать форму</button> : null}
      </div>
      {form?.fields.length ? <details className="process-form-builder__preview"><summary>Предпросмотр формы</summary><ProcessFormFields form={form} values={previewValues} onChange={setPreviewValues} /></details> : null}
    </section>
  );
}
