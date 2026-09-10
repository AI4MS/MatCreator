import assert from "node:assert/strict";
import test from "node:test";

import { createSessionRuntime } from "../src/features/session/runtime.js";

// A reconnected (wakeup) managed run must present like a composed turn:
// streamed text renders incrementally as partial events arrive, and the
// status line follows the event flow instead of freezing on "working".

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const snapshotEvents = [
  { id: "user-1", author: "user", content: { parts: [{ text: "REMOTE JOB STATUS UPDATE from the harness." }] } },
  { id: "assistant-1", author: "MatCreator", content: { parts: [{ text: "Job results collected." }] } },
];
const snapshot = () => ({
  events: snapshotEvents,
  state: { agent_mode: "normal" },
  userId: "alice",
  event_meta: snapshotEvents.map((_, index) => ({ index, cursor: `c${index}`, turn_id: "turn-1" })),
  pagination: { start_index: 0, total_count: snapshotEvents.length },
  revision: "completed",
});

const envelope = (value) => `data: ${JSON.stringify(value)}\n\n`;
const adkEvent = (value) => envelope({
  type: "event",
  sequence: adkEvent.sequence += 1,
  data: `data: ${JSON.stringify(value)}\n\n`,
});
adkEvent.sequence = 0;

test("a reconnected wakeup run streams text incrementally and animates status phases", async (t) => {
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
    sessionViewCache: new Map(), summaryGeneratedFor: new Set(), deploymentMode: "local",
  };
  const sessionRequestKey = (
    sessionId = state.sessionId,
    owner = state.activeSessionUserId || state.userId,
  ) => `${owner}:${sessionId}`;

  let enqueue;
  let close;
  const stream = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      enqueue = (text) => controller.enqueue(encoder.encode(text));
      close = () => controller.close();
    },
  });
  globalThis.fetch = async (url) => {
    if (String(url) === "/events") return new Response(stream);
    if (String(url).startsWith("/api/agent-graph/")) return Response.json({ nodes: {} });
    return Response.json(snapshot());
  };

  const renders = [];
  const phases = [];
  const feedStub = {
    reset() {}, setHierarchy() {}, startLiveTurn() {}, resumeLiveTurn() {}, finishLiveTurn() {},
  };
  const liveHost = { children: [], appendChild(el) { el.isConnected = true; this.children.push(el); }, prepend() {} };
  const runtime = createSessionRuntime({
    session: {
      state,
      requestKey: sessionRequestKey,
      releaseRequest: (request) => {
        if (state.activeRequests.get(request.key) === request) state.activeRequests.delete(request.key);
        request.finishCleanup?.();
      },
    },
    timeline: {
      chatArea: null,
      stepExecutionFeed: feedStub,
      getFunctionResponse: (part) => part?.functionResponse || null,
      displayStoredUserText: (text) => text,
      addMessage: () => ({ classList: { add() {} } }),
      addAgentTimelineMessage: (message) => ({
        element: { isConnected: true },
        timelineElement: {},
        stepFeedLiveHost: {},
        finishDuration() {},
        finishLiveActivity() {},
        live: true,
        message,
      }),
      addPlanApprovalActions() {},
      renderTimeline: (view, message) => {
        renders.push(message.items.filter((item) => item.type === "text").map((item) => item.text).join(""));
      },
      clearDisclosures() {},
    },
    ui: {
      updateSendButtonState() {},
      renderSessionBanner() {},
      refreshSessionFiles: async () => [],
      workdirDisplay: null,
      onRequestStateChange() {},
      attachAgentRunningIndicator() {},
      updateAgentRunningStatus: (phase) => phases.push(phase),
    },
    managedRun: { eventsUrl: () => "/events" },
    createViewport: () => ({
      liveHost,
      clearLive() {},
      followOutput() {},
      setRows() {},
      currentOffset: () => 0,
      restoreOffset() {},
      reset() {},
      metrics: () => ({}),
    }),
  });

  const request = runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", latest_sequence: 0, created_at: 1 },
    "session-1",
    "alice",
  );
  assert.ok(request);
  await wait(20);

  enqueue(adkEvent({ author: "MatCreator", partial: true, content: { parts: [{ text: "The job has completed" }] } }));
  await wait(120);
  assert.ok(
    renders.some((text) => text === "The job has completed"),
    `first chunk must render before the next one arrives (renders: ${JSON.stringify(renders)})`,
  );

  enqueue(adkEvent({ author: "MatCreator", partial: true, content: { parts: [{ text: " successfully!" }] } }));
  enqueue(adkEvent({
    author: "MatCreator",
    content: { parts: [{ functionCall: { id: "call-1", name: "get_remote_job_status", args: {} } }] },
  }));
  await wait(120);
  assert.ok(renders.some((text) => text === "The job has completed successfully!"));
  // Streamed text keeps "thinking" live; the tool call moves the status line
  // to its tool phase exactly as the composer path does.
  assert.ok(phases.includes("thinking"), `phases: ${JSON.stringify(phases)}`);
  assert.equal(phases.at(-1), "working"); // get_remote_job_status → generic tool phase

  enqueue(adkEvent({
    author: "MatCreator",
    content: { parts: [{ functionCall: { id: "call-2", name: "run_node_executor", args: { node_id: "n1" } }
    }] },
  }));
  await wait(60);
  assert.equal(phases.at(-1), "executing");

  enqueue(envelope({ type: "terminal", status: "completed", latest_sequence: adkEvent.sequence }));
  close();
  await wait(200);
  assert.equal(state.activeRequests.size, 0, "the finished run must hand off and release its request");
});

