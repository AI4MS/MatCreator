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
      remoteJobsController: { reset() {} },
      agentGraph: { reset() {} },
      planGraph: { reset() {} },
      hidePlanGraph() {},
      clearDisclosures() {},
      renderSessionBanner() {},
      showConfirmDialog: async () => false,
      fetchImpl: async () => ({ ok: true, status: 200 }),
      ...overrides,
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
