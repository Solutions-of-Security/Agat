export type { ScenarioPreflight, ScenarioPreflightInput, ScenarioBlocker, ScenarioTrigger } from "../../coordinator/src/scenario-preflight";
import type { ScenarioPreflight } from "../../coordinator/src/scenario-preflight";

export function scenarioStartAllowed(result: ScenarioPreflight | null, mode: "queue" | "now"): boolean {
  return Boolean(result && (mode === "now" ? result.runnableNow : result.queueable));
}

export function recoveryTarget(hash: string): URLSearchParams {
  return new URLSearchParams(hash.split("?", 2)[1] ?? "");
}
