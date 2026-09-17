import type { CreateProcessRequest, ProcessTemplateCatalog } from "./types";

export type CatalogProcessTemplate = ProcessTemplateCatalog["templates"][number];

export function filterProcessTemplates(templates: CatalogProcessTemplate[], categoryId: string, query: string) {
  const terms = query.trim().toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
  return templates.filter((template) => {
    if (categoryId && template.categoryId !== categoryId) return false;
    const text = [template.name, template.description, template.outcome, template.id, ...template.inputs].join(" ").toLocaleLowerCase("ru");
    return terms.every((term) => text.includes(term));
  });
}

export function suggestedProcessName(name: string, existingNames: string[]): string {
  const names = new Set(existingNames.map((item) => item.trim().toLocaleLowerCase("ru")));
  const base = name.trim().slice(0, 100);
  if (!names.has(base.toLocaleLowerCase("ru"))) return base;
  for (let index = 2; ; index += 1) {
    const suffix = ` (${index})`;
    const candidate = `${base.slice(0, 100 - suffix.length)}${suffix}`;
    if (!names.has(candidate.toLocaleLowerCase("ru"))) return candidate;
  }
}

export function catalogProcessRequest(
  template: CatalogProcessTemplate,
  name: string,
  description: string,
  bindings: Record<string, string>,
  isTemplate: boolean,
): CreateProcessRequest {
  return {
    name, description, isTemplate,
    catalogTemplateId: template.id,
    catalogTemplateVersion: template.version,
    templateBindings: Object.fromEntries(template.roles
      .filter((role) => bindings[role.id])
      .map((role) => [role.id, bindings[role.id]!])),
  };
}
