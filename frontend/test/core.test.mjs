// Hermetic unit tests for the pure core (compiled artifact). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseRoute,
  checkedKey,
  diffFields,
  clamp01,
  asNumber,
} from "../js/core.js";

test("parseRoute: default is search, strips leading hash", () => {
  assert.deepEqual(parseRoute("#/"), { name: "search", args: [] });
  assert.deepEqual(parseRoute(""), { name: "search", args: [] });
  assert.deepEqual(parseRoute("#/entity/N000000001"), { name: "entity", args: ["N000000001"] });
  assert.deepEqual(parseRoute("#/compare/R1/R2"), { name: "compare", args: ["R1", "R2"] });
  assert.deepEqual(parseRoute("#/review?x=1"), { name: "review", args: [] });
});

test("checkedKey is run-scoped and order-sensitive", () => {
  assert.equal(checkedKey("runA", "R1", "R2"), "runA|R1|R2");
  assert.notEqual(checkedKey("runA", "R1", "R2"), checkedKey("runB", "R1", "R2"));
  assert.notEqual(checkedKey("runA", "R1", "R2"), checkedKey("runA", "R2", "R1"));
});

test("diffFields ignores identity/metadata fields", () => {
  const row = { record_id: "R", policy_id: "P", observed_at: "d", name_kanji: "佐藤", phone: "o" };
  assert.deepEqual(diffFields(row), ["name_kanji", "phone"]);
});

test("clamp01 / asNumber bounds and NaN safety", () => {
  assert.equal(clamp01(1.4), 1);
  assert.equal(clamp01(-0.2), 0);
  assert.equal(clamp01(Number.NaN), 0);
  assert.equal(asNumber("nope", 7), 7);
  assert.equal(asNumber("0.5"), 0.5);
});

test("diffFields excludes identity/metadata noise from the comparison", () => {
  const row = { record_id: "R1", policy_id: "P1", observed_at: "2020-01-01", name_kanji: "佐藤" };
  assert.deepEqual(diffFields(row), ["name_kanji"]);
});