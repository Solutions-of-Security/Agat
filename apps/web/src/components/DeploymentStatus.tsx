import type { DeploymentSnapshot } from "../../../coordinator/src/deployment-contract";

export function DeploymentStatus({ deployment }: { deployment?: DeploymentSnapshot }) {
  if (!deployment) return null;
  return <details className="deployment-status" aria-label="Профиль и возможности">
    <summary>{deployment.label} · {deployment.authentication.label}</summary>
    <p>Версия контракта {deployment.contractVersion}. Доступность функций не подтверждает качество результата или production qualification.</p>
    {deployment.blockers.map((issue) => <p key={issue.code}>{issue.message}</p>)}
    <ul>{deployment.capabilities.map((capability) => <li key={capability.operation} data-available={capability.available}>
      <strong>{capability.label}: {capability.available ? "доступно" : "недоступно"}</strong>
      <span>{capability.message}</span>
    </li>)}</ul>
  </details>;
}
