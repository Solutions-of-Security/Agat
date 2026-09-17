import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { getProcessTemplateCatalog } from "../apps/coordinator/src/process-catalog.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const catalog = getProcessTemplateCatalog();
const lines = [
  "# Каталог процессов с LLM-агентами",
  "",
  "Сгенерировано из `apps/coordinator/src/process-catalog.ts`. Не редактировать вручную: `npm run docs:processes`.",
  "",
  `Версия каталога: ${catalog.version}. Категорий: ${catalog.categories.length}. Шаблонов: ${catalog.templates.length}.`,
  "",
  "Шаблоны создают **черновики подготовки и согласования материала**. Назначение агента не подтверждает его квалификацию или наличие источников. Приёмка бизнес-результата и готовность интеграции проверяются отдельно. Общие требования: [requirements.md](./requirements.md); анализ: [analysis.md](./analysis.md); создание: [README.md](./README.md).",
  "",
];
for (const category of catalog.categories) {
  lines.push(`## ${category.id} — ${category.name}`, "", category.description, "", `**Правило отнесения:** ${category.inclusion}`, "",
    "**Требования категории:**", "", ...category.requirements.map((item) => `- ${item}`), "",
    `**Метрики:** ${category.metrics.join("; ")}.`, "");
  for (const template of catalog.templates.filter((item) => item.categoryId === category.id)) {
    lines.push(
      `### ${template.id} — ${template.name}`, "", `Версия ${template.version} · ${template.priority} · ${template.roadmapId}.`, "",
      template.description, "", `**Ответственный:** ${template.ownerRole}. Конкретный сотрудник назначается при адаптации.`, "",
      `**Триггер:** ${template.trigger}.`, "", "**Вход:**", "", ...template.inputs.map((item) => `- ${item}`), "",
      `**Результат:** ${template.outcome}`, "", `**Граница шаблона:** ${template.boundary}`, "",
      "**Участники:**", "", ...template.roles.map((role) => `- ${role.id} — ${role.name}: ${role.responsibility}`), "",
      "**Этапы:**", "", ...template.stages.map((stage, index) => `${index + 1}. **${stage.name}** (роль ${stage.roleId}). ${stage.instruction}`),
      `${template.stages.length + 1}. Решение ответственного: принять сохранение материала или отклонить.`,
      `${template.stages.length + 2}. Сохранение Markdown-артефакта и завершение.`, "",
      "**Требования процесса:**", "", ...template.requirements.map((item) => `- ${item}`), "",
      "**Приёмка:**", "", ...template.acceptance.map((item, index) => `- ${template.id}/AC-${index + 1}: ${item}`), "",
      "**Исключения:**", "", ...template.exceptions.map((item) => `- ${item}`), "",
      "**Пример входа** (учебный, многоточия заполняет владелец; это не evidence выполненного процесса):", "", "```text", template.inputExample, "```", "",
    );
  }
}
const outputs = new Map([
  ["docs/llm-processes/catalog.json", `${JSON.stringify(catalog, null, 2)}\n`],
  ["docs/llm-processes/catalog.md", `${lines.join("\n").trimEnd()}\n`],
]);
const check = process.argv.includes("--check");
for (const [relativePath, content] of outputs) {
  const target = path.join(root, relativePath);
  if (check) {
    if (!fs.existsSync(target) || fs.readFileSync(target, "utf8") !== content) {
      throw new Error(`${relativePath} устарел. Выполните npm run docs:processes`);
    }
  } else {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }
}
console.log(`${check ? "Проверено" : "Сформировано"}: ${catalog.categories.length} категорий, ${catalog.templates.length} шаблонов; catalog.md, catalog.json.`);
