import assert from "node:assert/strict";
import test from "node:test";

import {
  createSessionId,
  createSessionRequestKey,
  managedRunEventsUrl,
  shouldRefreshPlanGraphForTool,
  workspaceFileUrl,
} from "../src/features/session/sessionUtils.js";

test("session utilities build stable IDs and scoped request keys", () => {
  assert.equal(
    createSessionId({ now: 1234, randomId: "random" }),
    "session-1234-random",
  );
  assert.equal(createSessionRequestKey("session-a", "owner-a"), "owner-a:session-a");
  assert.equal(createSessionRequestKey("", ""), "user:");
});

test("session URL helpers encode untrusted identifiers and paths", () => {
  assert.equal(
    managedRunEventsUrl({ runId: "run/a", lastSequence: 7 }),
    "/api/runs/run%2Fa/events?after=7",
  );
  assert.equal(
    workspaceFileUrl("outputs/a b.cif", "session/a"),
    "/api/workspace/files?path=outputs%2Fa+b.cif&session_id=session%2Fa",
  );
});

test("plan refresh is limited to graph validation tools", () => {
  assert.equal(shouldRefreshPlanGraphForTool("validate_graph"), true);
  assert.equal(shouldRefreshPlanGraphForTool("validate_plan"), true);
  assert.equal(shouldRefreshPlanGraphForTool("read_file"), false);
});
