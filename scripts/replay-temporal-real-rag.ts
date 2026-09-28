import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "@temporalio/worker";

const root = fileURLToPath(new URL("../", import.meta.url));
const directory = path.resolve(process.argv[2] ?? "");
assert.equal(process.argv.length, 3, "Pass one evidence directory");
assert.ok(directory.startsWith(path.join(root, "docs") + path.sep));
const sha = (value: Buffer) => createHash("sha256").update(value).digest("hex");
const planBytes = fs.readFileSync(path.join(directory, "plan.json"));
const plan = JSON.parse(planBytes.toString());
assert.ok(["agat.temporal.real-rag-plan.v1", "agat.temporal.real-rag-plan.v2", "agat.temporal.real-rag-plan.v3"].includes(plan.schema));
const launcher = JSON.parse(fs.readFileSync(path.join(directory, "launcher.json"), "utf8"));
assert.equal(launcher.status, "pass"); assert.equal(launcher.planSha256, sha(planBytes));
const workflowBundle = { codePath: path.join(root, "apps/temporal-worker/dist/workflow-bundle.js") };
// Require the exact measured bundle for this independent local replay. The
// general historical compatibility suite remains apps/temporal-worker/replay.
const bundleSha = sha(fs.readFileSync(workflowBundle.codePath));
const results = [];
for (const transport of ["isolated", "session"]) {
  const bytes = fs.readFileSync(path.join(directory, `${transport}.json`));
  assert.equal(sha(bytes), launcher.phaseSha256[`${transport}.json`]);
  const phase = JSON.parse(bytes.toString());
  assert.equal(phase.status, "pass"); assert.equal(phase.planSha256, sha(planBytes));
  assert.equal(phase.workflowBundleSha256, bundleSha);
  await Worker.runReplayHistory({ workflowBundle, replayName: `real-rag-${transport}` }, phase.history, phase.workflowId);
  results.push({ transport, workflowId: phase.workflowId, historyEvents: phase.history.events.length, status: "pass" });
}
console.log(JSON.stringify({ schema: "agat.temporal.real-rag-native-replay.v1", planSha256: sha(planBytes),
  workflowBundleSha256: bundleSha, status: "pass", phases: results }));
