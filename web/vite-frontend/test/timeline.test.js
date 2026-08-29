import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAssistantMessageEvent,
  applyAssistantMessagePart,
  completeAssistantMessage,
  compactRepeatedPrefixSnapshots,
  createAssistantMessage,
  deduplicateDelegationToolCalls,
  mergeReplayedText,
  upsertTimelineEvent,
  upsertTimelineText,
  upsertTimelineThought,
} from "../src/features/chat/timeline.js";
import { performance } from "node:perf_hooks";
import {
  activityToolCalls,
  delegationToolCalls,
  getFunctionResponse,
  getPlotPaths,
  getStructurePaths,
  isDelegatedTaskRootTool,
  timelineSegments,
} from "../src/features/chat/timelinePresentation.js";

test("normalizes timeline protocol variants and artifact paths", () => {
  assert.deepEqual(getFunctionResponse({ function_response: { id: "response-1" } }), { id: "response-1" });
  assert.deepEqual(getPlotPaths({ plot_path: "plot.png", plot_paths: ["plot.png", "energy.png"] }), ["plot.png", "energy.png"]);
  assert.deepEqual(getStructurePaths({
    artifacts: ["plots/energy.png", "structures/optimized.cif"],
    nested: { structure_paths: ["structures/optimized.cif", "structures/initial.xyz"] },
  }), ["structures/optimized.cif", "structures/initial.xyz"]);
});

test("assistant message lifecycle has one deterministic completion owner", () => {
  const message = createAssistantMessage({ id: "assistant:test", startedAt: 10 });
  assert.equal(message.lifecycle, "created");

  applyAssistantMessagePart(message, { text: "# Heading" });
  applyAssistantMessagePart(message, {
    functionCall: { id: "tool-1", name: "read_file", args: { path: "a.md" } },
  });
  assert.equal(message.lifecycle, "streaming");
  assert.deepEqual(message.items.map((item) => item.type), ["text", "activity_action"]);

  assert.equal(completeAssistantMessage(message, 20), true);
  assert.equal(message.lifecycle, "completed");
  assert.equal(message.endedAt, 20);
  assert.equal(completeAssistantMessage(message, 30), false);
  assert.equal(applyAssistantMessagePart(message, { text: "late replay" }), null);
  assert.equal(message.endedAt, 20);
});

test("separates delegated executor tools from regular activity tools", () => {
  const action = {
    type: "activity_action",
    toolCalls: [
      { id: "regular", name: "read_file" },
      { id: "executor", name: "run_node_executor", input: { node_id: "relax" } },
      { id: "child", name: "run_sub_agent", input: { step_number: 1, action: "Optimize" } },
    ],
  };
  assert.deepEqual(activityToolCalls(action).map((call) => call.id), ["regular"]);
  assert.deepEqual(delegationToolCalls([action]).map((call) => call.id), ["executor"]);
  assert.equal(isDelegatedTaskRootTool("run_sub_agent"), false);
});

test("keeps each delegated task at its invocation position", () => {
  const delegatedAction = (id) => ({
    type: "activity_action",
    timelineId: `action:${id}`,
    renderRevision: 1,
    toolCalls: [{ id, name: "run_node_executor", input: { node_id: id } }],
  });
  const timeline = [
    { type: "text", timelineId: "text:intro", text: "Intro" },
    delegatedAction("first"),
    { type: "text", timelineId: "text:middle", text: "Code" },
    { type: "reasoning", timelineId: "reasoning:middle", text: "Checking" },
    delegatedAction("second"),
    { type: "text", timelineId: "text:final", text: "Done" },
  ];

  const segments = timelineSegments(timeline);
  assert.deepEqual(segments.map((segment) => segment.type), [
    "text", "delegation", "text", "activity", "delegation", "text",
  ]);
  assert.deepEqual(segments.filter((segment) => segment.type === "delegation")
    .map((segment) => segment.calls[0].input.node_id), ["first", "second"]);
});

