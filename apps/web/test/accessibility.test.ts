import assert from "node:assert/strict";
import test from "node:test";

import { getNextTabIndex } from "../src/components/AccessibleTabs.tsx";

test("roving tabs циклически обрабатывают стрелки", () => {
  assert.equal(getNextTabIndex(0, "ArrowRight", 4), 1);
  assert.equal(getNextTabIndex(3, "ArrowRight", 4), 0);
  assert.equal(getNextTabIndex(0, "ArrowLeft", 4), 3);
  assert.equal(getNextTabIndex(2, "ArrowDown", 4), 2);
});

test("roving tabs поддерживают Home/End и безопасное пустое состояние", () => {
  assert.equal(getNextTabIndex(2, "Home", 4), 0);
  assert.equal(getNextTabIndex(1, "End", 4), 3);
  assert.equal(getNextTabIndex(0, "ArrowRight", 0), -1);
  assert.equal(getNextTabIndex(2, "Enter", 4), 2);
});
