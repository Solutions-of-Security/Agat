import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  KubernetesLocalWorkerLauncher,
  type KubernetesTransport,
  WorkerLauncherError,
  buildWorkerDeployment,
} from "../src/local-workers.js";

type JsonObject = Record<string, unknown>;

const options = {
  namespace: "agat",
  workerImage: "agat-local/worker:test",
  workerConfigMap: "agat-worker-config",
  workerSecret: "agat-secrets",
  modelBaseUrl: "http://host.docker.internal:11434/v1",
  modelDiscoveryUrl: "",
  embeddingModels: "embeddinggemma",
  defaultWebEnabled: true,
  maxWorkersPerLaunch: 8,
};

function record(value: unknown): JsonObject {
  assert.ok(value && typeof value === "object");
  return value as JsonObject;
}

class FakeKubernetesTransport implements KubernetesTransport {
  readonly deployments = new Map<string, JsonObject>();

  async request<T>(method: string, path: string, body?: JsonObject): Promise<T> {
    if (method === "POST") {
      const deployment = structuredClone(body ?? {});
      const metadata = record(deployment.metadata);
      const name = String(metadata.name);
      deployment.status = { readyReplicas: 1, availableReplicas: 1 };
      this.deployments.set(name, deployment);
      return structuredClone(deployment) as T;
    }
    if (method === "GET") {
      const url = new URL(path, "https://kubernetes.invalid");
      const selector = url.searchParams.get("labelSelector") ?? "";
      const requirements = selector.split(",").filter(Boolean).map((item) => item.split("="));
      const items = [...this.deployments.values()].filter((deployment) => {
        const labels = record(record(deployment.metadata).labels);
        return requirements.every(([key, value]) => labels[key ?? ""] === value);
      });
      return { items: structuredClone(items) } as T;
    }
    const name = decodeURIComponent(path.split("/").pop() ?? "");
    if (method === "PATCH") {
      const deployment = this.deployments.get(name);
      if (!deployment) throw new Error("not found");
      const replicas = Number(record(record(body).spec).replicas);
      record(deployment.spec).replicas = replicas;
      deployment.status = replicas === 1 ? { readyReplicas: 1, availableReplicas: 1 } : {};
      return structuredClone(deployment) as T;
    }
    if (method === "DELETE") {
      this.deployments.delete(name);
      return {} as T;
    }
    throw new Error(`unexpected ${method}`);
  }
}

describe("local worker launcher", () => {
  it("builds an isolated, non-root worker deployment with a fixed model", () => {
    const deployment = buildWorkerDeployment(
      options,
      { name: "Qwen workers", model: "qwen3:8b", workers: 2, concurrency: 1, webEnabled: true },
      "abcdef123456",
      "2026-08-13T10:00:00.000Z",
      0,
    );
    const spec = record(deployment.spec);
    const template = record(spec.template);
    const podSpec = record(template.spec);
    const container = (podSpec.containers as JsonObject[])[0]!;
    const env = container.env as Array<JsonObject>;

    assert.equal(record(deployment.metadata).name, "agat-local-qwen-workers-abcdef-01");
    assert.equal(podSpec.automountServiceAccountToken, false);
    assert.equal(record(podSpec.securityContext).runAsNonRoot, true);
    assert.equal(record(container.securityContext).readOnlyRootFilesystem, true);
    assert.equal((podSpec.volumes as JsonObject[]).some((volume) => volume.name === "state" && "emptyDir" in volume), true);
    assert.equal(env.find((item) => item.name === "AGAT_WORKER_MODELS")?.value, "qwen3:8b");
    assert.equal(env.find((item) => item.name === "AGAT_EMBEDDING_MODELS")?.value, "embeddinggemma");
    assert.equal(env.find((item) => item.name === "AGAT_WORKER_NAME")?.value, "agat-local-qwen-workers-abcdef-01");
    assert.equal(env.find((item) => item.name === "AGAT_WORKER_LABELS")?.value,
      "runtime=kubernetes,cluster=docker-desktop,managed=local-launcher,pool=abcdef123456");
  });

  it("launches several independent workers and stops or starts the whole pool", async () => {
    const transport = new FakeKubernetesTransport();
    const launcher = new KubernetesLocalWorkerLauncher(options, transport);

    const launched = await launcher.launch({
      name: "Llama pool",
      model: "llama3.2:latest",
      workers: 3,
      concurrency: 1,
      webEnabled: false,
    });
    assert.equal(launched.workers, 3);
    assert.equal(launched.readyWorkers, 3);
    assert.equal(launched.status, "ready");
    assert.equal(new Set(launched.workerNames).size, 3);

    const stopped = await launcher.stopPool(launched.id);
    assert.equal(stopped.desiredWorkers, 0);
    assert.equal(stopped.readyWorkers, 0);
    assert.equal(stopped.status, "stopped");

    const started = await launcher.startPool(launched.id);
    assert.equal(started.desiredWorkers, 3);
    assert.equal(started.readyWorkers, 3);
    assert.equal(started.status, "ready");

    const deleted = await launcher.deletePool(launched.id);
    assert.equal(deleted.id, launched.id);
    assert.equal((await launcher.snapshot()).pools.length, 0);
  });

  it("rejects unsafe model names and excessive local worker counts", async () => {
    const launcher = new KubernetesLocalWorkerLauncher(options, new FakeKubernetesTransport());
    await assert.rejects(
      launcher.launch({ model: "model\nAGAT_ADMIN_TOKEN=x", workers: 1 }),
      (error: unknown) => error instanceof WorkerLauncherError && error.status === 400,
    );
    await assert.rejects(
      launcher.launch({ model: "qwen3:8b", workers: 9 }),
      /Количество workers должно быть от 1 до 8/,
    );
  });
});
