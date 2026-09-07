import assert from "node:assert/strict";
import test from "node:test";

import {
  structureTabId,
  structureTabTitle,
} from "../src/features/workspace/WorkspaceViewerController.js";

test("structure tab identity is deterministic and path-sensitive", () => {
  assert.equal(structureTabId("/work/a.cif"), structureTabId("/work/a.cif"));
  assert.notEqual(structureTabId("/work/a.cif"), structureTabId("/work/b.cif"));
});

test("structure tab title supports POSIX and Windows paths", () => {
  assert.equal(structureTabTitle("/work/result.cif"), "result.cif");
  assert.equal(structureTabTitle("C:\\work\\result.xyz"), "result.xyz");
  assert.equal(structureTabTitle(""), "Structure");
});
