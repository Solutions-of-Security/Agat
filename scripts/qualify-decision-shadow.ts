import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { runDecisionShadowSmoke } from "./lib/decision-shadow-smoke.js";

const { values } = parseArgs({ options: { url: { type: "string" }, request: { type: "string" }, output: { type: "string" },
  "expect-inference-timeout": { type: "boolean", default: false },
  "expect-worker-timeout": { type: "boolean", default: false } } });
if (!values.url || !values.request || !values.output) {
  throw new Error("Required: --url http://127.0.0.1:<port> --request <request.json> --output <new-evidence.json>");
}
if (fs.existsSync(values.output)) throw new Error("Evidence already exists; choose a new output path");
if (values["expect-inference-timeout"] && values["expect-worker-timeout"]) throw new Error("Choose one expected timeout origin");
const result = await runDecisionShadowSmoke(values.url, JSON.parse(fs.readFileSync(values.request, "utf8")),
  values["expect-worker-timeout"] ? "worker_timeout" : values["expect-inference-timeout"]);
fs.mkdirSync(path.dirname(values.output), { recursive: true });
fs.writeFileSync(values.output, JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
console.log(JSON.stringify({ output: values.output, status: result.status, qualification: result.qualification,
  shadowStatus: result.observation.status, primaryRoute: result.primaryRoute }));
