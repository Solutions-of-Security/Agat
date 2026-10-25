import assert from "node:assert/strict";
import { test } from "node:test";
import { reportPackRequest } from "../src/processPacks.ts";

test("report form creates the strict request contract and preserves distinct requirements", () => {
  const value = reportPackRequest(" Compare periods ", " July–August ", " Use sources \r\n\nUse sources\nShow formulas");
  assert.equal(value.error, null);
  assert.deepEqual(JSON.parse(value.input!), { schemaVersion: "report-request.v1", question: "Compare periods", period: "July–August", requirements: ["Use sources", "Show formulas"] });
});

test("required fields, excessive requirements and byte overflow cannot become a start payload", () => {
  assert.equal(reportPackRequest(" ", "July", "").input, null);
  assert.equal(reportPackRequest("Question", " ", "").input, null);
  assert.equal(reportPackRequest("q".repeat(8001), "July", "").input, null);
  assert.equal(reportPackRequest("Question", "p".repeat(161), "").input, null);
  assert.equal(reportPackRequest("Question", "July", Array.from({ length: 21 }, (_, i) => `Rule ${i}`).join("\n")).input, null);
  assert.equal(reportPackRequest("Question", "July", "r".repeat(8001)).input, null);
  const large = Array.from({ length: 8 }, (_, i) => `Rule ${i} ${"Ж".repeat(7000)}`).join("\n");
  assert.match(reportPackRequest("Question", "July", large).error!, /100 000 байт/);
  assert.equal(reportPackRequest("Question", "July", "").error, null);
});
