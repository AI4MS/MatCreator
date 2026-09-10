import assert from "node:assert/strict";
import test from "node:test";

import { createSessionCoordinator } from "../src/features/session/SessionCoordinator.js";

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function createHarness(overrides = {}) {
  const state = {
    sessionId: "session-a",
    userId: "user-a",
    activeSessionUserId: "user-a",
    sessionReady: false,
    activeRequests: new Map(),
    remoteJobs: [],
    customWorkdir: "",
    defaultWorkdir: "",
    agentMode: "normal",
    ...overrides.state,
  };
  const sessionRequestKey = (
    sessionId = state.sessionId,
    owner = state.activeSessionUserId || state.userId,
  ) => `${owner}:${sessionId}`;

  return {
    state,
    coordinator: createSessionCoordinator({
      state,
      appName: "test-app",
      createSessionId: () => "new-session",
      sessionRequestKey,
      activeSessionRequest: () => null,
      requestHasActiveRun: (request) => Boolean(request?.running),
      activeSessionBackendUserId: () => state.userId,
      updateSendButtonState() {},
      storeSessionSelection() {},
      clearSessionSelection() {},
      loadSessions: async () => {},
      getSessionRuntime: () => ({ resetTranscript() {} }),
      stepExecutionFeed: { reset() {} },
      renderSessionFilesTree() {},
      clearCurrentUploads() {},
      remoteJobsController: { reset() {}, startPolling() {}, async load() {} },
      agentGraph: { reset() {}, startPolling() {} },
      planGraph: { reset() {}, startPolling() {} },
      hidePlanGraph() {},
      clearDisclosures() {},
      renderSessionBanner() {},
      showConfirmDialog: async () => false,
      fetchImpl: async () => ({ ok: true, status: 200 }),
      ...overrides,
      state,
    }),
  };
}

test("session display status prioritizes active local work", () => {
  const { coordinator, state } = createHarness();
  state.activeRequests.set("user-a:session-a", { running: true });

  assert.equal(coordinator.displayStatus({ id: "session-a", status: "idle" }, "user-a"), "running");

  state.activeRequests.clear();
  state.remoteJobs = [{ status: "queued" }];
  assert.equal(coordinator.displayStatus({ id: "session-a", status: "idle" }, "user-a"), "running");
  assert.equal(coordinator.displayStatus({ id: "session-b", status: "complete" }, "user-a"), "idle");
});

test("session creation deduplicates only matching session and owner requests", async () => {
  const requests = [];
  let loadCount = 0;
  const fetchImpl = (url) => {
    const request = deferred();
    requests.push({ url, request });
    return request.promise;
  };
  const { coordinator, state } = createHarness({
    fetchImpl,
    loadSessions: async () => { loadCount += 1; },
  });

  const first = coordinator.createSession();
  const duplicate = coordinator.createSession();
  await Promise.resolve();
  assert.equal(requests.length, 1);

  state.sessionId = "session-b";
  const second = coordinator.createSession();
  await Promise.resolve();
  assert.equal(requests.length, 2);
  assert.match(requests[1].url, /session-b$/);

  requests[1].request.resolve({ ok: true, status: 200 });
  assert.equal(await second, true);

  requests[0].request.resolve({ ok: true, status: 200 });
  assert.equal(await first, false);
  assert.equal(await duplicate, false);
  assert.equal(loadCount, 1);
});

test("destroy aborts pending session creation", async () => {
  let capturedSignal;
  const fetchImpl = (_url, { signal }) => {
    capturedSignal = signal;
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
  };
  const { coordinator } = createHarness({ fetchImpl });

  const creation = coordinator.createSession();
  await Promise.resolve();
  coordinator.destroy();

  assert.equal(capturedSignal.aborted, true);
  assert.equal(await creation, false);
});

