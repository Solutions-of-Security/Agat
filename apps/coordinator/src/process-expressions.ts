export interface ProcessExpressionContext {
  input: string;
  lastOutput: string | null;
  loopCounts?: Record<string, number>;
}

const EXPRESSION = /{{\s*([^{}]+?)\s*}}/g;
const SAFE_PATH = /^[\p{L}\p{N}_-]+(?:\.[\p{L}\p{N}_-]+)*$/u;

function readPath(value: unknown, path: string): unknown {
  let current = value;
  for (const segment of path.split(".")) {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    if (!Object.prototype.hasOwnProperty.call(current, segment)) return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function stringify(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function parsedLastOutput(lastOutput: string | null): unknown {
  if (!lastOutput) return null;
  try {
    return JSON.parse(lastOutput) as unknown;
  } catch {
    return null;
  }
}

export function renderProcessTemplate(template: string, context: ProcessExpressionContext): string {
  if (typeof template !== "string") throw new Error("Шаблон должен быть строкой");
  if (template.length > 100_000) throw new Error("Шаблон: максимум 100000 символов");
  const json = parsedLastOutput(context.lastOutput);
  const rendered = template.replace(EXPRESSION, (_match, rawExpression: string) => {
    const expression = rawExpression.trim();
    if (expression === "input") return context.input;
    if (expression === "lastOutput") return context.lastOutput ?? "";
    if (expression === "json") return stringify(json);
    if (expression.startsWith("json.")) {
      const path = expression.slice(5);
      if (!SAFE_PATH.test(path)) throw new Error(`Недопустимый путь выражения: ${expression}`);
      return stringify(readPath(json, path));
    }
    if (expression.startsWith("loop.")) {
      const path = expression.slice(5);
      if (!SAFE_PATH.test(path) || path.includes(".")) throw new Error(`Недопустимый идентификатор цикла: ${expression}`);
      return String(context.loopCounts?.[path] ?? 0);
    }
    throw new Error(`Неизвестное выражение: ${expression}`);
  });
  if (rendered.length > 200_000) throw new Error("Результат шаблона превышает 200000 символов");
  return rendered;
}
