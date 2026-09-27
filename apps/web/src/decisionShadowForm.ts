import type { DecisionShadowConfig } from "./types";

export type DecisionKind = DecisionShadowConfig["kind"];
export interface DecisionOptionDraft { key: number; id: string; description: string; abstain: boolean; value: string }
export interface DecisionShadowDraft {
  kind: DecisionKind; question: string; profileJson: string; timeout: string; options: DecisionOptionDraft[];
}
export const decisionKindLabels: Record<DecisionKind, string> = { choice: "Выбор варианта", boolean: "Да или нет", score: "Числовая оценка" };

export function defaultDecisionOptions(kind: DecisionKind): DecisionOptionDraft[] {
  return [0, 1].map(index => ({ key: index, id: kind === "boolean" ? (index ? "yes" : "no") : `option_${index + 1}`,
    description: kind === "boolean" ? (index ? "Да" : "Нет") : "", abstain: false,
    value: kind === "boolean" ? String(Boolean(index)) : String(index) }));
}

export function decisionShadowDraft(config?: DecisionShadowConfig): DecisionShadowDraft {
  return config ? { kind: config.kind, question: config.question, profileJson: config.profileJson, timeout: String(config.timeoutMs),
    options: config.options.map((option, key) => ({ ...option, key, value: option.value === undefined ? "" : String(option.value) })) }
    : { kind: "boolean", question: "", profileJson: "", timeout: "1000", options: defaultDecisionOptions("boolean") };
}

function validText(text: string, max: number) {
  return Boolean(text.trim()) && [...text].length <= max
    && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text);
}

// Validate form input; the coordinator remains authoritative for model/policy
// pinning and the complete profile contract. Never reserialize profileJson.
export function decisionShadowFormResult(draft: DecisionShadowDraft): { config?: DecisionShadowConfig; issues: string[] } {
  const issues: string[] = [];
  if (!validText(draft.question, 2000)) issues.push("Введите вопрос: от 1 до 2000 символов.");
  let profile: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = JSON.parse(draft.profileJson);
    if (!validText(draft.profileJson, 16_000) || !parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    profile = parsed as Record<string, unknown>;
    if (profile.schemaVersion !== "agat.decision.v1" || typeof profile.runtimeVersion !== "string"
      || !profile.model || !profile.policy || !profile.calibration) throw new Error();
  } catch { issues.push("Вставьте полную строку profileJson из health локальной модели (до 16 000 символов)."); }
  if (draft.kind === "score" && profile && (!Array.isArray(profile.inputFingerprintVersions)
      || !profile.inputFingerprintVersions.includes("binary64-v1"))) issues.push("Для числовой оценки нужен профиль с поддержкой binary64-v1.");
  const timeoutMs = Number(draft.timeout);
  if (!draft.timeout.trim() || !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 10_000) issues.push("Время ожидания должно быть целым числом от 100 до 10 000 мс.");
  if (draft.options.length < 2 || draft.options.length > 10 || (draft.kind === "boolean" && draft.options.length !== 2)) issues.push("Нужно от 2 до 10 вариантов; для «Да или нет» — ровно два.");
  const options: DecisionShadowConfig["options"] = draft.options.map((option, index) => {
    if (!validText(option.id, 80) || !/^[A-Za-z0-9_.:-]+$/.test(option.id)) issues.push(`Вариант ${index + 1}: укажите код до 80 символов из латиницы, цифр и _ . : -.`);
    if (!validText(option.description, 1000)) issues.push(`Вариант ${index + 1}: введите описание до 1000 символов.`);
    if (draft.kind === "boolean" && !["true", "false"].includes(option.value)) issues.push(`Вариант ${index + 1}: выберите «Да» или «Нет».`);
    const numeric = Number(option.value);
    if (draft.kind === "score" && (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(option.value.trim())
        || !Number.isFinite(numeric) || Math.abs(numeric) > 1_000_000)) issues.push(`Вариант ${index + 1}: нужно конечное число от −1 000 000 до 1 000 000.`);
    return { id: option.id, description: option.description, abstain: draft.kind === "choice" && option.abstain,
      ...(draft.kind === "boolean" ? { value: option.value === "true" } : draft.kind === "score" ? { value: numeric === 0 ? 0 : numeric } : {}) };
  });
  if (new Set(options.map(option => option.id)).size !== options.length) issues.push("Коды вариантов должны различаться.");
  if (new Set(options.map(option => option.description)).size !== options.length) issues.push("Описания вариантов должны различаться.");
  if (draft.kind !== "choice" && new Set(options.map(option => option.value)).size !== options.length) issues.push("Значения вариантов должны различаться.");
  return issues.length ? { issues } : { issues, config: { mode: "shadow", kind: draft.kind, question: draft.question,
    profileJson: draft.profileJson, timeoutMs, options } };
}
