import React, { useState, type MouseEvent } from "react";
import type { ScenarioPreflight } from "../scenarioPreflight";
import type { AgatRole } from "../types";
import { canAccessView, parseAppRoute } from "../navigation";

export function ScenarioPreflightPanel({ result, error, onRefresh, onRecover, roles }: {
  result: ScenarioPreflight | null;
  error: string | null;
  onRefresh: () => void;
  onRecover?: (event: MouseEvent<HTMLAnchorElement>, href: string) => void;
  roles?: AgatRole[];
}) {
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  function recoveryAction(href: string, label: string) {
    if (!roles || canAccessView(parseAppRoute(href).view, roles)) return <a href={href} onClick={(event) => onRecover?.(event, href)}>{label}</a>;
    return <div><span>{label}. Нужен администратор.</span><button type="button" className="button button--secondary" data-recovery-href={href} onClick={async () => {
      const url = new URL(href, window.location.href).href;
      try { await navigator.clipboard.writeText(url); setCopyMessage("Ссылка для администратора скопирована. Укажите ему текущий проект."); }
      catch { setCopyMessage(`Скопируйте ссылку для администратора: ${url}`); }
    }}>Скопировать ссылку администратору</button></div>;
  }
  const states = result ? [
    ["Сохранено", result.saved], ["Можно в очередь", result.queueable],
    ["Можно выполнить сейчас", result.runnableNow], ["Проверено на сценарии", result.scenarioVerified],
  ] as const : [];
  return <section className="scenario-preflight" aria-label="Готовность сценария" aria-busy={!result && !error}>
    <header><h3>Готовность сценария</h3><button type="button" className="button button--secondary" onClick={onRefresh}>Проверить снова</button></header>
    {error ? <p role="alert" className="form-error">{error}. Повторите проверку перед запуском.</p> : !result ? <p role="status">Проверяем сценарий…</p> : <>
      <ul className="scenario-preflight__states" aria-label="Состояния сценария">{states.map(([label, ready]) => <li key={label} data-ready={ready}><strong>{label}</strong><span>{ready ? "Да" : "Нет"}</span></li>)}</ul>
      {result.version ? <p>Проверена закреплённая версия v{result.version}.</p> : null}
      {result.blockers.length ? <ul className="scenario-preflight__blockers">{result.blockers.map((blocker) => <li key={blocker.code}>
        <p>{blocker.message}</p><small>{blocker.blocks === "queue" ? "Блокирует постановку в очередь" : "Блокирует выполнение сейчас; можно ожидать в очереди"}</small>
        {recoveryAction(blocker.recovery.href, blocker.recovery.label)}
      </li>)}</ul> : <p>Блокирующих причин сейчас нет.</p>}
      {result.verification ? <p>Сценарий завершён на этой версии и конфигурации. <a href={result.verification.href} onClick={(event) => onRecover?.(event, result.verification!.href)}>Открыть подтверждающий запуск</a></p>
        : <p>Сценарий ещё не проверен: нужен полный успешный запуск с текущими агентами, знаниями и инструментами, включая предусмотренные согласования.</p>}
      {result.notices.map((notice) => <p key={notice}>{notice}</p>)}
      {copyMessage ? <p role="status">{copyMessage}</p> : null}
      <p className="muted">Доступность и policy повторно проверяются при выполнении.</p>
    </>}
  </section>;
}
