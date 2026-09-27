import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { decisionKindLabels, decisionShadowDraft, decisionShadowFormResult, defaultDecisionOptions,
  type DecisionKind, type DecisionOptionDraft } from "../decisionShadowForm";
import { useModalFocus } from "../hooks/useModalFocus";
import type { DecisionShadowConfig } from "../types";
import { Icon } from "./Icon";

export function DecisionShadowEditor({ value, onChange }: {
  value?: DecisionShadowConfig; onChange: (value: DecisionShadowConfig | undefined) => void;
}) {
  const [editing, setEditing] = useState(false);
  return <section className="process-form-builder process-shadow-editor" aria-label="Локальная проверка">
    <h3>Локальная проверка</h3>
    <p className="process-field-help">Локальная модель оценивает входные данные шага. Основной агент определяет результат и маршрут; проверка видна в истории запуска.</p>
    {value ? <div className="process-shadow-summary"><strong>{decisionKindLabels[value.kind]}</strong><p>{value.question}</p>
      <small>{value.options.length} варианта · ожидание {value.timeoutMs} мс</small></div> : <p className="process-field-help">Проверка для этого шага не настроена.</p>}
    <div className="process-form-builder__actions">
      <button type="button" onClick={() => setEditing(true)}>{value ? "Изменить проверку" : "Настроить проверку"}</button>
      {value ? <button type="button" onClick={() => onChange(undefined)}>Убрать проверку</button> : null}
    </div>
    {editing ? <DecisionShadowDialog value={value} onClose={() => setEditing(false)} onApply={config => { onChange(config); setEditing(false); }} /> : null}
  </section>;
}

