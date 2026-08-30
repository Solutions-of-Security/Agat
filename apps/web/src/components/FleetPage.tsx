import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../lib/api";
import type {
  AgatRole,
  ComputeNode,
  FleetSnapshot,
  ProjectFleetPolicy,
  RegisterWorkerReleaseRequest,
} from "../types";
import { Icon } from "./Icon";

interface FleetPageProps {
  projectId: string;
  nodes: ComputeNode[];
  roles: AgatRole[];
}

type PolicyDraft = Omit<ProjectFleetPolicy, "revision"> & { revision: number };

const initialManifest = JSON.stringify({
  schemaVersion: 1,
  releaseId: "worker-1.7.0",
  version: "1.7.0",
  artifactDigest: `sha256:${"0".repeat(64)}`,
  platforms: ["linux"],
  issuedAt: new Date().toISOString(),
  metadata: { channel: "stable" },
}, null, 2);

function dateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function shortId(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 10)}…${value.slice(-6)}`;
}

function policyFromSnapshot(snapshot: FleetSnapshot): PolicyDraft {
  return { ...snapshot.policy, allowedRegions: [...snapshot.policy.allowedRegions] };
}

export function FleetPage({ projectId, nodes, roles }: FleetPageProps) {
  const [snapshot, setSnapshot] = useState<FleetSnapshot | null>(null);
  const [policy, setPolicy] = useState<PolicyDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manifestText, setManifestText] = useState(initialManifest);
  const [releaseKeyId, setReleaseKeyId] = useState("");
  const [releaseSignature, setReleaseSignature] = useState("");
  const [rolloutReleaseId, setRolloutReleaseId] = useState("");
  const [rolloutRing, setRolloutRing] = useState("stable");
  const [rolloutPercentage, setRolloutPercentage] = useState(100);
  const canManage = roles.includes("admin");

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await api.fleet(signal);
      setSnapshot(next);
      setPolicy(policyFromSnapshot(next));
      setRolloutReleaseId((current) => current || next.releases.find((release) => release.status === "active")?.id || "");
      setError(null);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить Fleet snapshot");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => void load(), 10_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load, projectId]);

  const readyReplicas = snapshot?.replicas.filter((replica) => replica.status === "ready").length ?? 0;
  const queued = snapshot?.queues
    .filter((queue) => queue.status === "queued")
    .reduce((total, queue) => total + queue.count, 0) ?? 0;
  const activeReleases = snapshot?.releases.filter((release) => release.status === "active") ?? [];
  const currentRollout = useMemo(() => snapshot?.rollouts.find(
    (rollout) => rollout.region === snapshot.cell.region && rollout.ring === rolloutRing,
  ) ?? null, [rolloutRing, snapshot]);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy || !canManage) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateFleetPolicy({
        homeRegion: policy.homeRegion.trim(),
        allowedRegions: policy.allowedRegions.map((region) => region.trim()).filter(Boolean),
        residencyDomain: policy.residencyDomain.trim(),
        queueName: policy.queueName.trim(),
        maxQueuedTasks: policy.maxQueuedTasks,
        maxRunningTasks: policy.maxRunningTasks,
        expectedRevision: policy.revision,
      });
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось обновить project Fleet policy");
    } finally {
      setBusy(false);
    }
  }

  async function registerRelease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canManage) return;
    setBusy(true);
    setError(null);
    try {
      const manifest = JSON.parse(manifestText) as RegisterWorkerReleaseRequest["manifest"];
      await api.registerWorkerRelease({ manifest, keyId: releaseKeyId.trim(), signature: releaseSignature.trim() });
      setReleaseSignature("");
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось зарегистрировать signed worker release");
    } finally {
      setBusy(false);
    }
  }

  async function revokeRelease(releaseId: string) {
    if (!canManage) return;
    const reason = window.prompt(`Причина немедленного revoke ${releaseId}`)?.trim();
    if (!reason) return;
    setBusy(true);
    setError(null);
    try {
      await api.revokeWorkerRelease(releaseId, reason);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось отозвать worker release");
    } finally {
      setBusy(false);
    }
  }

  async function saveRollout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot || !canManage || !rolloutReleaseId) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateWorkerRollout({
        releaseId: rolloutReleaseId,
        region: snapshot.cell.region,
        ring: rolloutRing.trim(),
        percentage: rolloutPercentage,
        expectedRevision: currentRollout?.revision ?? 0,
      });
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось обновить staged rollout");
    } finally {
      setBusy(false);
    }
  }

  async function setRing(nodeId: string, ring: string) {
    if (!canManage) return;
    setBusy(true);
    setError(null);
    try {
      await api.setNodeRolloutRing(nodeId, ring);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось изменить rollout ring worker-узла");
    } finally {
      setBusy(false);
    }
  }

  if (!snapshot || !policy) {
    return (
      <main className="main-column section-page ha-page">
        <div className="process-page-loading"><span className="boot-mark" /><strong>{error ?? "Загружаем Fleet HA-cell"}</strong></div>
      </main>
    );
  }

  return (
    <main className="main-column section-page ha-page" id="fleet">
      <div className="page-title page-title--section">
        <div><h1>Fleet и HA</h1><p>Regional cell, project queues, signed workers, tenant boundary и SIEM delivery</p></div>
        <button className="button button--secondary page-title__action page-title__action--always" type="button" onClick={() => void load()} disabled={busy}>
          <Icon name="repeat" size={17} />Обновить
        </button>
      </div>

      {error ? <div className="ha-alert"><Icon name="warning" size={17} />{error}</div> : null}
      {!snapshot.cell.haReady ? (
        <div className="ha-alert ha-alert--warning"><Icon name="warning" size={17} />HA не готов: нужны PostgreSQL и минимум две свежие coordinator replicas.</div>
      ) : null}

      <section className="ha-summary" aria-label="Fleet summary">
        <div><strong className={snapshot.cell.haReady ? "is-ok" : "is-warning"}>{snapshot.cell.haReady ? "HA" : "DEGRADED"}</strong><span>{snapshot.cell.stateStoreDriver}</span></div>
        <div><strong>{readyReplicas}/{snapshot.replicas.length}</strong><span>coordinator replicas</span></div>
        <div><strong>{queued}</strong><span>queued в проекте</span></div>
        <div><strong>{snapshot.auditExport.pending + snapshot.auditExport.delivering}</strong><span>audit ждут SIEM</span></div>
      </section>

      <section className="ha-grid ha-grid--two">
        <article className="ha-card">
          <header><div><span className="eyebrow">REGIONAL CELL</span><h2>{snapshot.cell.region} / {snapshot.cell.residencyDomain}</h2></div><code>{shortId(snapshot.cell.instanceId)}</code></header>
          <div className="ha-replica-list">
            {snapshot.replicas.map((replica) => (
              <div key={replica.instanceId}>
                <span className={`status-dot status-dot--${replica.status === "ready" ? "online" : "offline"}`} />
                <code title={replica.instanceId}>{shortId(replica.instanceId)}</code>
                <span>{replica.stateStoreDriver}</span>
                <time>{dateTime(replica.lastSeen)}</time>
              </div>
            ))}
          </div>
        </article>

        <article className="ha-card">
          <header><div><span className="eyebrow">AUDIT OUTBOX</span><h2>SIEM delivery</h2></div><code>at-least-once</code></header>
          <dl className="ha-metrics">
            <div><dt>Pending</dt><dd>{snapshot.auditExport.pending}</dd></div>
            <div><dt>Delivering</dt><dd>{snapshot.auditExport.delivering}</dd></div>
            <div><dt>Delivered</dt><dd>{snapshot.auditExport.delivered}</dd></div>
            <div><dt>Oldest</dt><dd>{dateTime(snapshot.auditExport.oldestPendingAt)}</dd></div>
          </dl>
        </article>
      </section>

      <section className="ha-card ha-policy-card">
        <header><div><span className="eyebrow">PROJECT POLICY · REV {policy.revision}</span><h2>Queue quota и residency</h2></div><code>{projectId}</code></header>
        <form className="ha-form" onSubmit={savePolicy}>
          <label className="field"><span>Home region</span><input value={policy.homeRegion} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, homeRegion: event.target.value }))} /></label>
          <label className="field"><span>Residency domain</span><input value={policy.residencyDomain} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, residencyDomain: event.target.value }))} /></label>
          <label className="field"><span>Allowed regions</span><input value={policy.allowedRegions.join(", ")} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, allowedRegions: event.target.value.split(",") }))} /></label>
          <label className="field"><span>Queue</span><input value={policy.queueName} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, queueName: event.target.value }))} /></label>
          <label className="field"><span>Max outstanding</span><input type="number" min={1} max={100000} value={policy.maxQueuedTasks} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, maxQueuedTasks: Number(event.target.value) }))} /></label>
          <label className="field"><span>Max running</span><input type="number" min={1} max={10000} value={policy.maxRunningTasks} disabled={!canManage || busy} onChange={(event) => setPolicy((current) => current && ({ ...current, maxRunningTasks: Number(event.target.value) }))} /></label>
          {canManage ? <button className="button button--primary" type="submit" disabled={busy}><Icon name="save" size={16} />Сохранить optimistic update</button> : null}
        </form>
        <div className="ha-queue-list">
          {snapshot.queues.length ? snapshot.queues.map((queue) => (
            <div key={`${queue.region}:${queue.queueName}:${queue.status}`}><code>{queue.queueName}</code><span>{queue.region}</span><span>{queue.status}</span><strong>{queue.count}</strong></div>
          )) : <p>Очередь проекта пуста.</p>}
        </div>
      </section>

      <section className="ha-grid ha-grid--release">
        <article className="ha-card">
          <header><div><span className="eyebrow">TRUSTED SUPPLY CHAIN</span><h2>Signed worker releases</h2></div><strong>{activeReleases.length} active</strong></header>
          <div className="ha-release-list">
            {snapshot.releases.length ? snapshot.releases.map((release) => (
              <div key={release.id} className={release.status === "revoked" ? "is-revoked" : ""}>
                <div><strong>{release.version}</strong><code>{release.id}</code></div>
                <span>{release.keyId}</span>
                <code title={release.artifactDigest}>{shortId(release.artifactDigest)}</code>
                <time>{dateTime(release.issuedAt)}</time>
                {canManage && release.status === "active" ? <button className="button button--danger" type="button" disabled={busy} onClick={() => void revokeRelease(release.id)}>Revoke</button> : <em>{release.status}</em>}
              </div>
            )) : <p>Подписанные releases ещё не зарегистрированы.</p>}
          </div>
          {canManage ? (
            <details className="ha-details">
              <summary>Зарегистрировать Ed25519 manifest</summary>
              <form className="ha-register-form" onSubmit={registerRelease}>
                <label className="field"><span>Manifest JSON</span><textarea value={manifestText} onChange={(event) => setManifestText(event.target.value)} /></label>
                <div><label className="field"><span>Trust key ID</span><input required value={releaseKeyId} onChange={(event) => setReleaseKeyId(event.target.value)} /></label><label className="field"><span>Signature · base64</span><input required value={releaseSignature} onChange={(event) => setReleaseSignature(event.target.value)} /></label></div>
                <button className="button button--primary" type="submit" disabled={busy}>Verify и зарегистрировать</button>
              </form>
            </details>
          ) : null}
        </article>

        <article className="ha-card">
          <header><div><span className="eyebrow">DETERMINISTIC COHORT</span><h2>Staged rollout</h2></div><code>{snapshot.cell.region}</code></header>
          <form className="ha-rollout-form" onSubmit={saveRollout}>
            <label className="field"><span>Target release</span><select value={rolloutReleaseId} disabled={!canManage || busy} onChange={(event) => setRolloutReleaseId(event.target.value)}><option value="">Выберите release</option>{activeReleases.map((release) => <option value={release.id} key={release.id}>{release.version} · {release.id}</option>)}</select></label>
            <label className="field"><span>Ring</span><input value={rolloutRing} disabled={!canManage || busy} onChange={(event) => setRolloutRing(event.target.value)} /></label>
            <label className="field"><span>Target cohort · {rolloutPercentage}%</span><input type="range" min={0} max={100} step={5} value={rolloutPercentage} disabled={!canManage || busy} onChange={(event) => setRolloutPercentage(Number(event.target.value))} /></label>
            <p>{currentRollout ? `Fallback ${currentRollout.fallbackVersion ?? "не задан"} · revision ${currentRollout.revision}` : "Первый release ring должен быть развёрнут на 100%."}</p>
            {canManage ? <button className="button button--primary" type="submit" disabled={busy || !rolloutReleaseId}>Обновить rollout</button> : null}
          </form>
          <div className="ha-rollout-list">
            {snapshot.rollouts.map((rollout) => <div key={rollout.id}><code>{rollout.ring}</code><strong>{rollout.percentage}%</strong><span>{rollout.version}</span><span>fallback {rollout.fallbackVersion ?? "—"}</span><em>rev {rollout.revision}</em></div>)}
          </div>
        </article>
      </section>

      <section className="ha-card ha-workers">
        <header><div><span className="eyebrow">WORKER COHORTS</span><h2>Region, residency и release identity</h2></div><strong>{nodes.length} nodes</strong></header>
        <div className="ha-worker-list">
          {nodes.map((node) => (
            <div key={node.id}>
              <span className={`status-dot status-dot--${node.status}`} />
              <strong>{node.name}</strong>
              <span>{node.region}/{node.residencyDomain}</span>
              <code>{node.release.id ?? "unsigned"}</code>
              <span className={node.release.verified ? "is-ok" : "is-warning"}>{node.release.verified ? "verified" : "unverified"}</span>
              {canManage ? (
                <select aria-label={`Rollout ring ${node.name}`} value={node.release.rolloutRing} disabled={busy} onChange={(event) => void setRing(node.id, event.target.value)}>
                  {[...new Set([node.release.rolloutRing, "canary", "stable"])].map((ring) => <option value={ring} key={ring}>{ring}</option>)}
                </select>
              ) : <code>{node.release.rolloutRing}</code>}
            </div>
          ))}
          {!nodes.length ? <p>Workers ещё не подключены.</p> : null}
        </div>
      </section>
    </main>
  );
}
