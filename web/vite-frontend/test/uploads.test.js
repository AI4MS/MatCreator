import assert from "node:assert/strict";
import test from "node:test";

import {
  displayMessageFromStoredUserText,
  formatUploadNames,
  mergeUploadedFiles,
  messageWithUploadContext,
  messageWithUploadNames,
  sessionRelativeUploadPath,
} from "../src/features/session/uploads.js";

test("mergeUploadedFiles preserves order and de-duplicates stable paths", () => {
  const existing = [{ name: "first.cif", path: "/work/first.cif" }];
  const duplicate = { name: "renamed.cif", path: "/work/first.cif" };
  const second = { name: "second.cif", path: "/work/second.cif" };

  assert.deepEqual(mergeUploadedFiles(existing, [duplicate, second]), [existing[0], second]);
  assert.deepEqual(existing, [{ name: "first.cif", path: "/work/first.cif" }]);
});

test("sessionRelativeUploadPath handles POSIX and Windows workspace paths", () => {
  assert.equal(
    sessionRelativeUploadPath(
      { name: "input.cif", path: "/tmp/session-123/uploads/input.cif" },
      "session-123",
    ),
    "uploads/input.cif",
  );
  assert.equal(
    sessionRelativeUploadPath(
      { name: "input.cif", path: "C:\\work\\session-123\\uploads\\input.cif" },
      "session-123",
    ),
    "uploads/input.cif",
  );
  assert.equal(
    sessionRelativeUploadPath({ name: "input.cif", path: "/another/input.cif" }, "session-123"),
    "uploads/input.cif",
  );
});

test("upload context keeps machine paths out of the visible stored message", () => {
  const uploads = [{
    name: "structure.cif",
    path: "/tmp/session-123/uploads/structure.cif",
  }];
  const stored = messageWithUploadContext("Inspect this", uploads, "session-123");

  assert.match(stored, /absolute path: \/tmp\/session-123\/uploads\/structure\.cif/);
  assert.equal(
    displayMessageFromStoredUserText(stored),
    "Inspect this\n\nAttached: `structure.cif`",
  );
});

test("upload display helpers handle empty and partial file metadata", () => {
  assert.equal(formatUploadNames([]), "");
  assert.equal(messageWithUploadNames("Hello", []), "Hello");
  assert.equal(
    messageWithUploadNames("Hello", [{ name: "a.txt" }, {}, null]),
    "Hello\n\nAttached: `a.txt`",
  );
});
