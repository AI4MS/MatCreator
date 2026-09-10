import assert from "node:assert/strict";
import test from "node:test";

import {
  formatSkippedFilesNotice,
  relativePathForFile,
} from "../src/features/session/fileTree.js";

test("formats a notice only when session files were skipped", () => {
  assert.equal(formatSkippedFilesNotice(4090, 1), "已显示 4090 个文件，另有 1 个异常文件名被隐藏");
  assert.equal(formatSkippedFilesNotice(12, 0), "");
});

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
