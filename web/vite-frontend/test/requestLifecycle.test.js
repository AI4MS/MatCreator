import assert from "node:assert/strict";
import test from "node:test";

import {
  findConversationRequest,
  finishRequestCleanup,
  initializeRequestLifecycle,
  markRequestTerminal,
  requestHasActiveRun,
  requestOwnsLiveDom,
  requestPresentsLiveTurn,
  requestRetainsVisibleTurn,
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

test("a terminal request still owns its live message until cleanup resolves", async () => {
  const request = initializeRequestLifecycle({
    id: "run-2",
    message: { lifecycle: "completed" },
    userMessage: { isConnected: true },
    messageView: { element: { isConnected: true } },
  });

  markRequestTerminal(request, "completed");
  assert.equal(requestHasActiveRun(request), false);
  assert.equal(requestRetainsVisibleTurn(request), true);

  finishRequestCleanup(request);
  await request.cleanupComplete;
  assert.equal(requestRetainsVisibleTurn(request), false);
});

test("a reconnect can claim live-turn ownership before its DOM is mounted", async () => {
  const request = initializeRequestLifecycle({ id: "run-3", liveTurnClaimed: true });
  assert.equal(requestRetainsVisibleTurn(request), true);

  finishRequestCleanup(request);
  await request.cleanupComplete;
  assert.equal(requestRetainsVisibleTurn(request), false);
});

test("an empty recovered turn keeps presenting while terminal state is reconciled", async () => {
  const request = initializeRequestLifecycle({
    id: "run-4",
    liveTurnClaimed: true,
    message: { lifecycle: "created", items: [] },
  });
  markRequestTerminal(request, "completed");

  assert.equal(requestHasActiveRun(request), false);
  assert.equal(requestPresentsLiveTurn(request), true);

  request.message.lifecycle = "completed";
  assert.equal(requestPresentsLiveTurn(request), false);
});

test("conversation ownership survives a transient owner-key normalization", () => {
  const request = initializeRequestLifecycle({
    key: "temporary-owner:session-1",
    sessionId: "session-1",
    owner: "temporary-owner",
    liveTurnClaimed: true,
  });
  const requests = new Map([[request.key, request]]);

  assert.strictEqual(findConversationRequest(requests, {
    key: "canonical-owner:session-1",
    sessionId: "session-1",
    owner: "canonical-owner",
  }), request);
});

test("conversation ownership never crosses sessions or ambiguous requests", () => {
  const requests = new Map([
    ["a:session-1", initializeRequestLifecycle({ sessionId: "session-1", liveTurnClaimed: true })],
    ["b:session-1", initializeRequestLifecycle({ sessionId: "session-1", liveTurnClaimed: true })],
  ]);
  assert.equal(findConversationRequest(requests, {
    key: "missing:session-1", sessionId: "session-1", owner: "missing",
  }), null);
  assert.equal(findConversationRequest(requests, {
    key: "missing:session-2", sessionId: "session-2", owner: "missing",
  }), null);
});

test("failed and cancelled runs are terminal while unknown requests stay active", () => {
  assert.equal(requestHasActiveRun(null), false);
  assert.equal(requestHasActiveRun({}), true);
  assert.equal(requestHasActiveRun({ backendStatus: "running" }), true);
  assert.equal(requestHasActiveRun({ backendStatus: "failed" }), false);
  assert.equal(requestHasActiveRun({ backendStatus: "cancelled" }), false);
});

test("live-DOM ownership tracks mounted views through terminal handoff", async () => {
  assert.equal(requestOwnsLiveDom(null), false);
  assert.equal(requestOwnsLiveDom({}), false);
  // Detached DOM (a stale entry) does not own the live area.
  assert.equal(requestOwnsLiveDom({ messageView: { element: { isConnected: false } } }), false);
  assert.equal(requestOwnsLiveDom({ userMessage: { isConnected: false } }), false);
  // Mounted DOM keeps ownership even after the backend run went terminal.
  const request = initializeRequestLifecycle({
    messageView: { element: { isConnected: true } },
    userMessage: { isConnected: false },
  });
  markRequestTerminal(request, "completed");
  assert.equal(requestHasActiveRun(request), false);
  assert.equal(requestOwnsLiveDom(request), true);
  assert.equal(requestOwnsLiveDom({ userMessage: { isConnected: true } }), true);
  // Cleanup (release/handoff) ends ownership regardless of DOM state.
  finishRequestCleanup(request);
  await request.cleanupComplete;
  assert.equal(requestOwnsLiveDom(request), false);
});
