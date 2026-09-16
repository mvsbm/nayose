// Hermetic unit tests for the pure core (compiled artifact). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseRoute,
  checkedKey,
  diffFields,
  clamp01,
  asNumber,
  pollJob,
} from "../js/core.js";
import { ROUTES, routeDescriptor, routeHash } from "../js/routes.js";
import { CommandRegistry, createWorkbenchCommands } from "../js/commands.js";
import { parseCsv } from "../js/data-entry-view.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady, isAsyncLoading } from "../js/async-state.js";

test("async state transitions are explicit and discriminated", () => {
  const idle = asyncIdle();
  assert.equal(idle.kind, "idle");
  const loading = asyncLoading("request-1");
  assert.equal(loading.kind, "loading");
  assert.equal(loading.requestId, "request-1");
  assert.equal(isAsyncLoading(loading), true);
  const ready = asyncReady({ count: 1 });
  assert.deepEqual(ready, { kind: "ready", value: { count: 1 } });
  const failure = asyncError("request failed", "request-1");
  assert.deepEqual(failure, { kind: "error", message: "request failed", requestId: "request-1" });
});

test("parseRoute: default is search, strips leading hash", () => {
  assert.deepEqual(parseRoute("#/"), { name: "search", args: [] });
  assert.deepEqual(parseRoute(""), { name: "search", args: [] });
  assert.deepEqual(parseRoute("#/entity/N000000001"), { name: "entity", args: ["N000000001"] });
  assert.deepEqual(parseRoute("#/compare/R1/R2"), { name: "compare", args: ["R1", "R2"] });
  assert.deepEqual(parseRoute("#/entity/N%2F1"), { name: "entity", args: ["N/1"] });
  assert.deepEqual(parseRoute("#/review?x=1"), { name: "review", args: [] });
});

test("route descriptors fail closed and build canonical hashes", () => {
  assert.equal(routeDescriptor("not-a-route").name, "search");
  assert.equal(routeHash("entity", ["N1"]), "#/entity/N1");
  assert.ok(ROUTES.some((route) => route.name === "entities"));
});

test("command registry filters capabilities and disposes registrations", () => {
  const registry = new CommandRegistry();
  const context = {
    t: (key) => key,
    closePalette: () => {},
    navigate: () => {},
    toggleNavigation: () => {},
    setTheme: () => {},
    setDensity: () => {},
    announce: () => {},
    capabilities: { admin: false, dataEntry: false },
  };
  const registration = registry.registerAll(createWorkbenchCommands(ROUTES));
  assert.ok(registry.list(context).some((command) => command.id === "navigate.search"));
  assert.equal(registry.list(context).some((command) => command.id === "navigate.admin"), false);
  assert.equal(registry.execute("navigate.admin", context), false);
  registration.dispose();
  assert.deepEqual(registry.list(context), []);
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

test("CSV parser handles RFC quoting and rejects malformed quotes", () => {
  assert.deepEqual(parseCsv('name_kanji,phone\n"佐藤, 太郎","090""123"'), [
    { name_kanji: "佐藤, 太郎", name_kana: "", phone: "090\"123", email: "" },
  ]);
  assert.throws(() => parseCsv('name_kanji\n佐藤"太郎'), /csv_parse_error/);
});

test("pollJob distinguishes timeout and cancellation from terminal failure", async () => {
  const initial = { id: "job", run_id: "run", stage: "queued", elapsed_ms: 0, timeline: [], error: null, result: null };
  const timedOut = await pollJob(async () => initial, initial, 0);
  assert.equal(timedOut.poll_status, "timeout");
  assert.equal(timedOut.stage, "queued");
  const controller = new AbortController();
  controller.abort();
  const cancelled = await pollJob(async () => initial, initial, 300, undefined, controller.signal);
  assert.equal(cancelled.poll_status, "cancelled");
});