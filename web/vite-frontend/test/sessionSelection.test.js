import assert from "node:assert/strict";
import test from "node:test";

import { validateStoredSessionSelection } from "../src/features/session/sessionSelection.js";

const legacyOwner = "a30feff7-bc8f-4ba8-a70e-c4a513c10f4f";
const sessions = [
  { id: "current", userId: "user" },
  { id: "legacy", userId: legacyOwner },
];

function validate(overrides = {}) {
  return validateStoredSessionSelection({
    sessions,
    sessionId: "legacy",
    storedOwner: legacyOwner,
    deploymentMode: "local",
    isAdmin: false,
    currentUserId: "user",
    ...overrides,
  });
}

test("local mode restores an accessible historical UUID-owner tuple", () => {
  assert.deepEqual(validate(), { sessionId: "legacy", owner: legacyOwner });
});

test("local mode rejects a saved owner that does not match the accessible tuple", () => {
  assert.equal(validate({ storedOwner: "wrong-owner" }), null);
});

test("ownerless legacy selections restore only when the owner is unambiguous", () => {
  assert.deepEqual(validate({ storedOwner: "" }), { sessionId: "legacy", owner: legacyOwner });
  assert.equal(validate({
    sessions: [
      { id: "legacy", userId: legacyOwner },
      { id: "legacy", userId: "another-owner" },
    ],
    storedOwner: "",
  }), null);
});

test("server non-admin restore stays confined to the signed-in owner", () => {
  assert.equal(validate({ deploymentMode: "server", currentUserId: "user" }), null);
  assert.deepEqual(validate({
    deploymentMode: "server",
    sessionId: "current",
    storedOwner: "user",
  }), { sessionId: "current", owner: "user" });
});

test("server admin restore requires an exact persisted owner", () => {
  assert.deepEqual(validate({ deploymentMode: "server", isAdmin: true }), {
    sessionId: "legacy",
    owner: legacyOwner,
  });
  assert.equal(validate({ deploymentMode: "server", isAdmin: true, storedOwner: "" }), null);
});
