import { useState } from "react";

import type { Agent, ProcessTemplateCatalog } from "../types";
import { filterProcessTemplates, type CatalogProcessTemplate } from "../processTemplates";

interface ProcessTemplatePickerProps {
  catalog: ProcessTemplateCatalog;
  selected: CatalogProcessTemplate | null;
  agents: Agent[];
  bindings: Record<string, string>;
  disabled: boolean;
  onSelect: (template: CatalogProcessTemplate) => void;
  onBindingChange: (roleId: string, agentId: string) => void;
}

export function ProcessTemplatePicker({ catalog, selected, agents, bindings, disabled, onSelect, onBindingChange }: ProcessTemplatePickerProps) {
  const [categoryId, setCategoryId] = useState("");
  const [query, setQuery] = useState("");
  const matches = filterProcessTemplates(catalog.templates, categoryId, query);
  const category = catalog.categories.find((item) => item.id === categoryId);
  const selectedCategory = catalog.categories.find((item) => item.id === selected?.categoryId);

  return (
    <section className="process-template-picker" aria-label="Каталог процессов">
      <div className="process-template-picker__filters">
        <label className="field">
          <span>Категория процесса</span>
          <select value={categoryId} onChange={(event) => setCategoryId(event.target.value)} disabled={disabled}>
            <option value="">Все категории</option>
            {catalog.categories.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Поиск шаблона</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Например, договор или отчёт" disabled={disabled} />
        </label>
      </div>
      {category ? <p className="process-template-picker__caption">{category.description}</p> : null}
      <p className="process-template-picker__caption" role="status">Найдено шаблонов: {matches.length}</p>
      <div className="process-template-picker__list" aria-label="Шаблоны процессов">
        {matches.map((template) => (
          <button type="button" key={template.id} className={`process-template-card${selected?.id === template.id ? " is-selected" : ""}`}
            aria-pressed={selected?.id === template.id} disabled={disabled} onClick={() => onSelect(template)}>
            <strong>{template.name}</strong>
            <span>{template.description}</span>
            <small>{template.stages.length} этапа с агентами · v{template.version}</small>
          </button>
        ))}
        {matches.length === 0 ? <p className="process-template-picker__caption">По этому запросу шаблонов нет. Измените категорию или поиск.</p> : null}
      </div>
      {selected ? (
        <div className="process-template-detail" aria-label="Выбранный шаблон">
          <h3>{selected.name}</h3>
          <p className="process-template-picker__caption">{selectedCategory?.name} · Ответственный: {selected.ownerRole}</p>
          <p><strong>Результат:</strong> {selected.outcome}</p>
          <p><strong>Подготовьте:</strong> {selected.inputs.join("; ")}.</p>
          <p className="process-template-detail__boundary">{selected.boundary}</p>
          <details>
            <summary>Этапы, требования и пример входа</summary>
            <ol>{selected.stages.map((stage) => <li key={stage.id}>{stage.name}</li>)}</ol>
            <p><strong>Требования категории и процесса</strong></p>
            <ul>{[...(selectedCategory?.requirements ?? []), ...selected.requirements.filter((item) => !/^[A-Z]+-\d+$/.test(item))]
              .map((item) => <li key={item}>{item}</li>)}</ul>
            <p><strong>Критерии приёмки</strong></p>
            <ul>{selected.acceptance.map((item) => <li key={item}>{item}</li>)}</ul>
            <p><strong>Исключения</strong></p>
            <ul>{selected.exceptions.map((item) => <li key={item}>{item}</li>)}</ul>
            <p><strong>Пример входных данных</strong></p>
            <p>{selected.inputExample}</p>
          </details>
          <div className="process-template-picker__bindings">
            {selected.roles.map((role) => (
              <label className="field" key={role.id}>
                <span>{role.name}</span>
                <select id={`template-role-${role.id}`} value={bindings[role.id] ?? ""} onChange={(event) => onBindingChange(role.id, event.target.value)} disabled={disabled}>
                  <option value="">Назначить позже в редакторе</option>
                  {agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name}</option>)}
                </select>
                <small>{role.responsibility}</small>
              </label>
            ))}
          </div>
          <p className="process-template-picker__caption">Будет создан черновик. Перед публикацией назначьте всех агентов, проверьте их инструкции и доступ к данным. После запуска результат ожидает решения человека.</p>
        </div>
      ) : null}
    </section>
  );
}
