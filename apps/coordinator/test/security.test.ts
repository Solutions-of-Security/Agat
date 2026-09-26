import assert from "node:assert/strict";
import test from "node:test";
import { bearerToken } from "../src/security.js";

test("Bearer parsing preserves supported separators and rejects malformed schemes or token line breaks", () => {
  const cases: Array<[string | undefined, string | null]> = [
    [undefined, null], ["", null], ["Bearer", null], ["Bearer   ", null], ["Basic abc", null], ["Bearerabc", null],
    ["Bearer abc", "abc"], [" bEaReR\t abc  ", "abc"], ["Bearer abc def", "abc def"], ["Bearer\nabc", "abc"],
    ["Bearer abc\rdef", null], ["Bearer abc\ndef", null], ["Bearer abc\u2028def", null], ["Bearer abc\u2029def", null],
  ];
  for (const [header, expected] of cases) assert.equal(bearerToken(header), expected);
});

test("long whitespace prefixes and malformed token suffixes do not need overlapping regex scans", () => {
  for (const suffix of ["a\nb", "a\rb", "a\u2028b", "a\u2029b"]) {
    assert.equal(bearerToken(`Bearer ${" ".repeat(128_000)}${suffix}`), null);
  }
  assert.equal(bearerToken(`Bearer ${" ".repeat(128_000)}valid`), "valid");
});
