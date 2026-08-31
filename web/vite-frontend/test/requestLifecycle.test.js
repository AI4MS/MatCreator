import assert from "node:assert/strict";
import test from "node:test";

import {
  finishRequestCleanup,
  initializeRequestLifecycle,
  markRequestTerminal,
  requestHasActiveRun,
} from "../src/features/session/requestLifecycle.js";

test("a terminal backend run stops presenting as active before UI cleanup", async () => {
  const request = initializeRequestLifecycle({ id: "run-1" });
  assert.equal(requestHasActiveRun(request), true);

  markRequestTerminal(request, "completed");
  assert.equal(requestHasActiveRun(request), false);

  let cleaned = false;
  request.cleanupComplete.then(() => { cleaned = true; });
  await Promise.resolve();
  assert.equal(cleaned, false);

  finishRequestCleanup(request);
  await request.cleanupComplete;
  assert.equal(cleaned, true);
});

test("failed and cancelled runs are terminal while unknown requests stay active", () => {
  assert.equal(requestHasActiveRun(null), false);
  assert.equal(requestHasActiveRun({}), true);
  assert.equal(requestHasActiveRun({ backendStatus: "running" }), true);
  assert.equal(requestHasActiveRun({ backendStatus: "failed" }), false);
  assert.equal(requestHasActiveRun({ backendStatus: "cancelled" }), false);
});
