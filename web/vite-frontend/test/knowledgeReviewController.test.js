import assert from "node:assert/strict";
import test from "node:test";

import { createKnowledgeReviewController, knowledgeReviewPresentation } from "../src/features/skills/KnowledgeReviewController.js";

test("knowledge review presentation normalizes unknown status", () => {
  assert.deepEqual(knowledgeReviewPresentation({ status: "unexpected" }), {
    status: "idle",
    running: false,
    title: "Click to review memory and graph nodes",
    text: "Review memory and graph · click to start",
  });
});

test("knowledge review does not fetch or execute while the local instance is locked", async () => {
  let unlocked = false;
  let requests = 0;
  const node = () => ({ setAttribute() {}, append() {}, addEventListener() {}, removeEventListener() {}, classList: { toggle() {} } });
  const controller = createKnowledgeReviewController({
    getSessionId: () => "session-a",
    canAccess: () => unlocked,
    documentRef: { createElement: node },
    windowRef: { clearInterval() {}, setInterval() {} },
    fetchImpl: async () => { requests++; return { ok: true, json: async () => ({ status: "idle" }) }; },
  });
  controller.init();
  await controller.refresh();
  assert.equal(await controller.start(), false);
  assert.equal(requests, 0);
  unlocked = true;
  await controller.refresh();
  assert.equal(requests, 1);
  controller.destroy();
});

test("knowledge review presentation reports progress and partial failures", () => {
  assert.equal(knowledgeReviewPresentation({
    status: "running",
    phase: "graph",
    progress: { completed: 3, total: 5, percent: 60 },
  }).text, "Reviewing graph nodes: 3/5 (60%)");

  const completed = knowledgeReviewPresentation({
    status: "completed_with_errors",
    results: [{ phase: "memory" }, { phase: "graph" }],
    errors: ["one warning"],
  });
  assert.equal(completed.status, "completed_with_errors");
  assert.equal(completed.text, "Review complete: 1 memory, 1 graph actions, 1 error · click to run again");
});