test("merges streaming snapshots without repeating replayed content", () => {
  assert.equal(mergeReplayedText("hello", "hello world"), "hello world");
  assert.equal(mergeReplayedText("hello world", "world"), "hello world");
  assert.equal(mergeReplayedText("abcde", "defgh"), "abcdefgh");
  assert.equal(
    mergeReplayedText("hello world", "\nhello world again"),
    "hello world again",
  );
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

test("drops final ADK text replays after token-level partial events", () => {
  const message = createAssistantMessage({ id: "assistant:partial-replay" });
  const partials = [
    { author: "agent", partial: true, content: { parts: [{ text: "Working", thought: true }] } },
    { author: "agent", partial: true, content: { parts: [{ text: " on it", thought: true }] } },
    { author: "agent", partial: true, content: { parts: [{ text: "# Result" }] } },
  ];
  partials.forEach((event) => applyAssistantMessageEvent(message, event));

  applyAssistantMessageEvent(message, {
    author: "agent",
    partial: false,
    content: { parts: [
      { text: "Working", thought: true },
      { text: " on it", thought: true },
      { text: "# Result" },
      { functionCall: { id: "call-1", name: "read_file", args: {} } },
    ] },
  });

  assert.deepEqual(message.items.map((item) => item.type), ["reasoning", "text", "activity_action"]);
  assert.equal(message.items[0].text, "Working on it");
  assert.equal(message.items[1].text, "# Result");
});

test("keeps final visible text when only reasoning was streamed partially", () => {
  const message = createAssistantMessage({ id: "assistant:mixed-replay" });
  applyAssistantMessageEvent(message, {
    author: "agent", partial: true, content: { parts: [{ text: "Thinking", thought: true }] },
  });

  applyAssistantMessageEvent(message, {
    author: "agent", partial: false, content: { parts: [
      { text: "Thinking", thought: true },
      { text: "Final answer" },
    ] },
  });

  assert.deepEqual(message.items.map((item) => item.type), ["reasoning", "text"]);
  assert.equal(message.items[1].text, "Final answer");
});

test("adds only a missing final suffix after an interrupted partial stream", () => {
  const message = createAssistantMessage({ id: "assistant:interrupted-replay" });
  applyAssistantMessageEvent(message, {
    author: "agent", partial: true, content: { parts: [{ text: "Partial answer" }] },
  });

  applyAssistantMessageEvent(message, {
    author: "agent", partial: false, content: { parts: [{ text: "Partial answer completed" }] },
  });

  assert.equal(message.items[0].text, "Partial answer completed");
});

test("promotes a corrected thought stream to one Markdown slot", () => {
  const message = createAssistantMessage({ id: "assistant:corrected-kind" });
  applyAssistantMessageEvent(message, {
    author: "agent",
    partial: true,
    content: { parts: [{ text: "## Result\n\nValue: **42**", thought: true }] },
  });
  const originalSlot = message.items[0];

  applyAssistantMessageEvent(message, {
    author: "agent",
    partial: false,
    content: { parts: [{ text: "## Result\n\nValue: **42**" }] },
  });

  assert.equal(message.items.length, 1);
  assert.strictEqual(message.items[0], originalSlot);
  assert.equal(message.items[0].type, "text");
});

test("repartitions a provisional thought stream into reasoning and one Markdown answer", () => {
  const message = createAssistantMessage({ id: "assistant:repartitioned-thought" });
  applyAssistantMessageEvent(message, {
    id: "partial-output",
    author: "agent",
    partial: true,
    content: { parts: [
      { text: "Checking the calculation.\n\n", thought: true },
      { text: "## Result\n\nValue: **42**", thought: true },
    ] },
  });

  applyAssistantMessageEvent(message, {
    id: "final-output",
    author: "agent",
    partial: false,
    content: { parts: [
      { text: "Checking the calculation.\n\n", thought: true },
      { text: "## Result\n\nValue: **42**" },
      { functionCall: { id: "call-after-answer", name: "save_result", args: {} } },
    ] },
  });

  assert.deepEqual(message.items.map((item) => item.type), [
    "reasoning", "text", "activity_action",
  ]);
  assert.equal(message.items[0].text, "Checking the calculation.\n\n");
  assert.equal(message.items[1].text, "## Result\n\nValue: **42**");
  assert.equal(message.items.filter((item) => item.text?.includes("## Result")).length, 1);
});

test("does not lose a live text slot at an intervening tool-only event", () => {
  const message = createAssistantMessage({ id: "assistant:tool-boundary" });
  applyAssistantMessageEvent(message, {
    id: "partial-answer",
    author: "agent",
    partial: true,
    content: { parts: [{ text: "## Result\n\nValue: **42**" }] },
  });
  const originalTextSlot = message.items[0];

  applyAssistantMessageEvent(message, {
    id: "tool-only",
    author: "agent",
    partial: false,
    content: { parts: [
      { functionResponse: { id: "call-tool", name: "save_result", response: { status: "ok" } } },
    ] },
  });
  applyAssistantMessageEvent(message, {
    id: "final-answer",
    author: "agent",
    partial: false,
    content: { parts: [{ text: "## Result\n\nValue: **42**" }] },
  });

  const textItems = message.items.filter((item) => item.type === "text");
  assert.equal(textItems.length, 1);
  assert.strictEqual(textItems[0], originalTextSlot);
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

test("preserves parallel calls with the same tool name as distinct chronological tasks", () => {
  const message = createAssistantMessage({ id: "assistant:parallel-executors" });
  applyAssistantMessageEvent(message, {
    id: "activity-parallel-executors",
    author: "execution_orchestrator",
    partial: false,
    content: { parts: [
      { functionCall: { id: "call-a", name: "run_node_executor", args: { node_id: "step_parallel_a" } } },
      { functionCall: { id: "call-b", name: "run_node_executor", args: { node_id: "step_parallel_b" } } },
      { functionCall: { id: "call-c", name: "run_node_executor", args: { node_id: "step_parallel_c" } } },
    ] },
  });
  applyAssistantMessageEvent(message, {
    id: "activity-parallel-responses",
    author: "execution_orchestrator",
    partial: false,
    content: { parts: [
      { functionResponse: { id: "call-a", name: "run_node_executor", response: { status: "success" } } },
      { functionResponse: { id: "call-b", name: "run_node_executor", response: { status: "success" } } },
      { functionResponse: { id: "call-c", name: "run_node_executor", response: { status: "success" } } },
    ] },
  });

  const calls = message.items.flatMap((item) => item.toolCalls || []);
  assert.deepEqual(calls.map((call) => call.id), ["call-a", "call-b", "call-c"]);
  assert.deepEqual(calls.map((call) => call.input.node_id), [
    "step_parallel_a", "step_parallel_b", "step_parallel_c",
  ]);
  assert.ok(calls.every((call) => call.status === "success"));
  const segments = timelineSegments(message.items);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].type, "delegation");
  assert.deepEqual(
    segments[0].calls.map((call) => call.input.node_id),
    ["step_parallel_a", "step_parallel_b", "step_parallel_c"],
  );
});

test("groups parallel agents by activity without merging separate activities in one bubble", () => {
  const message = createAssistantMessage({ id: "assistant:activity-boundaries" });
  const dispatch = (id, nodes) => applyAssistantMessageEvent(message, {
    id,
    author: "execution_orchestrator",
    partial: false,
    content: { parts: nodes.map((node) => ({
      functionCall: { id: `call-${node}`, name: "run_node_executor", args: { node_id: node } },
    })) },
  });

  dispatch("activity-one", ["a", "b"]);
  applyAssistantMessageEvent(message, {
    id: "activity-text",
    author: "execution_orchestrator",
    partial: false,
    content: { parts: [{ text: "The next batch is now ready." }] },
  });
  dispatch("activity-two", ["c", "d"]);

  const segments = timelineSegments(message.items);
  assert.deepEqual(segments.map((segment) => segment.type), ["delegation", "text", "delegation"]);
  assert.deepEqual(
    segments.filter((segment) => segment.type === "delegation")
      .map((segment) => segment.calls.map((call) => call.input.node_id)),
    [["a", "b"], ["c", "d"]],
  );
});

test("keeps later calls in later chronological slots even when action ids match", () => {
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

  assert.equal(timeline.length, 2);
  assert.deepEqual(timeline.map((action) => action.toolCalls[0].name), ["validate_plan", "get_ready_nodes"]);
  assert.equal(timeline[0].backendActionId, "action-1");
  assert.equal(timeline[1].backendActionId, "action-1");
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

test("compacts replayed prefix snapshots without changing chronological text", () => {
  assert.equal(compactRepeatedPrefixSnapshots("abcdefghabcdefghTAIL"), "abcdefghTAIL");
  assert.equal(compactRepeatedPrefixSnapshots("ordinary streamed prose"), "ordinary streamed prose");
});

test("large timelines pair updates through incremental indexes", () => {
  const timeline = [];
  const callCount = 5_000;
  const startedAt = performance.now();
  for (let index = 0; index < callCount; index += 1) {
    upsertTimelineEvent(timeline, {
      type: "function_call",
      id: `stress-${index}`,
      name: "read_file",
      args: { index },
    });
  }
  for (let index = 0; index < callCount; index += 1) {
    upsertTimelineEvent(timeline, {
      type: "function_response",
      id: `stress-${index}`,
      name: "read_file",
      response: { status: "ok" },
    });
  }

  assert.equal(timeline.length, callCount);
  assert.ok(timeline.every((action) => action.status === "success"));
  // This is a deliberately generous regression ceiling. The indexed path is
  // normally well below 250ms; the former repeated filter/flatMap scan took
  // seconds and grew quadratically at this size.
  assert.ok(performance.now() - startedAt < 2_000);
});
