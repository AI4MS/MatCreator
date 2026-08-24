import assert from "node:assert/strict";
import test from "node:test";

import { remoteJobLifecycle } from "../src/features/remoteJobs/RemoteJobsController.js";

test("normalizes remote job lifecycle labels", () => {
  assert.deepEqual(remoteJobLifecycle("RUNNING"), { key: "running", label: "Running" });
  assert.deepEqual(remoteJobLifecycle("collected"), { key: "collected", label: "Completed" });
  assert.deepEqual(remoteJobLifecycle("pause_requested"), { key: "pause_requested", label: "Pausing" });
  assert.deepEqual(remoteJobLifecycle(undefined), { key: "unknown", label: "Unknown" });
});
