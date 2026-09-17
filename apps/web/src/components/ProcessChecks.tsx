import type { ProcessIssue } from "../processReadiness";
import { Icon } from "./Icon";

export function ProcessChecks({ issues, onSelect, onClose }: { issues: ProcessIssue[]; onSelect: (id: string) => void; onClose: () => void }) {
  return (
    <section className="process-checks" aria-label="Проверка схемы">
      <header><div><h2>{issues.length ? "Что нужно настроить" : "Схема готова к публикации"}</h2><p>{issues.length ? "Выберите замечание, чтобы открыть нужный шаг." : "Связи и обязательные настройки проверены. При публикации сервер проверит процесс полностью."}</p></div><button className="icon-button" aria-label="Закрыть проверку" onClick={onClose}><Icon name="close" /></button></header>
      {issues.length ? <ul>{issues.map((issue) => <li key={issue.id}>{issue.nodeId ? <button onClick={() => onSelect(issue.nodeId!)}><Icon name="chevron" size={16} />{issue.message}</button> : <span>{issue.message}</span>}</li>)}</ul> : <p className="process-checks__success"><Icon name="check" />Можно опубликовать новую версию.</p>}
    </section>
  );
}
