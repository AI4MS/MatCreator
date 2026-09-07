import assert from "node:assert/strict";
import test from "node:test";

import { relativePathForFile } from "../src/features/session/fileTree.js";

test("uses the API relative path even when a filename contains the session id", () => {
  const files = [
    { path: "/workspace/cancellation/session-123.flag", relative_path: "cancellation/session-123.flag" },
    { path: "/workspace/results/final.cif", relative_path: "results/final.cif" },
  ];

  assert.equal(relativePathForFile(files, files[0]), "cancellation/session-123.flag");
  assert.equal(relativePathForFile(files, files[1]), "results/final.cif");
});

test("falls back to the literal common directory prefix for legacy responses", () => {
  const files = [
    { path: "/workspace/cancellation/session-123.flag" },
    { path: "/workspace/results/final.cif" },
  ];

  assert.equal(relativePathForFile(files, files[0]), "cancellation/session-123.flag");
  assert.equal(relativePathForFile(files, files[1]), "results/final.cif");
});
