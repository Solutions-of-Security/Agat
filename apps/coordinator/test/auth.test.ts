import assert from "node:assert/strict";
import { createSign, generateKeyPairSync } from "node:crypto";
import { describe, it } from "node:test";

import { AuthenticationError, OidcVerifier, selectProject } from "../src/auth.js";
import { AgatStore } from "../src/database.js";

const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const publicJwk = { ...publicKey.export({ format: "jwk" }), kid: "test-key", use: "sig", alg: "RS256" };

function token(overrides: Record<string, unknown> = {}): string {
  const now = Math.floor(Date.now() / 1_000);
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", kid: "test-key" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({
    iss: "https://identity.example/realms/agat",
    sub: "user-1",
    preferred_username: "ada",
    email: "ada@example.com",
    exp: now + 300,
    nbf: now - 5,
    aud: ["agat-web"],
    azp: "agat-web",
    realm_access: { roles: ["viewer"] },
    resource_access: { "agat-web": { roles: ["operator"] } },
    agat_projects: ["default", "research"],
    ...overrides,
  })).toString("base64url");
  const signer = createSign("RSA-SHA256");
  signer.update(`${header}.${payload}`);
  signer.end();
  return `${header}.${payload}.${signer.sign(privateKey).toString("base64url")}`;
}

function verifier() {
  let requests = 0;
  return {
    requests: () => requests,
    verifier: new OidcVerifier({
      issuer: "https://identity.example/realms/agat",
      clientId: "agat-web",
      fetcher: async () => {
        requests += 1;
        return new Response(JSON.stringify({ keys: [publicJwk] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    }),
  };
}

describe("OIDC authentication", () => {
  it("verifies Keycloak RS256 claims, merges roles and caches JWKS", async () => {
    const fixture = verifier();
    const first = await fixture.verifier.verify(token());
    const second = await fixture.verifier.verify(token({ sub: "user-2" }));

    assert.equal(first.username, "ada");
    assert.deepEqual(new Set(first.roles), new Set(["viewer", "operator"]));
    assert.deepEqual(new Set(first.projectIds), new Set(["default", "research"]));
    assert.equal(second.subject, "user-2");
    assert.equal(fixture.requests(), 1);
    assert.equal(selectProject(first, "research"), "research");
    assert.throws(() => selectProject(first, "finance"), (error: unknown) =>
      error instanceof AuthenticationError && error.status === 403
    );
  });

  it("rejects expired, wrong-client and tampered tokens", async () => {
    const fixture = verifier();
    await assert.rejects(() => fixture.verifier.verify(token({ exp: 1 })), /истёк/);
    await assert.rejects(() => fixture.verifier.verify(token({ aud: "another", azp: "another" })), /другого client/);
    const [header, payload, signature] = token().split(".") as [string, string, string];
    const signatureBytes = Buffer.from(signature, "base64url");
    signatureBytes[0] = signatureBytes[0]! ^ 1;
    const corrupted = `${header}.${payload}.${signatureBytes.toString("base64url")}`;
    await assert.rejects(() => fixture.verifier.verify(corrupted), /Подпись/);
  });

  it("requires an AGAT role", async () => {
    const fixture = verifier();
    await assert.rejects(() => fixture.verifier.verify(token({
      realm_access: { roles: ["offline_access"] },
      resource_access: {},
    })), (error: unknown) => error instanceof AuthenticationError && error.status === 403);
  });
});

describe("project isolation", () => {
  it("keeps agents, processes, runs, credentials and events inside the active project", () => {
    const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "project-test" });
    try {
      store.createProject({ id: "research", name: "Research" });
      const customAgent = store.createAgent({
        name: "Research agent",
        role: "Only research",
        systemPrompt: "Work only with research data",
        model: null,
      }, "research");
      const credential = store.createCredential({
        name: "Research secret",
        type: "api_key",
        data: { apiKey: "secret" },
      }, "research");
      const run = store.createRun({
        name: "Research run",
        input: "Private research input",
        agentIds: [String(customAgent.id)],
        approvalRequired: false,
      }, "research");

      assert.equal(store.listAgents().some((agent) => agent.id === customAgent.id), false);
      assert.equal(store.listCredentials().some((item) => item.id === credential.id), false);
      assert.equal(store.getRun(run.id), null);
      assert.equal(store.getOverview().runs.some((item) => item.id === run.id), false);
      assert.equal(store.listEvents(0, 1_000).some((event) => event.runId === run.id), false);
      assert.equal(store.getRun(run.id, "research")?.name, "Research run");
      assert.equal(store.getOverview("research").runs.some((item) => item.id === run.id), true);
    } finally {
      store.close();
    }
  });
});
