import assert from "node:assert/strict";
import test from "node:test";

import { appendRunFailure, runFailureMarkdown } from "../src/features/chat/runFailure.js";

test("run failures are appended to an assistant message exactly once", () => {
  const message = { id: "assistant-1", items: [], revision: 0 };

  assert.equal(appendRunFailure(message, new Error("JSONDecodeError: broken arguments")), true);
  assert.equal(appendRunFailure(message, "a later duplicate"), false);
  assert.equal(message.items.length, 1);
  assert.equal(message.items[0].runFailure, true);
  assert.match(message.items[0].text, /JSONDecodeError: broken arguments/);
  assert.match(message.items[0].text, /isolated to this chat/);
  assert.equal(message.revision, 1);
});

test("run failure copy strips the JavaScript Error prefix", () => {
  assert.doesNotMatch(runFailureMarkdown(new Error("HTTP 502")), /Error: HTTP 502/);
  assert.match(runFailureMarkdown(new Error("HTTP 502")), /HTTP 502/);
});
