import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { KubernetesTransport } from "../src/local-workers.js";
import {
  KubernetesSandboxExecutor,
  buildSandboxJob,
  buildSandboxNetworkPolicy,
  buildSandboxSecret,
  isPublicSandboxIp,
  normalizeSandboxProfile,
  type SandboxExecution,
  type SandboxOptions,
} from "../src/sandbox.js";

type JsonObject = Record<string, unknown>;

const moduleBase64 = Buffer.from([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]).toString("base64");

function record(value: unknown): JsonObject {
  assert.ok(value && typeof value === "object" && !Array.isArray(value));
  return value as JsonObject;
}

const options: SandboxOptions = {
  enabled: true,
  namespace: "agat",
  wasiImage: "agat-local/sandbox-wasi:1.5.0",
  runtimeClass: "gvisor",
  networkPolicyEnforced: true,
};

function execution(transport: "wasi" | "container" = "wasi"): SandboxExecution {
  const profile = normalizeSandboxProfile(transport, transport === "wasi" ? {
    tool: {
      name: "summarize",
      inputSchema: { type: "object", properties: { text: { type: "string" } } },
      annotations: { readOnlyHint: true },
    },
    moduleBase64,
  } : {
    tool: {
      name: "render",
      inputSchema: { type: "object", properties: {} },
      annotations: { destructiveHint: true },
    },
    image: `registry.example.test/tool@sha256:${"a".repeat(64)}`,
    command: ["/opt/tool", "run"],
    egress: [{ ip: "93.184.216.34", port: 443 }],
  });
  return {
    callId: "12345678-1234-1234-1234-123456789abc",
    transport,
    profile,
    arguments: { text: "private input" },
    credential: { type: "api_key", data: { apiKey: "scoped-secret" } },
    maxResponseBytes: 1_000_000,
    assertAllowed: () => undefined,
  };
}

