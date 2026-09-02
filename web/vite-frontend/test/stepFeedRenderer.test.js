import assert from "node:assert/strict";
import test from "node:test";

import {
  formatStepDuration,
  stepFeedStatusIcon,
  stepFeedTitle,
} from "../src/features/chat/stepFeedRenderer.js";
import {
  compareStepAttempts,
  StepExecutionFeed,
} from "../src/features/graphs/StepExecutionFeed.js";

function stepCard(node) {
  return {
    _stepNode: node,
    removed: false,
    classList: { contains: (name) => name === "step-feed-message" },
    remove() { this.removed = true; },
  };
}

test("formatStepDuration handles running, completed, and invalid timestamps", () => {
  const startedAt = "2026-01-01T00:00:00.000Z";
  const now = new Date("2026-01-01T01:02:03.000Z").getTime();
  assert.equal(formatStepDuration({ start_time: startedAt }, now), "1:02:03");
  assert.equal(formatStepDuration({
    start_time: startedAt,
    end_time: "2026-01-01T00:00:02.250Z",
  }), "2.3s");
  assert.equal(formatStepDuration({ start_time: "invalid" }, now), "—");
});

test("stepFeedTitle separates a readable action from its durable identifier", () => {
  assert.deepEqual(stepFeedTitle({
    id: "node-fallback",
    input: { action: "Relax the structure", node_id: "relax-1" },
  }), {
    action: "Relax the structure",
    identifier: "relax-1",
  });
});

test("step status icons distinguish replanning from successful completion", () => {
  assert.equal(stepFeedStatusIcon("needs_replanning"), "↻");
  assert.equal(stepFeedStatusIcon("success"), "✓");
  assert.equal(stepFeedStatusIcon("running"), "◌");
  assert.equal(stepFeedStatusIcon("failed"), "!");
  assert.equal(stepFeedStatusIcon("idle"), "•");
});

test("step attempts sort by start time and then durable execution sequence", () => {
  const older = { id: "execution_2__node_step-a", start_time: "2026-01-01T00:00:00Z" };
  const newer = { id: "execution_3__node_step-a", start_time: "2026-01-01T01:00:00Z" };
  assert.ok(compareStepAttempts(newer, older) > 0);
  assert.ok(compareStepAttempts(older, newer) < 0);
  assert.ok(compareStepAttempts(
    { id: "execution_10__node_step-a" },
    { id: "execution_9__node_step-a" },
  ) > 0);
  assert.ok(compareStepAttempts(
    { id: "execution_4__node_step-a" },
    { id: "execution_3__node_step-a", start_time: "2026-01-01T01:00:00Z" },
  ) > 0);
});

test("a delegated task host keeps only its newest execution attempt", () => {
  const feed = Object.create(StepExecutionFeed.prototype);
  const olderNode = { id: "execution_2__node_step-a", start_time: "2026-01-01T00:00:00Z" };
  const newerNode = { id: "execution_3__node_step-a", start_time: "2026-01-01T01:00:00Z" };
  const olderCard = stepCard(olderNode);
  const newerCard = stepCard(newerNode);
  const container = {
    classList: { contains: (name) => name === "delegation-task-host" },
    children: [olderCard],
  };

  assert.equal(feed._reconcileDelegationAttempt(container, newerCard, newerNode), true);
  assert.equal(olderCard.removed, true);

  container.children = [newerCard];
  assert.equal(feed._reconcileDelegationAttempt(container, olderCard, olderNode), false);
  assert.equal(olderCard.removed, true);
  assert.equal(newerCard.removed, false);
});
