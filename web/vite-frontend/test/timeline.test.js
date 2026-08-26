import assert from "node:assert/strict";
import test from "node:test";

import {
  deduplicateDelegationToolCalls,
  mergeReplayedText,
  upsertTimelineEvent,
  upsertTimelineText,
  upsertTimelineThought,
} from "../src/features/chat/timeline.js";
import {
  activityToolCalls,
  delegationToolCalls,
  getFunctionResponse,
  getPlotPaths,
  getStructurePaths,
} from "../src/features/chat/timelinePresentation.js";

test("normalizes timeline protocol variants and artifact paths", () => {
  assert.deepEqual(getFunctionResponse({ function_response: { id: "response-1" } }), { id: "response-1" });
  assert.deepEqual(getPlotPaths({ plot_path: "plot.png", plot_paths: ["plot.png", "energy.png"] }), ["plot.png", "energy.png"]);
  assert.deepEqual(getStructurePaths({
    artifacts: ["plots/energy.png", "structures/optimized.cif"],
    nested: { structure_paths: ["structures/optimized.cif", "structures/initial.xyz"] },
  }), ["structures/optimized.cif", "structures/initial.xyz"]);
});

test("separates delegated executor tools from regular activity tools", () => {
  const action = {
    type: "activity_action",
    toolCalls: [
      { id: "regular", name: "read_file" },
      { id: "executor", name: "run_node_executor", input: { node_id: "relax" } },
    ],
  };
  assert.deepEqual(activityToolCalls(action).map((call) => call.id), ["regular"]);
  assert.deepEqual(delegationToolCalls([action]).map((call) => call.id), ["executor"]);
});

test("merges streaming snapshots without repeating replayed content", () => {
  assert.equal(mergeReplayedText("hello", "hello world"), "hello world");
  assert.equal(mergeReplayedText("hello world", "world"), "hello world");
  assert.equal(mergeReplayedText("abcde", "defgh"), "abcdefgh");
});

test("keeps text and reasoning as separate chronological entries", () => {
  const timeline = [];
  upsertTimelineText(timeline, "Answer");
  upsertTimelineText(timeline, "Answer continued");
  upsertTimelineThought(timeline, "Checking inputs");
  upsertTimelineText(timeline, "Final result");

  assert.deepEqual(timeline.map(({ type, text }) => ({ type, text })), [
    { type: "text", text: "Answer continued" },
    { type: "reasoning", text: "Checking inputs" },
    { type: "text", text: "Final result" },
  ]);
  assert.equal(new Set(timeline.map((item) => item.timelineId)).size, 3);
});

test("pairs a tool response with its invocation and derives presentation state", () => {
  const timeline = [];
  const action = upsertTimelineEvent(timeline, {
    type: "function_call",
    id: "call-1",
    name: "run_node_executor",
    args: { node_id: "relax-structure" },
  });

  assert.equal(action.status, "running");
  assert.equal(action.toolCalls[0].input.node_id, "relax-structure");

  const updated = upsertTimelineEvent(timeline, {
    type: "function_response",
    id: "call-1",
    name: "run_node_executor",
    response: { duration_ms: 24, result: "ok" },
  });

  assert.strictEqual(updated, action);
  assert.equal(timeline.length, 1);
  assert.equal(action.status, "success");
  assert.equal(action.title, "relax-structure completed");
  assert.equal(action.durationMs, 24);
  assert.equal(action.toolCalls[0].output.result, "ok");
});

test("groups calls that share a backend action id", () => {
  const timeline = [];
  upsertTimelineEvent(timeline, {
    type: "function_call",
    id: "call-1",
    action_id: "action-1",
    name: "validate_plan",
    args: {},
  });
  upsertTimelineEvent(timeline, {
    type: "function_call",
    id: "call-2",
    action_id: "action-1",
    name: "get_ready_nodes",
    args: {},
  });

  assert.equal(timeline.length, 1);
  assert.equal(timeline[0].toolCalls.length, 2);
  assert.equal(timeline[0].backendActionId, "action-1");
});

test("merges replayed executor calls that have a new transient call id", () => {
  const timeline = [];
  upsertTimelineEvent(timeline, {
    type: "function_call",
    id: "call-original",
    name: "run_node_executor",
    args: { node_id: "relax-structure", action: "Relax the structure" },
  });
  upsertTimelineEvent(timeline, {
    type: "function_call",
    id: "call-replayed",
    name: "run_node_executor",
    args: { node_id: "relax-structure", action: "Relax the structure" },
  });
  upsertTimelineEvent(timeline, {
    type: "function_response",
    id: "provider-response-id",
    name: "run_node_executor",
    response: { status: "success" },
  });

  const calls = timeline.flatMap((item) => item.toolCalls || []);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].id, "call-original");
  assert.equal(calls[0].status, "success");
});

test("deduplicates delegated-task cards by their durable executor identity", () => {
  const calls = deduplicateDelegationToolCalls([
    { id: "stream-1", name: "run_node_executor", input: { node_id: "node-a" }, status: "running" },
    { id: "stream-2", name: "run_node_executor", input: { node_id: "node-a" }, status: "running" },
    { id: "stream-3", name: "run_node_executor", input: { node_id: "node-b" }, status: "running" },
  ]);

  assert.deepEqual(calls.map((call) => call.input.node_id), ["node-a", "node-b"]);
});
