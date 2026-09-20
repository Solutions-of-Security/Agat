import { useEffect, useState } from "react";
import type { AgatRole, KnowledgeCollection, ProcessDefinition, ProcessWebhook } from "../types";
import type { ScenarioTrigger } from "../scenarioPreflight";
import { api } from "../lib/api";
import { useScenarioPreflight } from "../hooks/useScenarioPreflight";
import { KnowledgeCollectionPicker } from "./KnowledgeCollectionPicker";
import { ScenarioPreflightPanel } from "./ScenarioPreflightPanel";
import { useRecoveryFocus, useRecoveryTarget } from "../hooks/useRecoveryTarget";

export function ProcessScenarioReadiness({ process, collections, roles }: { process: ProcessDefinition; collections: KnowledgeCollection[]; roles: AgatRole[] }) {
  const recovery = useRecoveryTarget();
  useRecoveryFocus(recovery.get("field") === "knowledge" ? "scenario-knowledge" : null);
  const [ids, setIds] = useState<string[]>(process.draftGraph.requiredKnowledgeCollectionIds ?? []);
  const [trigger, setTrigger] = useState<ScenarioTrigger>({ kind: "manual" });
  const [webhooks, setWebhooks] = useState<ProcessWebhook[]>([]);
  const [webhookError, setWebhookError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void api.processWebhooks(process.id).then((result) => { if (!cancelled) setWebhooks(result.webhooks); })
      .catch(() => { if (!cancelled) setWebhookError("Не удалось загрузить webhook; откройте вкладку «Триггеры»."); });
    return () => { cancelled = true; };
  }, [process.id]);
  const preflight = useScenarioPreflight("processes", process.id, { version: process.publishedVersion, knowledgeCollectionIds: ids, trigger }, true);
  return <div className="process-scenario-readiness">
    {process.hasUnpublishedChanges ? <p>Есть изменения черновика. Проверка относится к опубликованной версии; опубликуйте изменения для их проверки.</p> : null}
    <label className="field"><span>Проверяемый запуск</span><select value={trigger.kind === "webhook" ? `webhook:${trigger.webhookId}` : trigger.kind} onChange={(event) => {
      const value = event.target.value;
      setTrigger(value.startsWith("webhook:") ? { kind: "webhook", webhookId: value.slice(8) } : { kind: value as "manual" | "schedule" });
    }}><option value="manual">Вручную</option><option value="schedule">По расписанию</option>{webhooks.filter((item) => item.kind === "start").map((item) => <option key={item.id} value={`webhook:${item.id}`}>{item.name}</option>)}</select></label>
    {webhookError ? <p role="alert">{webhookError}</p> : null}
    <section id="scenario-knowledge" tabIndex={-1}><KnowledgeCollectionPicker collections={collections} selectedIds={ids} onToggle={(id) => setIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])} /></section>
    <ScenarioPreflightPanel {...preflight} roles={roles} onRefresh={preflight.refresh} />
  </div>;
}