test("session log download releases its temporary object URL", async () => {
  const events = [];
  let requestUrl = "";
  const link = {
    click: () => events.push("clicked"),
    remove: () => events.push("removed"),
  };
  const { coordinator } = createHarness({
    fetchImpl: async (url) => {
      requestUrl = url;
      return { ok: true, blob: async () => ({}) };
    },
    documentRef: {
      createElement: () => link,
      body: { appendChild: () => events.push("appended") },
    },
    urlApi: {
      createObjectURL: () => "blob:test",
      revokeObjectURL: (url) => events.push(`revoked:${url}`),
    },
  });

  assert.equal(await coordinator.downloadSessionLog("session/a", "owner/a"), true);
  assert.equal(requestUrl, "/api/sessions/session%2Fa/session-log?user_id=owner%2Fa");
  assert.equal(link.href, "blob:test");
  assert.deepEqual(events, ["appended", "clicked", "removed", "revoked:blob:test"]);
});

test("idle remote-job polling reconnects a harness-started root run", async () => {
  const calls = [];
  const graphPolls = [];
  const { coordinator } = createHarness({
    state: { sessionReady: true },
    agentGraph: { reset() {}, startPolling: (sessionId) => graphPolls.push(["agent", sessionId]) },
    planGraph: { reset() {}, startPolling: (sessionId) => graphPolls.push(["plan", sessionId]) },
    getSessionRuntime: () => ({
      startManagedRunReconnect: (...args) => { calls.push(args); return { key: "attached" }; },
      loadSession: async () => { throw new Error("Active runs should stream, not reload"); },
    }),
  });
  const run = { run_id: "harness-run" };
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: run, activity_revision: "running-1",
  });
  assert.deepEqual(calls, [[run, "session-a", "user-a"]]);
  // The wakeup turn streams delegated-task progress via the graphs; attaching
  // must start their polling exactly as a composed turn would.
  assert.deepEqual(graphPolls, [["agent", "session-a"], ["plan", "session-a"]]);
});

test("a deferred wakeup attachment does not start graph polling", async () => {
  const graphPolls = [];
  const { coordinator } = createHarness({
    state: { sessionReady: true },
    agentGraph: { reset() {}, startPolling: (sessionId) => graphPolls.push(sessionId) },
    planGraph: { reset() {}, startPolling: (sessionId) => graphPolls.push(sessionId) },
    getSessionRuntime: () => ({
      startManagedRunReconnect: () => null,
      loadSession: async () => ({}),
    }),
  });
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: { run_id: "wakeup-run" }, activity_revision: "running-1",
  });
  assert.deepEqual(graphPolls, []);
});

test("session creation starts remote-job polling for the created session", async () => {
  const polls = [];
  const loads = [];
  const { coordinator } = createHarness({
    remoteJobsController: {
      reset() {},
      startPolling: (...args) => polls.push(args),
      load: async (...args) => loads.push(args),
    },
  });
  assert.equal(await coordinator.createSession(), true);
  assert.deepEqual(polls, [["session-a", "user-a"]]);
  assert.deepEqual(loads, [["session-a", "user-a"]]);
});

test("session creation does not start polling once the session changed", async () => {
  const polls = [];
  const gate = deferred();
  const { coordinator, state } = createHarness({
    fetchImpl: () => gate.promise,
    remoteJobsController: { reset() {}, startPolling: (...args) => polls.push(args), async load() {} },
  });
  const creation = coordinator.createSession();
  state.sessionId = "session-b";
  gate.resolve({ ok: true, status: 200 });
  assert.equal(await creation, false);
  assert.deepEqual(polls, []);
});

test("a stale terminal request does not block wakeup-run attachment", async () => {
  const calls = [];
  let loads = 0;
  const { coordinator, state } = createHarness({
    state: { sessionReady: true },
    getSessionRuntime: () => ({
      startManagedRunReconnect: (...args) => calls.push(args),
      loadSession: async () => { loads += 1; return {}; },
    }),
  });
  state.activeRequests.set("user-a:session-a", { running: false });
  const run = { run_id: "wakeup-run" };
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: run, activity_revision: "running-1",
  });
  assert.deepEqual(calls, [[run, "session-a", "user-a"]]);
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    activity_revision: "completed-1",
  });
  assert.equal(loads, 1);
});

