import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  loadTemporalWorkerConfig,
  temporalWorkerConnectionOptions,
  temporalWorkerDeploymentOptions,
} from "../dist/config.js";

function baseEnv(overrides = {}) {
  return {
    AGAT_TEMPORAL_INTERNAL_TOKEN: "test-internal-token",
    ...overrides,
  };
}

describe("Temporal worker production configuration", () => {
  it("keeps local development explicitly unversioned and insecure", () => {
    const config = loadTemporalWorkerConfig(baseEnv());
    assert.equal(config.target, "local");
    assert.equal(config.tls, false);
    assert.equal(config.versioningEnabled, false);
    assert.equal(temporalWorkerDeploymentOptions(config), undefined);
  });

  it("requires TLS, credentials and immutable versioning for Temporal Cloud", () => {
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({ AGAT_TEMPORAL_TARGET: "cloud" })),
      /AGAT_TEMPORAL_API_KEY/,
    );
    const config = loadTemporalWorkerConfig(baseEnv({
      AGAT_TEMPORAL_TARGET: "cloud",
      AGAT_TEMPORAL_ADDRESS: "namespace.account.tmprl.cloud:7233",
      AGAT_TEMPORAL_API_KEY: "cloud-secret",
      AGAT_TEMPORAL_BUILD_ID: "sha-0123456789",
      AGAT_TEMPORAL_DEPLOYMENT_RING: "canary",
    }));
    assert.equal(config.tls, true);
    assert.equal(config.versioningEnabled, true);
    assert.equal(config.deploymentRing, "canary");
    assert.deepEqual(temporalWorkerDeploymentOptions(config), {
      useWorkerVersioning: true,
      version: { deploymentName: "agat-processes", buildId: "sha-0123456789" },
      defaultVersioningBehavior: "PINNED",
    });
    const connection = temporalWorkerConnectionOptions(config);
    assert.equal(connection.address, "namespace.account.tmprl.cloud:7233");
    assert.equal(connection.tls, true);
    assert.equal(connection.apiKey, "cloud-secret");
  });

  it("rejects partial mTLS and unsafe production fallbacks", () => {
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({
        AGAT_TEMPORAL_TARGET: "self-hosted",
        AGAT_TEMPORAL_CLIENT_CERT_PATH: "/tmp/client.crt",
        AGAT_TEMPORAL_BUILD_ID: "build-1",
      })),
      /CLIENT_CERT_PATH.*CLIENT_KEY_PATH/,
    );
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({
        AGAT_TEMPORAL_TARGET: "cloud",
        AGAT_TEMPORAL_API_KEY: "secret",
        AGAT_TEMPORAL_TLS: "false",
        AGAT_TEMPORAL_BUILD_ID: "build-1",
      })),
      /API_KEY.*TLS|Cloud.*TLS/,
    );
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({
        AGAT_TEMPORAL_TARGET: "cloud",
        AGAT_TEMPORAL_API_KEY: "secret",
      })),
      /immutable AGAT_TEMPORAL_BUILD_ID/,
    );
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({ AGAT_TEMPORAL_API_KEY: "secret\nheader" })),
      /AGAT_TEMPORAL_API_KEY.*управляющие символы/,
    );
    assert.throws(
      () => loadTemporalWorkerConfig(baseEnv({ AGAT_TEMPORAL_TLS: "sometimes" })),
      /Некорректное boolean-значение/,
    );
  });
});
