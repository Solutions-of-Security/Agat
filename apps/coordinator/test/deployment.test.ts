import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig, type CoordinatorConfig } from "../src/config.js";
import { deploymentPreflight, deploymentSnapshot, parseAuthenticationMode, parseDeploymentProfile } from "../src/deployment.js";
import { authenticateLegacyRequest } from "../src/auth.js";

const config = (overrides: Partial<CoordinatorConfig> = {}): CoordinatorConfig => ({ ...loadConfig(), deploymentProfile: undefined,
  authMode: undefined, oidcEnabled: false, oidcIssuer: "", temporalEnabled: false, stateStoreDriver: "sqlite", mcpEnabled: true, ...overrides });
const database = { runtime: { mode: "database" as const, connected: true }, sandbox: null };

test("single-user stays explicit and reports missing infrastructure without inventing future capabilities", () => {
  const result = deploymentSnapshot(config(), database);
  assert.equal(result.profile, "single-user");
  assert.equal(result.ready, true);
  assert.equal(result.authentication.individualSubjects, false);
  assert.match(result.authentication.label, /local-admin/);
  assert.equal(deploymentPreflight(result, ["runs", "processes"]).allowed, true);
  assert.equal(deploymentPreflight(result, ["schedules"]).capabilities[0]!.code, "temporal_unavailable");
  for (const name of ["multiuser", "workspace", "stdio", "chat", "callbacks", "cloudModels", "productionHa"]) {
    assert.equal(deploymentPreflight(result, [name]).allowed, false, name);
  }
});

test("OIDC RBAC and lite do not depend on Kong, Temporal, or PostgreSQL", () => {
  for (const ingress of ["direct", "gateway"] as const) {
    const result = deploymentSnapshot(config({ deploymentProfile: "lite", deploymentIngress: ingress, oidcEnabled: true, oidcIssuer: "https://id.example/realms/agat" }), database);
    assert.equal(result.ready, true);
    assert.equal(result.stateStore, "sqlite");
    assert.equal(result.runtime, "database");
    assert.equal(result.ingress, ingress);
    assert.equal(deploymentPreflight(result, ["multiuser"]).allowed, true);
  }
  const missing = deploymentSnapshot(config({ deploymentProfile: "lite", oidcEnabled: true }), database);
  assert.equal(missing.ready, false);
  assert.equal(missing.blockers[0]!.code, "oidc_configuration_missing");
});

test("multiuser never falls back to legacy and unavailable local identity fails closed", () => {
  assert.equal(deploymentSnapshot(config({ deploymentProfile: "lite" }), database).blockers[0]!.code, "individual_identity_required");
  const local = deploymentSnapshot(config({ authMode: "local", deploymentProfile: "lite" }), database);
  assert.equal(local.ready, false);
  assert.equal(local.blockers[0]!.code, "local_identity_unavailable");
  assert.equal(deploymentPreflight(local, ["runs"]).allowed, false);
  assert.equal(deploymentSnapshot(config({ authMode: "local", oidcEnabled: true }), { ...database, localIdentityAvailable: true }).ready, false);
});

test("Temporal and PostgreSQL requirements are evaluated per selected profile and observed runtime", () => {
  const oidc = { oidcEnabled: true, oidcIssuer: "https://id.example/realms/agat" };
  const base = config({ ...oidc, deploymentProfile: "durable", temporalEnabled: true });
  assert.equal(deploymentSnapshot(base, database).blockers[0]!.code, "temporal_required");
  const connected = { ...database, runtime: { mode: "temporal" as const, connected: true } };
  assert.equal(deploymentSnapshot(base, connected).ready, true);
  assert.equal(deploymentSnapshot(base, { ...connected, runtime: { mode: "temporal", connected: false } }).ready, false);
  const outage = deploymentSnapshot(base, { ...connected, runtime: { mode: "temporal", connected: false } });
  assert.equal(outage.controlPlaneAvailable, true);
  assert.equal(deploymentPreflight(outage, ["runs", "multiuser"]).allowed, true);
  assert.equal(deploymentPreflight(outage, ["processes", "schedules"]).allowed, false);
  const ha = config({ ...base, deploymentProfile: "production-ha" });
  assert.equal(deploymentSnapshot(ha, connected).blockers[0]!.code, "postgresql_required");
  const result = deploymentSnapshot({ ...ha, stateStoreDriver: "postgresql" }, connected);
  assert.equal(result.ready, true);
  assert.equal(result.productionQualified, false);
  assert.equal(deploymentPreflight(result, ["productionHa"]).allowed, false);
});

test("isolated WASI/OCI never imply workspace/STDIO or unverified network containment", () => {
  const deps = { ...database, sandbox: { available: true, networkPolicyEnforced: false } };
  const result = deploymentSnapshot(config(), deps);
  assert.equal(deploymentPreflight(result, ["wasi"]).allowed, true);
  assert.equal(deploymentPreflight(result, ["container"]).capabilities[0]!.code, "network_policy_unverified");
  assert.equal(deploymentPreflight(deploymentSnapshot(config(), { ...deps, sandbox: { available: true, networkPolicyEnforced: true } }), ["container"]).allowed, true);
  assert.equal(deploymentPreflight(result, ["workspace", "stdio"]).allowed, false);
  assert.equal(deploymentPreflight(deploymentSnapshot(config({ mcpEnabled: false }), deps), ["wasi"]).allowed, false);
});

test("unknown operations and invalid profile/auth configuration cannot manufacture readiness", () => {
  const result = deploymentSnapshot(config(), database);
  for (const operations of [[], ["host-shell"], ["runs", 1], "runs", { operations: ["runs"] }]) assert.throws(() => deploymentPreflight(result, operations));
  assert.throws(() => parseDeploymentProfile("admin"));
  assert.throws(() => parseAuthenticationMode("none"));
  assert.equal(parseDeploymentProfile(" lite "), "lite");
  assert.equal(deploymentSnapshot(config({ deploymentProfile: "single-user", oidcEnabled: true, oidcIssuer: "https://id.example/realms/agat" }), database).controlPlaneAvailable, false);
});

test("legacy loopback trust cannot be inherited by remote clients or invalid credentials", () => {
  const base = { adminToken: "fixture-admin", adminHeader: undefined, authorizationHeader: undefined, remoteAddress: "127.0.0.1", requireAdmin: false };
  assert.equal(authenticateLegacyRequest(base).subject, "local-admin");
  for (const remoteAddress of ["192.0.2.10", "::ffff:192.0.2.10", undefined]) {
    assert.throws(() => authenticateLegacyRequest({ ...base, remoteAddress }));
    assert.equal(authenticateLegacyRequest({ ...base, remoteAddress, adminHeader: "fixture-admin" }).subject, "local-admin");
  }
  assert.throws(() => authenticateLegacyRequest({ ...base, adminHeader: "wrong" }));
  assert.throws(() => authenticateLegacyRequest({ ...base, authorizationHeader: "Bearer wrong" }));
  assert.throws(() => authenticateLegacyRequest({ ...base, requireAdmin: true }));
});