test("a mid-handoff request defers wakeup attachment and history refresh", async () => {
  const calls = [];
  let loads = 0;
  const { coordinator, state } = createHarness({
    state: { sessionReady: true },
    getSessionRuntime: () => ({
      startManagedRunReconnect: (...args) => calls.push(args),
      loadSession: async () => { loads += 1; return {}; },
    }),
  });
  // Terminal backend run, but the streamed turn is still mounted in the DOM:
  // clearing or reloading now would destroy content that exists nowhere else.
  const midHandoff = { running: false, messageView: { element: { isConnected: true } } };
  state.activeRequests.set("user-a:session-a", midHandoff);
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: { run_id: "wakeup-run" }, activity_revision: "running-1",
  });
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    activity_revision: "completed-1",
  });
  assert.deepEqual(calls, []);
  assert.equal(loads, 0);

  // Handoff finished: the same poll payloads now attach and refresh.
  midHandoff.cleanupDone = true;
  const run = { run_id: "wakeup-run" };
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: run, activity_revision: "running-1",
  });
  assert.deepEqual(calls, [[run, "session-a", "user-a"]]);
  state.activeRequests.clear();
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    activity_revision: "completed-1",
  });
  assert.equal(loads, 1);
});

test("a presenting request found via activeSessionRequest also defers wakeup handling", async () => {
  const calls = [];
  let loads = 0;
  const { coordinator } = createHarness({
    state: { sessionReady: true },
    // Owner-key normalization can park the presenting request under another
    // map key; the active-session accessor still exposes it.
    activeSessionRequest: () => ({ running: false, userMessage: { isConnected: true } }),
    getSessionRuntime: () => ({
      startManagedRunReconnect: (...args) => calls.push(args),
      loadSession: async () => { loads += 1; return {}; },
    }),
  });
  await coordinator.observeRemoteJobActivity("session-a", "user-a", {
    active_run: { run_id: "wakeup-run" }, activity_revision: "running-1",
  });
  assert.deepEqual(calls, []);
  assert.equal(loads, 0);
});

test("a background turn completed between polls reloads history exactly once", async () => {
  let loads = 0;
  const { coordinator } = createHarness({
    state: { sessionReady: true },
    getSessionRuntime: () => ({
      loadSession: async () => { loads += 1; return {}; },
    }),
  });
  for (const revision of ["delivered", "delivered", "completed", "completed"]) {
    await coordinator.observeRemoteJobActivity("session-a", "user-a", { activity_revision: revision });
  }
  assert.equal(loads, 2);
  });

test("background activity respects session ownership, local work, and teardown", async () => {
  let loads = 0;
  const { coordinator, state } = createHarness({
    state: { sessionReady: true },
    getSessionRuntime: () => ({ loadSession: async () => { loads += 1; return {}; } }),
  });
  const activity = { activity_revision: "completed" };
  await coordinator.observeRemoteJobActivity("session-a", "user-b", activity);
  await coordinator.observeRemoteJobActivity("session-b", "user-a", activity);
  state.activeRequests.set("user-a:session-a", { running: true });
  await coordinator.observeRemoteJobActivity("session-a", "user-a", activity);
  assert.equal(loads, 0);
  state.activeRequests.clear();
  await coordinator.observeRemoteJobActivity("session-a", "user-a", activity);
  coordinator.destroy();
  await coordinator.observeRemoteJobActivity("session-a", "user-a", { activity_revision: "later" });
  assert.equal(loads, 1);
  });

test("overlapping background polls coalesce and failed snapshot reads retry", async () => {
  const gate = deferred();
  let loads = 0;
  const { coordinator } = createHarness({
    state: { sessionReady: true },
    getSessionRuntime: () => ({ loadSession: async () => {
      loads += 1;
      return loads === 1 ? gate.promise : {};
    } }),
  });
  const activity = { activity_revision: "completed" };
  const first = coordinator.observeRemoteJobActivity("session-a", "user-a", activity);
  await coordinator.observeRemoteJobActivity("session-a", "user-a", activity);
  assert.equal(loads, 1);
  gate.resolve(null);
  await first;
  await coordinator.observeRemoteJobActivity("session-a", "user-a", activity);
  assert.equal(loads, 2);
});
