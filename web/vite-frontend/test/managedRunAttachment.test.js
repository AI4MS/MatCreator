import assert from "node:assert/strict";
import test from "node:test";

import { createSessionRuntime } from "../src/features/session/runtime.js";

// A wakeup (harness-started) managed run must never evict a turn whose
// streamed DOM is still mounted: those partial chunks are not persisted
// anywhere server-side, so clearing them mid-handoff loses them for good.

const snapshotEvents = [
  { id: "user-1", author: "user", content: { parts: [{ text: "run the job" }] } },
  { id: "assistant-1", author: "MatCreator", content: { parts: [{ text: "Job finished." }] } },
];
const snapshot = () => ({
  events: snapshotEvents,
  state: { agent_mode: "normal" },
  userId: "alice",
  event_meta: snapshotEvents.map((_, index) => ({ index, cursor: `c${index}`, turn_id: "turn-1" })),
  pagination: { start_index: 0, total_count: snapshotEvents.length },
  revision: "completed",
});

function createHarness(t) {
  const original = {
    fetch: globalThis.fetch,
    window: globalThis.window,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
  };
  Object.assign(globalThis, {
    window: globalThis,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    cancelAnimationFrame: clearTimeout,
  });
  t.after(() => Object.assign(globalThis, original));

  const state = {
    sessionId: "session-1", activeSessionUserId: "alice", userId: "alice",
    sessionReady: true, activeRequests: new Map(), sessionSummaries: {},
    sessionViewCache: new Map(), summaryGeneratedFor: new Set(),
  };
  const counters = { clears: 0, follows: 0, streams: 0, historyLoads: 0, released: [] };
  const views = [];
  const host = { appendChild(element) { element.isConnected = true; } };
  const viewport = {
    liveHost: host,
    rows: [],
    clearLive() {
      counters.clears += 1;
      views.forEach((view) => { view.element.isConnected = false; });
    },
    followOutput() { counters.follows += 1; },
    currentOffset: () => 0,
    restoreOffset() {},
    setRows(rows) { this.rows = rows; },
    reset() {},
    metrics: () => ({}),
  };

  globalThis.fetch = async (url) => {
    if (url === "/events") {
      counters.streams += 1;
      const envelopes = [
        { type: "event", sequence: 1, data: `data: ${JSON.stringify(snapshotEvents[1])}\n\n` },
        { type: "terminal", status: "completed" },
      ];
      return new Response(envelopes.map((item) => `data: ${JSON.stringify(item)}\n\n`).join(""));
    }
    counters.historyLoads += 1;
    return Response.json(snapshot());
  };

  const runtime = createSessionRuntime({
    session: {
      state,
      requestKey: (
        sessionId = state.sessionId,
        owner = state.activeSessionUserId || state.userId,
      ) => `${owner}:${sessionId}`,
      releaseRequest: (request) => {
        counters.released.push(request);
        state.activeRequests.delete(request.key);
        request.cleanupDone = true;
        request.finishCleanup?.();
      },
    },
    timeline: {
      chatArea: {},
      stepExecutionFeed: {
        reset() {}, setHierarchy() {}, startLiveTurn() {}, resumeLiveTurn() {}, finishLiveTurn() {},
      },
      getFunctionResponse: (part) => part?.functionResponse || null,
      displayStoredUserText: (text) => text,
      addMessage() {},
      addAgentTimelineMessage(message, shownPlots, _actions, mountHost) {
        const element = { isConnected: false, hidden: false, classList: { add() {} } };
        mountHost?.appendChild?.(element);
        const view = { element, timelineElement: {}, stepFeedLiveHost: {} };
        views.push(view);
        return view;
      },
      renderTimeline() {},
      addPlanApprovalActions() {},
      clearDisclosures() {},
    },
    ui: {
      updateSendButtonState() {}, renderSessionBanner() {},
      refreshSessionFiles: async () => {}, workdirDisplay: null,
      onRequestStateChange() {}, attachAgentRunningIndicator() {}, updateAgentRunningStatus() {},
    },
    managedRun: { eventsUrl: () => "/events" },
    createViewport: () => viewport,
  });

  const settle = async () => {
    for (let i = 0; i < 400 && state.activeRequests.size; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  };
  return { runtime, state, counters, settle, viewport };
}

test("a mid-handoff terminal request defers wakeup attachment without clearing it", async (t) => {
  const { runtime, state, counters } = createHarness(t);
  const midHandoff = {
    key: "alice:session-1", sessionId: "session-1", owner: "alice",
    backendStatus: "completed", runId: "finished-run",
    messageView: { element: { isConnected: true } },
  };
  state.activeRequests.set(midHandoff.key, midHandoff);

  const attached = runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", status: "running", created_at: 1 }, "session-1", "alice",
  );

  assert.equal(attached, null);
  assert.equal(counters.clears, 0);
  assert.deepEqual(counters.released, []);
  assert.equal(counters.streams, 0);
  assert.strictEqual(state.activeRequests.get("alice:session-1"), midHandoff);
});

