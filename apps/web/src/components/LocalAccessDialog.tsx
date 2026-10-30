import { useEffect, useRef, useState, type FormEvent } from "react";
import { LOCAL_ROLES, scopesForRoles, type LocalRole, type LocalScope, type LocalPrincipal, type LocalCredential } from "../../../coordinator/src/local-identity-contract";
import { api } from "../lib/api";
import type { ProjectSummary } from "../types";
import { Icon } from "./Icon";

const scopeLabels: Record<LocalScope, string> = { read: "Чтение", run: "Запуск задач", design: "Настройка агентов и процессов", credentials: "Изменение credentials", approve: "Обычные согласования", review: "Рецензирование", admin: "Администрирование" };
export function LocalAccessDialog({ projects, onClose }: { projects: ProjectSummary[]; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [principals, setPrincipals] = useState<LocalPrincipal[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [credentials, setCredentials] = useState<LocalCredential[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [newRole, setNewRole] = useState<LocalRole>("viewer");
  const [newKind, setNewKind] = useState<"user" | "service">("user");
  const principal = principals.find(item => item.subject === selected);
  useEffect(() => {
    dialog.current?.showModal();
    const controller = new AbortController();
    void api.localPrincipals().then(result => { if (!controller.signal.aborted) { setPrincipals(result.principals); setSelected(result.principals[0]?.subject ?? ""); } })
      .catch(failure => { if (!controller.signal.aborted) setError(String(failure)); });
    return () => { controller.abort(); };
  }, []);
  useEffect(() => {
    let active = true;
    setCredentials([]); setToken(null);
    if (selected) void api.localCredentials(selected).then(result => { if (active) setCredentials(result.credentials); })
      .catch(failure => { if (active) setError(String(failure)); });
    return () => { active = false; };
  }, [selected]);
  async function action(work: () => Promise<void>) {
    setPending(true); setError(null); setToken(null);
    try { await work(); } catch (failure) { setError(failure instanceof Error ? failure.message : "Ошибка управления доступом"); }
    finally { setPending(false); }
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    await action(async () => {
      const created = await api.createLocalPrincipal({ username: String(data.get("username")), kind: newKind, roles: [newRole], projectIds: data.getAll("projectIds").map(String) });
      setPrincipals(current => [...current, created]); setSelected(created.subject); form.reset();
    });
  }
  async function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!principal) return; const data = new FormData(event.currentTarget);
    await action(async () => {
      const issued = await api.issueLocalCredential(principal.subject, { label: String(data.get("label")), purpose: data.get("purpose") === "personal" ? "personal" : "api",
        application: String(data.get("application")), roles: data.getAll("roles") as LocalRole[], projectIds: data.getAll("tokenProjects").map(String), scopes: data.getAll("scopes") as LocalScope[],
        expiresAt: new Date(Date.now() + Number(data.get("days")) * 86400000).toISOString() });
      setCredentials(current => [issued.credential, ...current]); setToken(issued.token);
    });
  }
  return <dialog ref={dialog} className="run-dialog local-access-dialog" aria-labelledby="local-access-title" onCancel={onClose} onClose={onClose}>
    <div className="dialog-head"><div><h2 id="local-access-title">Пользователи и доступ</h2><p>Индивидуальные пользователи и приложения этой установки.</p></div>
      <button className="icon-button" type="button" aria-label="Закрыть управление доступом" onClick={onClose}><Icon name="close" /></button></div>
    {error ? <p role="alert">{error}</p> : null}
    <div className="local-access__columns">
      <section aria-label="Создание субъекта"><h3>Новый субъект</h3><form onSubmit={event => void create(event)}>
        <label className="field"><span>Имя субъекта</span><input name="username" required maxLength={80} disabled={pending} /></label>
        <label className="field"><span>Тип</span><select value={newKind} onChange={event => { const kind = event.target.value as "user" | "service"; setNewKind(kind); if (kind === "service" && newRole === "admin") setNewRole("operator"); }}><option value="user">Пользователь</option><option value="service">Приложение</option></select></label>
        <label className="field"><span>Роль</span><select value={newRole} onChange={event => setNewRole(event.target.value as LocalRole)}>{LOCAL_ROLES.filter(role => newKind === "user" || role !== "admin").map(role => <option key={role}>{role}</option>)}</select></label>
        <label className="field"><span>Проекты субъекта</span><select name="projectIds" multiple required size={Math.min(5, projects.length + 1)} key={newRole}>
          {newRole === "admin" ? <option value="*">Все проекты (global admin)</option> : null}{projects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}
        </select></label><button className="button button--secondary" type="submit" disabled={pending}>Создать субъекта</button>
      </form></section>
      <section aria-label="Ключи субъекта"><h3>Ключи и сессии</h3>
        <label className="field"><span>Выбранный субъект</span><select value={selected} disabled={pending} onChange={event => setSelected(event.target.value)}>{principals.map(item => <option key={item.subject} value={item.subject}>{item.username}{item.disabled ? " · отключён" : ""}</option>)}</select></label>
        {principal ? <><p className="local-access__subject">{principal.subject}</p><p>{principal.kind === "service" ? "Приложение" : "Пользователь"} · {principal.roles.join(", ")} · {principal.projectIds.join(", ")}</p>
          <button className="button button--secondary" disabled={pending} onClick={() => void action(async () => {
            const next = await api.updateLocalPrincipal(principal.subject, { disabled: !principal.disabled });
            setPrincipals(current => current.map(item => item.subject === next.subject ? next : item));
          })}>{principal.disabled ? "Включить субъекта" : "Отключить субъекта"}</button>
          {!principal.disabled ? <form key={principal.subject} onSubmit={event => void issue(event)}>
            <h4>Выдать ограниченный ключ</h4>
            <label className="field"><span>Название ключа</span><input name="label" required maxLength={120} /></label>
            <label className="field"><span>Назначение</span><select name="purpose" defaultValue={principal.kind === "user" ? "personal" : "api"}>{principal.kind === "user" ? <option value="personal">Вход в интерфейс</option> : null}<option value="api">API</option></select></label>
            <label className="field"><span>Приложение в аудите</span><input name="application" required maxLength={80} defaultValue={principal.kind === "user" ? "agat-web" : principal.username} /></label>
            <fieldset><legend>Роли ключа</legend>{principal.roles.map(role => <label key={role}><input name="roles" type="checkbox" value={role} defaultChecked /> {role}</label>)}</fieldset>
            <fieldset><legend>Проекты ключа</legend>{(principal.projectIds.includes("*") ? ["*", ...projects.map(project => project.id)] : principal.projectIds).map(id => <label key={id}><input name="tokenProjects" type="checkbox" value={id} defaultChecked={!principal.projectIds.includes("*") || id === "*"} /> {id === "*" ? "Все проекты" : id}</label>)}</fieldset>
            <fieldset><legend>Разрешённые действия</legend>{scopesForRoles(principal.roles).filter(scope => principal.kind === "user" || !["approve", "review"].includes(scope)).map(scope => <label key={scope}><input name="scopes" type="checkbox" value={scope} defaultChecked /> {scopeLabels[scope]}</label>)}</fieldset>
            <label className="field"><span>Срок действия</span><select name="days" defaultValue="30">{[1, 7, 30, 90].map(days => <option value={days} key={days}>{days} дн.</option>)}</select></label>
            <button className="button button--primary" type="submit" disabled={pending}>Выдать ключ</button>
          </form> : null}
        </> : null}
      </section>
    </div>
    {token ? <div className="local-access__secret" role="status"><strong>Ключ показан один раз</strong><p>Сохраните его в менеджере секретов. После закрытия он исчезнет из интерфейса.</p><textarea aria-label="Новый индивидуальный ключ" readOnly value={token} rows={3} spellCheck={false} /><button className="button button--secondary" onClick={() => setToken(null)}>Скрыть ключ</button></div> : null}
    <ul className="local-access__credentials">{credentials.map(credential => <li key={credential.id}><div><strong>{credential.label}</strong><p>{credential.application} · {credential.purpose} · до {new Date(credential.expiresAt).toLocaleDateString("ru")}</p><small>{credential.projectIds.join(", ")} · {credential.scopes.join(", ")}{credential.revoked ? " · отозван" : ""}</small></div>
      {!credential.revoked ? <div><button className="button button--secondary" disabled={pending} onClick={() => void action(async () => { const result = await api.rotateLocalCredential(credential.id); setCredentials(current => [result.credential, ...current.map(item => item.id === credential.id ? { ...item, revoked: true } : item)]); setToken(result.token); })}>Заменить ключ</button>
        <button className="button button--secondary" disabled={pending} onClick={() => void action(async () => { await api.revokeLocalCredential(credential.id); setCredentials(current => current.map(item => item.id === credential.id ? { ...item, revoked: true } : item)); })}>Отозвать ключ</button></div> : null}</li>)}</ul>
    <p className="form-hint">Отзыв или изменение прав закрывает доступ и останавливает связанные активные запуски. Critical/four-eyes согласования требуют отдельной квалификации входа.</p>
  </dialog>;
}
