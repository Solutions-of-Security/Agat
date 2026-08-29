import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Worker } from "@temporalio/worker";

interface ReplayFixture {
  workflowId: string;
  description: string;
  history: {
    events: unknown[];
  };
}

function validateFixture(value: unknown, filename: string): ReplayFixture {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${filename}: fixture должен быть JSON-объектом`);
  }
  const fixture = value as Partial<ReplayFixture>;
  if (typeof fixture.workflowId !== "string" || !fixture.workflowId.trim()) {
    throw new Error(`${filename}: workflowId обязателен`);
  }
  if (typeof fixture.description !== "string" || !fixture.description.trim()) {
    throw new Error(`${filename}: description обязателен`);
  }
  if (!fixture.history || !Array.isArray(fixture.history.events) || fixture.history.events.length < 4) {
    throw new Error(`${filename}: сохранённая history должна содержать минимум четыре события`);
  }
  return fixture as ReplayFixture;
}

async function main(): Promise<void> {
  const fixturesDirectory = fileURLToPath(new URL("../test/fixtures/", import.meta.url));
  const workflowBundle = fileURLToPath(new URL("./workflow-bundle.js", import.meta.url));
  const filenames = (await fs.readdir(fixturesDirectory))
    .filter((filename) => filename.endsWith(".json"))
    .sort();
  if (filenames.length === 0) throw new Error("Не найдено ни одной Temporal replay fixture");

  for (const filename of filenames) {
    const fixturePath = path.join(fixturesDirectory, filename);
    const fixture = validateFixture(JSON.parse(await fs.readFile(fixturePath, "utf8")), filename);
    await Worker.runReplayHistory(
      {
        workflowBundle: { codePath: workflowBundle },
        replayName: `agat-${path.basename(filename, ".json")}`,
      },
      fixture.history,
      fixture.workflowId,
    );
    console.log(`replay ok · ${filename} · ${fixture.description}`);
  }
}

await main();
