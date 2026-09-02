import assert from "node:assert/strict";
import test from "node:test";

import { normalizeAgentMode } from "../src/features/chat/ComposerModeController.js";

test("normalizeAgentMode limits persisted and DOM modes to supported values", () => {
  assert.equal(normalizeAgentMode("flash"), "flash");
  assert.equal(normalizeAgentMode("normal"), "normal");
  assert.equal(normalizeAgentMode("bench"), "bench");
  assert.equal(normalizeAgentMode("unknown"), "normal");
  assert.equal(normalizeAgentMode(null), "normal");
});
