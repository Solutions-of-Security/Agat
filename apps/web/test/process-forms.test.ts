import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import React, { createElement } from "react";
import { formDefinitionIssues, prepareProcessFormData } from "../src/processForms.ts";
import { ProcessFormFields } from "../src/components/ProcessFormFields.tsx";
import { processReadiness } from "../src/processReadiness.ts";
import type { ProcessApprovalForm, ProcessGraph } from "../src/types.ts";

Object.assign(globalThis, { React });

const form: ProcessApprovalForm = { title: "Проверка", description: "Дополните данные", fields: [
  { id: "count", label: "Количество", type: "number", required: true },
  { id: "decision", label: "Решение", type: "select", required: true, options: ["Доработать", "Готово"] },
  { id: "confirmed", label: "Подтверждение", type: "checkbox", required: true },
] };

test("form submission distinguishes empty/zero/false and checks required fields and options", () => {
  assert.deepEqual(prepareProcessFormData(form, { count: "0", decision: "Готово", confirmed: true }), {
    data: { count: 0, decision: "Готово", confirmed: true }, errors: {},
  });
  const invalid = prepareProcessFormData(form, { count: "", decision: "Other", confirmed: false });
  assert.deepEqual(Object.keys(invalid.errors), ["count", "decision", "confirmed"]);
  assert.deepEqual(invalid.data, {});
  assert.deepEqual(formDefinitionIssues(form), []);
  assert.ok(formDefinitionIssues({ ...form, fields: [...form.fields, form.fields[0]!] }).length);
});

test("form controls have linked labels, choices and inline errors", () => {
  const html = renderToStaticMarkup(createElement(ProcessFormFields, { form, values: {}, errors: { count: "Введите число" }, onChange: () => {} }));
  assert.match(html, /<legend>Проверка<\/legend>/);
  assert.match(html, /type="number"/);
  assert.match(html, /aria-invalid="true"/);
  assert.match(html, /role="alert"/);
  assert.match(html, /<label for="/);
  assert.match(html, /<option value="Готово">Готово<\/option>/);
});

test("editor readiness points to missing loop connections, invalid limit and incomplete forms", () => {
  const graph: ProcessGraph = { nodes: [
    { id: "start", name: "Старт", type: "start", position: { x: 0, y: 0 }, config: {} },
    { id: "loop", name: "Цикл", type: "loop", position: { x: 200, y: 0 }, config: { maxIterations: 0, condition: { source: "last_output", operator: "always", value: "", caseSensitive: false } } },
    { id: "end", name: "Конец", type: "end", position: { x: 400, y: 0 }, config: {} },
  ], edges: [{ id: "s-l", source: "start", target: "loop", branch: "default" }, { id: "l-e", source: "loop", target: "end", branch: "exit" }] };
  assert.deepEqual(processReadiness(graph, []).map((issue) => issue.id), ["loop:branches", "loop:limit"]);
  graph.nodes[1] = { ...graph.nodes[1]!, type: "approval", config: { approvalMode: "input" } };
  assert.ok(processReadiness(graph, []).some((issue) => issue.id === "loop:form"));
});