test("a stale detached request is released and the wakeup run streams to handoff", async (t) => {
  const { runtime, state, counters, settle } = createHarness(t);
  const stale = {
    key: "alice:session-1", sessionId: "session-1", owner: "alice",
    backendStatus: "completed", runId: "finished-run",
    messageView: { element: { isConnected: false } },
  };
  state.activeRequests.set(stale.key, stale);

  const attached = runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", status: "running", created_at: 1 }, "session-1", "alice",
  );

  assert.ok(attached);
  assert.equal(attached.runId, "wakeup-run");
  assert.ok(counters.released.includes(stale));
  await settle();
  assert.equal(state.activeRequests.size, 0);
  assert.equal(counters.streams, 1);
  assert.ok(counters.historyLoads >= 1);
});

test("the wakeup trigger folds in from durable history while the run is still streaming", async (t) => {
  const { runtime, state, counters, viewport } = createHarness(t);
  const triggerText = "REMOTE JOB STATUS UPDATE from the harness.\nNotification ID: n-1\n";
  const events = [
    ...snapshotEvents,
    { id: "trigger-1", author: "user", timestamp: 2, content: { parts: [{ text: triggerText }] } },
  ];
  globalThis.fetch = async (url) => {
    if (url === "/events") {
      counters.streams += 1;
      // The managed channel never replays user events; keep it open so the
      // trigger can only come from durable history *during* streaming.
      return new Response(new ReadableStream({ start() {} }));
    }
    counters.historyLoads += 1;
    return Response.json({
      events, state: { agent_mode: "normal" }, userId: "alice",
      event_meta: events.map((_, index) => ({ index, cursor: `c${index}`, turn_id: `turn-${index}` })),
      pagination: { start_index: 0, total_count: events.length },
      revision: `r${counters.historyLoads}`,
    });
  };

  const attached = runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", status: "running", created_at: 1 }, "session-1", "alice",
  );
  assert.ok(attached);
  t.after(() => attached.controller.abort());

  const triggerRowMounted = () => viewport.rows.some((row) => row.type === "user"
    && row.records?.some((record) => record.event.id === "trigger-1"));
  for (let i = 0; i < 400 && !triggerRowMounted(); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }

  assert.ok(triggerRowMounted(), "trigger user row must mount while the run streams");
  assert.equal(state.activeRequests.get("alice:session-1"), attached);
  assert.equal(attached.backendStatus, "starting");
  assert.ok(counters.historyLoads >= 1);
});

test("a wakeup mounts alongside a presenting turn owned by another key without clearing it", async (t) => {
  const { runtime, state, counters, settle } = createHarness(t);
  // Owner-key normalization can leave the presenting request under another
  // map key; its mounted DOM must survive the wakeup's presentation mount.
  state.activeRequests.set("alice:session-2", {
    key: "alice:session-2", sessionId: "session-2", owner: "alice",
    backendStatus: "completed",
    messageView: { element: { isConnected: true } },
  });

  const attached = runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", status: "running", created_at: 1 }, "session-1", "alice",
  );
  const clearsAtMount = counters.clears;
  const followsAtMount = counters.follows;

  assert.ok(attached?.messageView);
  assert.equal(clearsAtMount, 0);
  assert.equal(followsAtMount, 0);
  state.activeRequests.delete("alice:session-2");
  await settle();
  assert.equal(state.activeRequests.size, 0);
});
