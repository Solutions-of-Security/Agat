import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { validateActionDialogValue } from "../src/components/ActionDialog.tsx";

const sourceRoot = fileURLToPath(new URL("../src", import.meta.url));

function sourceFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(target);
    return /\.tsx?$/u.test(entry.name) ? [target] : [];
  });
}

test("обязательная причина проверяется внутри product dialog", () => {
  const input = {
    label: "Причина отзыва",
    required: true,
    requiredMessage: "Укажите причину отзыва release",
    maxLength: 20,
  };
  assert.equal(validateActionDialogValue("   ", input), "Укажите причину отзыва release");
  assert.equal(validateActionDialogValue("Инцидент устранён", input), null);
  assert.equal(validateActionDialogValue("x".repeat(21), input), "Не больше 20 символов");
});

test("в frontend source не возвращаются blocking browser prompt/confirm", () => {
  const violations = sourceFiles(sourceRoot).flatMap((file) => {
    const source = fs.readFileSync(file, "utf8");
    return /\b(?:window\.)?(?:prompt|confirm)\s*\(/u.test(source) ? [path.relative(sourceRoot, file)] : [];
  });
  assert.deepEqual(violations, []);
});
