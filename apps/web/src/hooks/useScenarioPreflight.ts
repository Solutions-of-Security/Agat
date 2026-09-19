import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ScenarioPreflight, ScenarioPreflightInput } from "../scenarioPreflight";

export function useScenarioPreflight(kind: "processes" | "process-templates", id: string | null, input: ScenarioPreflightInput & { catalogTemplateVersion?: number; templateBindings?: Record<string, string> }, enabled: boolean) {
  const [attempt, setAttempt] = useState(0);
  const [response, setResponse] = useState<{ key: string; result: ScenarioPreflight | null; error: string | null } | null>(null);
  const key = JSON.stringify([kind, id, input, enabled, attempt]);
  useEffect(() => {
    setResponse(null);
    if (!enabled || !id) return;
    const controller = new AbortController();
    const [, requestId, payload] = JSON.parse(key) as [string, string, typeof input];
    void api.scenarioPreflight(kind, requestId, payload, controller.signal).then((result) => {
      if (!controller.signal.aborted) setResponse({ key, result, error: null });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResponse({ key, result: null, error: error instanceof Error ? error.message : "Проверка готовности недоступна" });
    });
    return () => controller.abort();
  }, [key]);
  // Results for an earlier selection must never enable Start, even for one render.
  const current = response?.key === key ? response : null;
  return { result: current?.result ?? null, error: current?.error ?? null, refresh: () => setAttempt((value) => value + 1) };
}
