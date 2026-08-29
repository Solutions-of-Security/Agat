import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  DEFAULT_MCP_POLICY,
  evaluateMcpPolicy,
  mcpArgumentsPreviewDiff,
  mcpPolicySha256,
  normalizeMcpPolicyDocument,
} from "../src/mcp-policy.js";

const hash = mcpPolicySha256(DEFAULT_MCP_POLICY);

describe("MCP policy as code", () => {
  it("maps risk labels to tiers and enforces four-eyes despite a legacy allow", () => {
    const read = evaluateMcpPolicy({
      document: DEFAULT_MCP_POLICY,
      version: 0,
      sha256: hash,
      namespace: "crm",
      toolName: "find_customer",
      risk: "read",
      legacyPolicy: "allow",
    });
    assert.deepEqual({ tier: read.tier, effect: read.effect, approvals: read.approvals }, {
      tier: "low",
      effect: "allow",
      approvals: 0,
    });

    const destructive = evaluateMcpPolicy({
      document: DEFAULT_MCP_POLICY,
      version: 0,
      sha256: hash,
      namespace: "crm",
      toolName: "delete_customer",
      risk: "destructive",
      legacyPolicy: "allow",
    });
    assert.equal(destructive.tier, "critical");
    assert.equal(destructive.effect, "approval");
    assert.equal(destructive.approvals, 2);
    assert.match(destructive.reason, /four-eyes:mandatory/);
  });

  it("uses the first matching rule and keeps legacy deny as a hard floor", () => {
    const document = normalizeMcpPolicyDocument({
      ...DEFAULT_MCP_POLICY,
      rules: [{
        id: "deny_billing_exports",
        description: "Emergency-independent business rule",
        match: { serverNamespaces: ["billing"], toolPatterns: ["export_*"], risks: ["read"] },
        decision: { tier: "high", effect: "deny", approvals: 0 },
      }],
    });
    const deniedByRule = evaluateMcpPolicy({
      document,
      version: 1,
      sha256: mcpPolicySha256(document),
      namespace: "billing",
      toolName: "export_invoices",
      risk: "read",
      legacyPolicy: "allow",
    });
    assert.equal(deniedByRule.effect, "deny");
    assert.equal(deniedByRule.ruleId, "deny_billing_exports");

    const deniedByLegacy = evaluateMcpPolicy({
      document: DEFAULT_MCP_POLICY,
      version: 0,
      sha256: hash,
      namespace: "crm",
      toolName: "find_customer",
      risk: "read",
      legacyPolicy: "deny",
    });
    assert.equal(deniedByLegacy.effect, "deny");
    assert.match(deniedByLegacy.reason, /legacy:deny/);
  });

  it("rejects policy code that weakens critical effects below two people", () => {
    assert.throws(() => normalizeMcpPolicyDocument({
      ...DEFAULT_MCP_POLICY,
      defaults: {
        ...DEFAULT_MCP_POLICY.defaults,
        destructive: { tier: "critical", effect: "approval", approvals: 1 },
      },
    }), /two-person approval/);
  });

  it("builds a redacted before/after preview diff", () => {
    const diff = mcpArgumentsPreviewDiff({
      before: { email: "old@example.test", apiToken: "old-secret", status: "active" },
      after: { email: "new@example.test", apiToken: "new-secret", status: "active" },
    });
    assert.deepEqual(diff, [
      { op: "replace", path: "/apiToken", before: "[redacted]", after: "[redacted]" },
      { op: "replace", path: "/email", before: "old@example.test", after: "new@example.test" },
    ]);
    assert.equal(JSON.stringify(diff).includes("secret"), false);
  });
});