function DecisionShadowDialog({ value, onApply, onClose }: {
  value?: DecisionShadowConfig; onApply: (value: DecisionShadowConfig) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [draft, setDraft] = useState(() => decisionShadowDraft(value));
  const [submitted, setSubmitted] = useState(false);
  const issues = submitted ? decisionShadowFormResult(draft).issues : [];
  useModalFocus(true, dialog, onClose);
  useEffect(() => {
    const element = dialog.current!; element.showModal();
    return () => element.close();
  }, []);

  function updateOption(index: number, patch: Partial<DecisionOptionDraft>) {
    setDraft(current => ({ ...current, options: current.options.map((option, position) => position === index ? { ...option, ...patch } : option) }));
  }
  function move(index: number, direction: number) {
    setDraft(current => {
      const options = [...current.options];
      [options[index], options[index + direction]] = [options[index + direction], options[index]];
      return { ...current, options };
    });
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    const result = decisionShadowFormResult(draft);
    setSubmitted(true);
    if (result.config) onApply(result.config);
  }

  return createPortal(<dialog ref={dialog} className="run-dialog decision-shadow-dialog" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); onClose(); }}>
    <form onSubmit={submit} noValidate>
      <div className="dialog-head"><div><h2 id={titleId}>Локальная проверка шага</h2><p>Дополнительная оценка входа шага в режиме наблюдения.</p></div>
        <button type="button" className="icon-button" aria-label="Закрыть проверку" onClick={onClose}><Icon name="close" /></button></div>
      <label className="field"><span id={`${titleId}-type`}>Тип проверки</span><select aria-labelledby={`${titleId}-type`} aria-describedby={`${titleId}-type-help`} data-autofocus value={draft.kind} onChange={event => {
        const kind = event.target.value as DecisionKind; setDraft(current => ({ ...current, kind, options: defaultDecisionOptions(kind) })); setSubmitted(false);
      }}>{Object.entries(decisionKindLabels).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select>
        <small id={`${titleId}-type-help`}>При выборе другого типа задайте варианты заново.</small></label>
      <label className="field"><span id={`${titleId}-question`}>Вопрос проверки</span><textarea aria-labelledby={`${titleId}-question`} rows={3} maxLength={4000} value={draft.question} onChange={event => setDraft({ ...draft, question: event.target.value })} /></label>
      <label className="field"><span id={`${titleId}-profile`}>Профиль модели (profileJson)</span><textarea aria-labelledby={`${titleId}-profile`} aria-describedby={`${titleId}-profile-help`} className="decision-shadow-profile" rows={4} maxLength={32000} spellCheck={false} value={draft.profileJson} onChange={event => setDraft({ ...draft, profileJson: event.target.value })} />
        <small id={`${titleId}-profile-help`}>Вставьте точную строку profileJson из ответа health локальной модели. Она закрепляет модель, пороги и калибровку.</small></label>
      <label className="field"><span id={`${titleId}-timeout`}>Время ожидания, мс</span><input aria-labelledby={`${titleId}-timeout`} type="number" min={100} max={10000} step={1} value={draft.timeout} onChange={event => setDraft({ ...draft, timeout: event.target.value })} /></label>
      <div className="process-form-builder">
        {draft.options.map((option, index) => <fieldset className="process-form-builder__field" key={option.key}>
          <legend>Вариант {index + 1}</legend>
          <label className="field"><span id={`${titleId}-${option.key}-id`}>Код варианта {index + 1}</span><input aria-labelledby={`${titleId}-${option.key}-id`} maxLength={80} value={option.id} onChange={event => updateOption(index, { id: event.target.value })} /></label>
          <label className="field"><span id={`${titleId}-${option.key}-description`}>Описание варианта {index + 1}</span><textarea aria-labelledby={`${titleId}-${option.key}-description`} rows={2} maxLength={2000} value={option.description} onChange={event => updateOption(index, { description: event.target.value })} /></label>
          {draft.kind === "choice" ? <label className="approval-toggle"><input type="checkbox" checked={option.abstain} onChange={event => updateOption(index, { abstain: event.target.checked })} /><span>Вариант {index + 1} означает отказ от оценки</span></label>
            : draft.kind === "boolean" ? <label className="field"><span id={`${titleId}-${option.key}-value`}>Значение варианта {index + 1}</span><select aria-labelledby={`${titleId}-${option.key}-value`} value={option.value} onChange={event => updateOption(index, { value: event.target.value })}><option value="false">Нет</option><option value="true">Да</option></select></label>
              : <label className="field"><span id={`${titleId}-${option.key}-value`}>Значение варианта {index + 1}</span><input aria-labelledby={`${titleId}-${option.key}-value`} inputMode="decimal" value={option.value} onChange={event => updateOption(index, { value: event.target.value })} /></label>}
          <div className="process-form-builder__actions">
            <button type="button" disabled={index === 0} aria-label={`Переместить вариант ${index + 1} вверх`} onClick={() => move(index, -1)}>↑</button>
            <button type="button" disabled={index === draft.options.length - 1} aria-label={`Переместить вариант ${index + 1} вниз`} onClick={() => move(index, 1)}>↓</button>
            {draft.kind !== "boolean" ? <button type="button" disabled={draft.options.length <= 2} onClick={() => setDraft({ ...draft, options: draft.options.filter((_, position) => position !== index) })}>Удалить вариант {index + 1}</button> : null}
          </div>
        </fieldset>)}
        {draft.kind !== "boolean" ? <div className="process-form-builder__actions"><button type="button" disabled={draft.options.length >= 10} onClick={() => {
          const key = Math.max(...draft.options.map(option => option.key)) + 1;
          setDraft({ ...draft, options: [...draft.options, { key, id: "", description: "", abstain: false, value: "" }] });
        }}>Добавить вариант</button></div> : null}
      </div>
      <p className="process-field-help">Проверка начнёт выполняться после публикации процесса, если локальная модель подключена и проверки включены администратором. Основной маршрут сохраняется.</p>
      {issues.length ? <div className="form-error" role="alert"><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></div> : null}
      <div className="dialog-actions"><button className="button button--secondary" type="button" onClick={onClose}>Отмена</button><button className="button button--primary" type="submit">Применить к шагу</button></div>
    </form>
  </dialog>, document.body);
}
