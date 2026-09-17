import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { evaluateSiemResponse } from "../src/siem-export.js";

describe("SIEM sink conformance", () => {
  it("requires an exact acknowledgement for a successful batch", () => {
    assert.deepEqual(evaluateSiemResponse(202, "10-20", "10-20", true), {
      delivered: true,
      terminal: false,
      reasonCode: null,
      errorMessage: null,
    });
    assert.equal(evaluateSiemResponse(200, "", "10-20", true).reasonCode, "ack_missing");
    assert.equal(evaluateSiemResponse(200, "10-19", "10-20", true).reasonCode, "ack_mismatch");
  });

  it("retries transient responses and dead-letters permanent responses", () => {
    assert.equal(evaluateSiemResponse(429, "", "10-20", true).terminal, false);
    assert.equal(evaluateSiemResponse(503, "", "10-20", true).terminal, false);
    assert.deepEqual(evaluateSiemResponse(400, "", "10-20", true), {
      delivered: false,
      terminal: true,
      reasonCode: "http_4xx",
      errorMessage: "SIEM ответил HTTP 400",
    });
  });
});
