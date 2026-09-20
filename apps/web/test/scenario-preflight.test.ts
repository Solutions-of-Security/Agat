import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ScenarioPreflightPanel } from "../src/components/ScenarioPreflightPanel.tsx";
import { scenarioStartAllowed, recoveryTarget, type ScenarioPreflight } from "../src/scenarioPreflight.ts";
import { parseAppRoute } from "../src/navigation.ts";

const ready: ScenarioPreflight = { checkedAt: "2026-09-18T10:00:00Z", processId: "process/one", version: 2, saved: true, queueable: true,
  runnableNow: true, scenarioVerified: false, verification: null, fingerprint: "test", blockers: [], notices: [] };
const render = (result: ScenarioPreflight | null, error: string | null = null) => renderToStaticMarkup(createElement(ScenarioPreflightPanel, { result, error, onRefresh() {} }));

test("start stays disabled for loading/failure and requires deliberate queue mode when only queueable", () => {
  assert.equal(scenarioStartAllowed(null, "now"), false);
  assert.equal(scenarioStartAllowed(null, "queue"), false);
  const queueable = { ...ready, runnableNow: false };
  assert.equal(scenarioStartAllowed(queueable, "now"), false);
  assert.equal(scenarioStartAllowed(queueable, "queue"), true);
  assert.equal(scenarioStartAllowed({ ...queueable, queueable: false }, "queue"), false);
  assert.equal(scenarioStartAllowed(ready, "now"), true);
});

test("panel renders all four independent states without claiming an untested scenario is verified", () => {
  const html = render(ready);
  for (const label of ["Сохранено", "Можно в очередь", "Можно выполнить сейчас", "Проверено на сценарии"]) assert.ok(html.includes(label));
  assert.match(html, /data-ready="false"><strong>Проверено на сценарии/);
  assert.match(html, /Сценарий ещё не проверен/);
  assert.match(html, /закреплённая версия v2/);
  const verified = render({ ...ready, scenarioVerified: true, runnableNow: false, verification: { instanceId: "instance", runId: "run", completedAt: ready.checkedAt, href: "#runs/run" } });
  assert.match(verified, /data-ready="false"><strong>Можно выполнить сейчас/);
  assert.match(verified, /data-ready="true"><strong>Проверено на сценарии/);
  assert.match(verified, /href="#runs\/run"/);
});

test("each blocker shows its reason, queue impact, and exact navigable recovery link", () => {
  const href = "#processes?processId=process%2Fone&tab=schema&nodeId=review%20step";
  const result: ScenarioPreflight = { ...ready, queueable: false, runnableNow: false, blockers: [
    { code: "agent", category: "team", blocks: "queue", message: "Назначьте агента этапу review step", recovery: { label: "Назначить агента", href } },
    { code: "embeddings", category: "embeddings", blocks: "run", message: "Не готова коллекция Evidence", recovery: { label: "Индексировать Evidence", href: "#knowledge?collectionId=evidence" } },
  ] };
  const html = render(result);
  assert.match(html, /Назначьте агента этапу review step/);
  assert.match(html, /Блокирует постановку в очередь/);
  assert.match(html, /можно ожидать в очереди/);
  assert.match(html, /href="#knowledge\?collectionId=evidence"/);
  assert.equal(parseAppRoute(href).view, "processes");
  assert.equal(recoveryTarget(href).get("processId"), "process/one");
  assert.equal(recoveryTarget(href).get("nodeId"), "review step");
  for (const view of ["agents", "models", "nodes", "tools", "knowledge"]) assert.equal(parseAppRoute(`#${view}?target=id`).view, view);
});

test("loading and failed preflights expose accessible status and retry without stale success", () => {
  assert.match(render(null), /aria-busy="true"/);
  assert.match(render(null), /role="status"/);
  const failed = render(null, "Сервер недоступен");
  assert.match(failed, /role="alert"/);
  assert.match(failed, /Проверить снова/);
  assert.doesNotMatch(failed, /Блокирующих причин сейчас нет/);
});

test("a designer gets a precise admin recovery link instead of navigation to a forbidden infrastructure page", () => {
  const result: ScenarioPreflight = { ...ready, runnableNow: false, blockers: [{ code: "runtime", category: "runtime", blocks: "run", message: "Нет worker", recovery: { label: "Подключить worker", href: "#nodes?runtime=langgraph" } }] };
  const html = renderToStaticMarkup(createElement(ScenarioPreflightPanel, { result, error: null, onRefresh() {}, roles: ["designer"] }));
  assert.match(html, /Нужен администратор/);
  assert.match(html, /data-recovery-href="#nodes\?runtime=langgraph"/);
  assert.doesNotMatch(html, /<a href="#nodes/);
});
