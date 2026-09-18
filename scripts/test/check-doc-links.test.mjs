import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { checkMarkdownLinks } from "../check-doc-links.mjs";

function fixture(t, content) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agat-links-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "docs"));
  fs.writeFileSync(path.join(root, "docs", "index.md"), content);
  fs.writeFileSync(path.join(root, "docs", "Valid file.md"), "# Target\n");
  return root;
}

test("checks Markdown reference links, images, escaped spaces and fragments", (t) => {
  const root = fixture(t, '[guide][ref]\n\n[ref]: <Valid file.md#heading>\n\n![image](Valid%20file.md)');
  assert.deepEqual(checkMarkdownLinks(root, ["docs/index.md"]), { checked: 2, errors: [] });
});

test("ignores fenced code, inline code and external links", (t) => {
  const root = fixture(t, '```md\n[missing](missing.md)\n```\n`[missing](missing.md)`\n[web](https://example.com) [anchor](#here)');
  assert.deepEqual(checkMarkdownLinks(root, ["docs/index.md"]), { checked: 0, errors: [] });
});

test("reports missing files and case differences on all platforms", (t) => {
  const root = fixture(t, '[missing](missing.md)\n[wrong case](valid%20file.md)');
  const result = checkMarkdownLinks(root, ["docs/index.md"]);
  assert.equal(result.checked, 2);
  assert.equal(result.errors.length, 2);
  assert.match(result.errors[0], /docs\/index.md:1:.*missing.md/);
});

test("rejects links outside the checkout while allowing repository-root links", (t) => {
  const root = fixture(t, '[escape](../../outside.md) [root](/docs/Valid%20file.md)');
  const result = checkMarkdownLinks(root, ["docs/index.md"]);
  assert.equal(result.checked, 2);
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0], /outside.md/);
});

test("does not accept a generated local file absent from the repository", (t) => {
  const root = fixture(t, '[generated](Valid%20file.md)');
  const result = checkMarkdownLinks(root, ["docs/index.md"], ["docs/index.md"]);
  assert.equal(result.errors.length, 1);
});
