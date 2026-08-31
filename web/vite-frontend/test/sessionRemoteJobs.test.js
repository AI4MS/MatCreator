import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { activateRemoteJobsSession } from "../src/features/session/remoteJobsSession.js";

const mainJsUrl = new URL("../src/main.js", import.meta.url);

test("newly created sessions wire Remote Jobs before createSession completes", async () => {
  const source = await readFile(mainJsUrl, "utf8");
  const createSessionSource = source.slice(
    source.indexOf("async function createSession()"),
    source.indexOf("function renderKnowledgeReviewStatus"),
  );

  assert.match(createSessionSource, /const owner = state\.userId/);
  assert.match(createSessionSource, /activateRemoteJobsSession\(\{[\s\S]*?controller: remoteJobsController,[\s\S]*?sessionId,[\s\S]*?owner,[\s\S]*?\}\)/);
  assert.match(createSessionSource, /await Promise\.all\(\[/);
});

test("activates polling and performs an initial load for the active session", async () => {
  const calls = [];
  const state = {
    sessionId: "session-new",
    activeSessionUserId: "owner-a",
  };
  const controller = {
    startPolling(sessionId, owner) {
      calls.push(["poll", sessionId, owner]);
    },
    async load(sessionId, owner) {
      calls.push(["load", sessionId, owner]);
    },
  };

  const activated = await activateRemoteJobsSession({
    state,
    controller,
    sessionId: "session-new",
    owner: "owner-a",
  });

  assert.equal(activated, true);
  assert.deepEqual(calls, [
    ["poll", "session-new", "owner-a"],
    ["load", "session-new", "owner-a"],
  ]);
});

test("does not attach a stale session or another owner's job stream", async () => {
  const calls = [];
  const state = {
    sessionId: "session-current",
    activeSessionUserId: "owner-a",
  };
  const controller = {
    startPolling(...args) { calls.push(["poll", ...args]); },
    async load(...args) { calls.push(["load", ...args]); },
  };

  assert.equal(await activateRemoteJobsSession({
    state,
    controller,
    sessionId: "session-old",
    owner: "owner-a",
  }), false);
  assert.equal(await activateRemoteJobsSession({
    state,
    controller,
    sessionId: "session-current",
    owner: "owner-b",
  }), false);
  assert.deepEqual(calls, []);
});

test("reports inactive if the selected session changes during the initial load", async () => {
  const state = {
    sessionId: "session-new",
    activeSessionUserId: "owner-a",
  };
  const controller = {
    startPolling() {},
    async load() {
      state.sessionId = "session-selected-later";
    },
  };

  assert.equal(await activateRemoteJobsSession({
    state,
    controller,
    sessionId: "session-new",
    owner: "owner-a",
  }), false);
});
