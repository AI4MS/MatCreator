import assert from "node:assert/strict";
import test from "node:test";

import { knowledgeReviewPresentation } from "../src/features/skills/KnowledgeReviewController.js";

test("knowledge review presentation normalizes unknown status", () => {
  assert.deepEqual(knowledgeReviewPresentation({ status: "unexpected" }), {
    status: "idle",
    running: false,
    title: "Click to review memory and graph nodes",
    text: "Review memory and graph · click to start",
  });
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
