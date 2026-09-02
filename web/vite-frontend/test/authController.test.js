import assert from "node:assert/strict";
import test from "node:test";

import {
  isUuid,
  isValidIdentity,
  validatedStoredSession,
} from "../src/features/auth/AuthController.js";

test("identity validation accepts only local user and UUID identities", () => {
  assert.equal(isValidIdentity("user"), true);
  assert.equal(isUuid("1d8b87de-22c2-4e4e-84e1-8ded12be3400"), true);
  assert.equal(isValidIdentity("1d8b87de-22c2-4e4e-84e1-8ded12be3400"), true);
  assert.equal(isValidIdentity("legacy-display-name"), false);
});

test("stored session validation keeps ordinary users inside their ownership boundary", () => {
  const sessions = [{ id: "session-a", userId: "user-a", status: "running" }];
  const common = {
    sessionId: "session-a",
    userId: "user-a",
    deploymentMode: "server",
    isAdmin: false,
  };

  assert.deepEqual(validatedStoredSession(sessions, { ...common, storedOwner: "user-a" }), {
    sessionId: "session-a",
    owner: "user-a",
    knownRunning: true,
    knownRun: null,
  });
  assert.equal(
    validatedStoredSession(sessions, { ...common, storedOwner: "user-b" }),
    null,
  );
});

test("admin session restoration requires an explicit matching owner", () => {
  const sessions = [{
    id: "session-a",
    userId: "user-b",
    phase: "idle",
    activeRun: { id: "run-a" },
  }];
  const common = {
    sessionId: "session-a",
    userId: "admin-a",
    deploymentMode: "server",
    isAdmin: true,
  };

  assert.equal(validatedStoredSession(sessions, { ...common, storedOwner: "" }), null);
  assert.deepEqual(validatedStoredSession(sessions, { ...common, storedOwner: "user-b" }), {
    sessionId: "session-a",
    owner: "user-b",
    knownRunning: false,
    knownRun: { id: "run-a" },
  });
});