describe("isolated MCP tool sandbox", () => {
  it("validates immutable artifacts and exact public-IP egress", () => {
    assert.equal(isPublicSandboxIp("93.184.216.34"), true);
    assert.equal(isPublicSandboxIp("127.0.0.1"), false);
    assert.equal(isPublicSandboxIp("10.1.2.3"), false);
    assert.equal(isPublicSandboxIp("::ffff:127.0.0.1"), false);
    assert.equal(isPublicSandboxIp("0:0:0:0:0:0:0:1"), false);
    assert.equal(isPublicSandboxIp("::ffff:7f00:1"), false);
    assert.equal(isPublicSandboxIp("2002:a00::1"), false);
    assert.equal(isPublicSandboxIp("4000::1"), false);
    assert.equal(isPublicSandboxIp("2606:4700:4700::1111"), true);
    assert.throws(() => normalizeSandboxProfile("container", {
      tool: { name: "unsafe", inputSchema: { type: "object" } },
      image: "registry.example.test/tool:latest",
      command: ["/tool"],
    }), /@sha256 digest/);
    assert.throws(() => normalizeSandboxProfile("container", {
      tool: { name: "unsafe", inputSchema: { type: "object" } },
      image: `registry.example.test/tool@sha256:${"b".repeat(64)}`,
      command: ["/tool"],
      egress: [{ ip: "169.254.169.254", port: 80 }],
    }), /публичным exact IP/);
    assert.throws(() => normalizeSandboxProfile("container", {
      tool: { name: "unsafe", inputSchema: { type: "object" } },
      image: `registry.example.test/tool@sha256:${"b".repeat(64)}`,
      command: ["/tool"],
      egress: [{ ip: "93.184.216.34", port: -1 }],
    }), /целым числом/);
    assert.throws(() => normalizeSandboxProfile("wasi", {
      tool: { name: "unsafe", inputSchema: { type: "object" } },
      moduleBase64,
      egress: [{ ip: "93.184.216.34", port: 443 }],
    }), /не поддерживает сетевой egress/);
  });

  it("builds a one-shot non-root Job, immutable invocation Secret and default-deny policy", () => {
    const input = execution("container");
    const secret = buildSandboxSecret(input, "agat");
    const job = buildSandboxJob(input, options);
    const policy = buildSandboxNetworkPolicy(input, "agat");
    const secretJson = JSON.stringify(secret);
    assert.equal(secretJson.includes("scoped-secret"), false);
    assert.equal(secretJson.includes("private input"), false);
    assert.equal(secret.immutable, true);

    const podSpec = record(record(record(record(job.spec).template).spec));
    const container = (podSpec.containers as JsonObject[])[0]!;
    assert.equal(podSpec.automountServiceAccountToken, false);
    assert.equal(podSpec.runtimeClassName, "gvisor");
    assert.equal(record(podSpec.securityContext).runAsNonRoot, true);
    assert.equal(record(container.securityContext).readOnlyRootFilesystem, true);
    assert.deepEqual(record(record(container.securityContext).capabilities).drop, ["ALL"]);
    assert.equal(record(job.spec).backoffLimit, 0);
    assert.equal(record(record((podSpec.volumes as JsonObject[])[0]).secret).defaultMode, 0o440);

    const policySpec = record(policy.spec);
    assert.deepEqual(policySpec.ingress, []);
    assert.deepEqual(policySpec.policyTypes, ["Ingress", "Egress"]);
    assert.deepEqual(policySpec.egress, [{
      to: [{ ipBlock: { cidr: "93.184.216.34/32" } }],
      ports: [{ protocol: "TCP", port: 443 }],
    }]);
  });

  it("executes through Kubernetes API, rechecks the kill switch and cleans every ephemeral object", async () => {
    const requests: Array<{ method: string; path: string; body?: JsonObject }> = [];
    let checks = 0;
    const input = execution("wasi");
    input.assertAllowed = () => { checks += 1; };
    const transport: KubernetesTransport = {
      async request<T>(method: string, path: string, body?: JsonObject): Promise<T> {
        requests.push({ method, path, body });
        if (method === "GET" && path.includes("/jobs/")) return { status: { succeeded: 1 } } as T;
        if (method === "GET" && path.includes("/pods?") ) {
          return { items: [{ metadata: { name: "agat-tool-pod" } }] } as T;
        }
        if (method === "GET" && path.includes("/log?")) return { ok: true } as T;
        return {} as T;
      },
    };
    const executor = new KubernetesSandboxExecutor(options, transport, async () => undefined);
    assert.deepEqual(await executor.execute(input), { ok: true });
    assert.ok(checks >= 3);
    assert.equal(requests.filter((entry) => entry.method === "POST").length, 3);
    assert.equal(requests.filter((entry) => entry.method === "DELETE").length, 3);
    const jobDelete = requests.find((entry) => entry.method === "DELETE" && entry.path.includes("/jobs/"));
    assert.equal(record(jobDelete?.body).propagationPolicy, "Foreground");
  });

  it("retains the network boundary until an emergency-aborted Pod is confirmed gone", async () => {
    const requests: Array<{ method: string; path: string; body?: JsonObject }> = [];
    let checks = 0;
    const input = execution("wasi");
    input.assertAllowed = () => {
      checks += 1;
      if (checks >= 3) throw new Error("emergency deny");
    };
    const transport: KubernetesTransport = {
      async request<T>(method: string, path: string, body?: JsonObject): Promise<T> {
        requests.push({ method, path, body });
        if (method === "GET" && path.includes("/pods?")) {
          return { items: [{ metadata: { name: "still-terminating" } }] } as T;
        }
        return {} as T;
      },
    };
    const executor = new KubernetesSandboxExecutor(options, transport, async () => undefined);
    await assert.rejects(executor.execute(input), /emergency deny/);
    assert.ok(requests.some((entry) => entry.method === "DELETE" && entry.path.includes("/jobs/")));
    assert.ok(requests.some((entry) => entry.method === "DELETE" && entry.path.includes("/secrets/")));
    assert.equal(requests.some((entry) => entry.method === "DELETE" && entry.path.includes("/networkpolicies/")), false);
  });

  it("fails closed for native containers without confirmed NetworkPolicy enforcement", async () => {
    const transport: KubernetesTransport = { async request<T>(): Promise<T> { throw new Error("must not run"); } };
    const executor = new KubernetesSandboxExecutor({ ...options, networkPolicyEnforced: false }, transport);
    await assert.rejects(executor.execute(execution("container")), /NetworkPolicy не подтверждён/);
  });
});
