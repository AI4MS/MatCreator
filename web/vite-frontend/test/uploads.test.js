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

test('folder uploads send relative paths and preserve attachment context', async () => {
  const { createSessionUploadsController } = await import('../src/features/session/uploads.js');
  const file = new Blob(['structure']);
  Object.defineProperty(file, 'name', { value: 'original.extxyz' });
  Object.defineProperty(file, 'webkitRelativePath', { value: 'retest/151_Li3SCl/original.extxyz' });
  const state = { userId: 'user', sessionId: 'session', sessionReady: true, currentUploads: [] };
  const controller = createSessionUploadsController({
    state, canWrite: () => true,
    fetchImpl: async (_url, options) => {
      assert.equal(options.body.get('relative_path'), file.webkitRelativePath);
      assert.equal(await options.body.get('file').text(), 'structure');
      return { ok: true, json: async () => ({ name: file.name, path: '/work/uploads/' + file.webkitRelativePath, relative_path: 'uploads/' + file.webkitRelativePath }) };
    },
  });
  await controller.upload([file]);
  assert.equal(state.currentUploads.length, 1);
  assert.match(controller.messageWithUploadContext('Analyze', state.currentUploads), /uploads\/retest\/151_Li3SCl\/original.extxyz/);
});

test('uploaded folders group nested files without mixing identical filenames', async () => {
  const { buildUploadTree } = await import('../src/features/session/uploads.js');
  const first = { name: 'original.extxyz', relative_path: 'uploads/retest/151/original.extxyz' };
  const second = { name: 'original.extxyz', relative_path: 'uploads/retest/156/original.extxyz' };
  const plain = { name: 'notes.txt', relative_path: 'uploads/notes.txt' };
  const tree = buildUploadTree([first, second, plain]);
  assert.deepEqual(tree.files, [plain]);
  assert.deepEqual([...tree.folders.keys()], ['retest']);
  assert.deepEqual(tree.folders.get('retest').folders.get('151').files, [first]);
  assert.deepEqual(tree.folders.get('retest').folders.get('156').files, [second]);
});
