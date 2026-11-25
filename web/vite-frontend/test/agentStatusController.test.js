import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseHTML } from "linkedom";

import { createAgentStatusController } from "../src/features/chat/AgentStatusController.js";

test("composer preserves its original glyphs while switching between Send, Stop and queued", () => {
  const { document } = parseHTML(readFileSync(new URL("../index.html", import.meta.url), "utf8"));
  const button = document.getElementById("send-btn");
  function assertOriginalGlyphs() {
    for (const [kind, glyph] of [["send", "➜"], ["stop", "■"], ["queued", "…"]]) {
      const icon = button.querySelector(`.send-icon-${kind}`);
      assert.ok(icon);
      assert.equal(icon.textContent, glyph);
      assert.equal(icon.getAttribute("aria-hidden"), "true");
    }
  }
  assertOriginalGlyphs();

  let request;
  const controller = createAgentStatusController({
    chatArea: document,
    sendButton: button,
    getActiveRequest: () => request,
    requestHasActiveRun: (value) => Boolean(value?.running),
    requestPresentsLiveTurn: () => false,
  });

  for (const [next, label, stopping, queued] of [
    [undefined, "Send", false, false],
    [{ running: true }, "Stop", true, false],
    [{ running: true, followupQueued: true }, "Sending after message sync…", true, true],
    [undefined, "Send", false, false],
  ]) {
    request = next;
    controller.updateSendButton();
    assertOriginalGlyphs();
    assert.equal(button.getAttribute("aria-label"), label);
    assert.equal(button.classList.contains("is-stopping"), stopping);
    assert.equal(button.classList.contains("is-finalizing"), queued);
    assert.equal(button.disabled, queued);
  }
});
