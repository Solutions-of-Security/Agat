import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runDecisionShadowSmoke } from "../../../scripts/lib/decision-shadow-smoke.js";

test("real Python worker carries a pinned decision through lease, loopback inference and coordinator HTTP", { timeout: 60_000 }, async () => {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const child = spawn("python3", ["-u", "-c", `
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.server import make_server
class Backend:
    identity = {"repository": "test-only", "revision": "fixture", "artifactSha256": "1"*64,
                "tokenizerSha256": "2"*64, "implementationSha256": "3"*64,
                "promptVersion": "test-v1", "backend": "fixture", "quantization": "none"}
    def score(self, request): return Scores([8.0, 0.0], 20)
with make_server(DecisionEngine(Backend()), 0) as server:
    print(server.server_port, flush=True)
    server.serve_forever()
`], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  const lines = createInterface({ input: child.stdout! });
  try {
    const port = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture runtime startup timeout")), 5000);
      lines.once("line", (line) => { clearTimeout(timer); resolve(line); });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
    });
    assert.match(port, /^\d+$/);
    const result = await runDecisionShadowSmoke(`http://127.0.0.1:${port}`, {
      state: "Приватные исходные данные", question: "Подтверждено?", kind: "boolean",
      options: [{ id: "no", description: "Нет", value: false }, { id: "yes", description: "Да", value: true }],
    });
    assert.equal(result.status, "integration_pass");
    assert.equal(result.observation.result.value, false);
    assert.equal(result.primaryRoute, "PRIMARY_BRANCH");
    const score = await runDecisionShadowSmoke(`http://127.0.0.1:${port}`, {
      state: "Доступны исходный документ и проверяемые результаты", question: "Оцените полноту по числовой шкале", kind: "score",
      options: [{ id: "low", description: "Неполные данные", value: -0.25 },
        { id: "high", description: "Полные данные", value: 1e-7 }],
    });
    assert.equal(score.status, "integration_pass");
    assert.equal(score.observation.result.inputFingerprintVersion, "binary64-v1");
    assert.ok(score.observation.result.value > -0.25 && score.observation.result.value < 1e-7);
    assert.equal(score.primaryRoute, "PRIMARY_BRANCH");
  } finally {
    lines.close(); child.kill("SIGTERM");
    await new Promise<void>((resolve) => child.exitCode !== null || child.signalCode !== null ? resolve() : child.once("exit", () => resolve()));
  }
});