test("attaching a wakeup run keeps the previous turn's durable rows visible", async (t) => {
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
    sessionViewCache: new Map(), summaryGeneratedFor: new Set(), deploymentMode: "local",
  };
  const sessionRequestKey = (
    sessionId = state.sessionId,
    owner = state.activeSessionUserId || state.userId,
  ) => `${owner}:${sessionId}`;

  const priorTurn = [
    { id: "user-1", author: "user", timestamp: 1000, content: { parts: [{ text: "submit a job" }] } },
    { id: "assistant-1", author: "MatCreator", timestamp: 1001, content: { parts: [{ text: "Submitted." }] } },
  ];
  // The wakeup's harness user event and the run's partial reply are persisted
  // after the run starts (created_at = 2000 s below).
  const wakeupTurn = [
    { id: "user-2", author: "user", timestamp: 2001, content: { parts: [{ text: "REMOTE JOB STATUS UPDATE from the harness." }] } },
    { id: "assistant-2", author: "MatCreator", timestamp: 2002, content: { parts: [{ text: "The batch job has completed" }] } },
  ];
  const page = (events, revision) => ({
    events,
    state: { agent_mode: "normal" },
    userId: "alice",
    event_meta: events.map((event, index) => ({ index, cursor: `c${index}`, turn_id: event.id })),
    pagination: { start_index: 0, total_count: events.length },
    revision,
  });
  let currentSnapshot = page(priorTurn, "r1");

  let close;
  const stream = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      close = (text) => {
        if (text) controller.enqueue(encoder.encode(text));
        controller.close();
      };
    },
  });
  globalThis.fetch = async (url) => {
    if (String(url) === "/events") return new Response(stream);
    if (String(url).startsWith("/api/agent-graph/")) return Response.json({ nodes: {} });
    return Response.json(currentSnapshot);
  };

  const setRowsCalls = [];
  const liveHost = { appendChild(el) { el.isConnected = true; }, prepend() {} };
  const runtime = createSessionRuntime({
    session: {
      state,
      requestKey: sessionRequestKey,
      releaseRequest: (request) => {
        if (state.activeRequests.get(request.key) === request) state.activeRequests.delete(request.key);
        request.finishCleanup?.();
      },
    },
    timeline: {
      chatArea: null,
      stepExecutionFeed: { reset() {}, setHierarchy() {}, startLiveTurn() {}, resumeLiveTurn() {}, finishLiveTurn() {} },
      getFunctionResponse: (part) => part?.functionResponse || null,
      displayStoredUserText: (text) => text,
      addMessage: () => ({ classList: { add() {} } }),
      addAgentTimelineMessage: () => ({
        element: { isConnected: true }, timelineElement: {}, stepFeedLiveHost: {},
        finishDuration() {}, finishLiveActivity() {}, live: true,
      }),
      addPlanApprovalActions() {},
      renderTimeline() {},
      clearDisclosures() {},
    },
    ui: {
      updateSendButtonState() {}, renderSessionBanner() {},
      refreshSessionFiles: async () => [], workdirDisplay: null,
      onRequestStateChange() {}, attachAgentRunningIndicator() {}, updateAgentRunningStatus() {},
    },
    managedRun: { eventsUrl: () => "/events" },
    createViewport: () => ({
      liveHost,
      clearLive() {},
      followOutput() {},
      setRows: (rows) => setRowsCalls.push(rows.map((row) => `${row.type}:${row.startIndex}`)),
      currentOffset: () => 0,
      restoreOffset() {},
      reset() {},
      metrics: () => ({}),
    }),
  });

  await runtime.loadSession("session-1", "alice");
  assert.deepEqual(setRowsCalls.at(-1), ["user:0", "assistant:1"]);

  // The wakeup run starts long after the stored turn: attaching must not
  // claim (and hide) the previous assistant reply while the store still
  // predates the run's own user event.
  runtime.startManagedRunReconnect(
    { run_id: "wakeup-run", latest_sequence: 0, created_at: 2000 },
    "session-1",
    "alice",
  );
  assert.deepEqual(
    setRowsCalls.at(-1),
    ["user:0", "assistant:1"],
    "attaching the wakeup run must keep the previous turn's rows",
  );

  // Once a mid-run snapshot contains the run's own user event, the durable
  // partial reply after it belongs to the live bubble and stays filtered,
  // while the earlier completed turn remains visible.
  currentSnapshot = page([...priorTurn, ...wakeupTurn], "r2");
  await runtime.loadSession("session-1", "alice");
  assert.deepEqual(setRowsCalls.at(-1), ["user:0", "assistant:1", "user:2"]);

  close(envelope({ type: "terminal", status: "completed", latest_sequence: 0 }));
  await wait(200);
  assert.equal(state.activeRequests.size, 0);
  assert.deepEqual(
    setRowsCalls.at(-1),
    ["user:0", "assistant:1", "user:2", "assistant:3"],
    "after the durable handoff every persisted row is visible again",
  );
});
