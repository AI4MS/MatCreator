import assert from "node:assert/strict";
import test from "node:test";

import { TranscriptStore } from "../src/features/session/TranscriptStore.js";
import {
  latestConversationTurn,
  managedRecoverySource,
  shouldShowApproval,
} from "../src/features/session/runtime.js";

function page(start, total, events) {
  return {
    events,
    event_meta: events.map((event, offset) => ({
      index: start + offset,
      cursor: `cursor-${start + offset}`,
      turn_id: event.turnId || `turn-${start + offset}`,
    })),
    pagination: {
      start_index: start,
      end_index: start + events.length,
      total_count: total,
    },
    revision: `revision-${total}`,
  };
}

test("represents unloaded history as stable logical gaps", () => {
  const store = new TranscriptStore();
  store.insertPage(page(998, 1000, [
    { id: "user-last", author: "user", turnId: "last", content: { parts: [{ text: "question" }] } },
    { id: "agent-last", author: "agent", turnId: "last", content: { parts: [{ text: "answer" }] } },
  ]));

  const rows = store.rows();
  assert.equal(rows[0].type, "gap");
  assert.equal(rows[0].span, 998);
  assert.equal(rows.at(-1).id, "assistant:last");
  assert.ok(store.estimateRow(rows[0]) > 100_000);
});

test("keeps existing transcript row IDs when an earlier page arrives", () => {
  const store = new TranscriptStore();
  store.insertPage(page(4, 8, [
    { id: "user-b", author: "user", turnId: "b", content: { parts: [{ text: "b" }] } },
    { id: "agent-b-1", author: "agent", turnId: "b", content: { parts: [{ text: "one" }] } },
    { id: "agent-b-2", author: "agent", turnId: "b", content: { parts: [{ text: "two" }] } },
    { id: "agent-b-3", author: "agent", turnId: "b", content: { parts: [{ text: "three" }] } },
  ]));
  const existingIds = store.rows().filter((row) => row.type !== "gap").map((row) => row.id);

  store.insertPage(page(0, 8, [
    { id: "user-a", author: "user", turnId: "a", content: { parts: [{ text: "a" }] } },
    { id: "agent-a-1", author: "agent", turnId: "a", content: { parts: [{ text: "one" }] } },
    { id: "agent-a-2", author: "agent", turnId: "a", content: { parts: [{ text: "two" }] } },
    { id: "agent-a-3", author: "agent", turnId: "a", content: { parts: [{ text: "three" }] } },
  ]));

  const nextIds = new Set(store.rows().map((row) => row.id));
  existingIds.forEach((id) => assert.ok(nextIds.has(id), `${id} should retain identity`));
});

test("bounds cached event pages independently of logical transcript length", () => {
  const store = new TranscriptStore({ pageLimit: 2 });
  store.insertPage(page(0, 120, [{ id: "0", author: "user" }]));
  store.insertPage(page(40, 120, [{ id: "40", author: "user" }]));
  store.setFocusIndex(80);
  store.insertPage(page(80, 120, [{ id: "80", author: "user" }]));

  assert.equal(store.pages.size, 2);
  assert.equal(store.totalCount, 120);
  assert.ok(store.rows().some((row) => row.type === "gap"));
});

test("reconnect rehydrates the newest pending user prompt before the live agent bubble", () => {
  const events = [
    { author: "user", content: { parts: [{ text: "first prompt" }] } },
    { author: "agent", content: { parts: [{ text: "first answer" }] } },
    { author: "user", content: { parts: [{ text: "second prompt" }] } },
    { author: "agent", content: { parts: [{ text: "partial second answer" }] } },
  ];

  const turn = latestConversationTurn(events);
  assert.equal(turn.userEvent.content.parts[0].text, "second prompt");
  assert.deepEqual(turn.assistantEvents.map((event) => event.content.parts[0].text), ["partial second answer"]);
});

test("reconnect hydration never borrows assistant output from an earlier turn", () => {
  const { assistantEvents } = latestConversationTurn([
    { author: "agent", content: { parts: [{ text: "orphaned old answer" }] } },
    { author: "user", content: { parts: [{ text: "pending prompt" }] } },
  ]);
  assert.deepEqual(assistantEvents, []);
});

test("managed recovery uses one authoritative reconstruction source", () => {
  assert.equal(managedRecoverySource({ earliest_sequence: 1, latest_sequence: 18 }), "replay");
  assert.equal(managedRecoverySource({ earliest_sequence: 0, latest_sequence: 0 }), "replay");
  assert.equal(managedRecoverySource({ latest_sequence: 18 }), "replay");
  assert.equal(managedRecoverySource({ earliest_sequence: 7, latest_sequence: 18 }), "snapshot");
});

test("suppressed approval prompts do not reappear when the final transcript row is rerendered", () => {
  const events = [
    { author: "user", content: { parts: [{ text: "Please plan this for me" }] } },
    { author: "agent", content: { parts: [{ functionCall: { name: "validate_plan", args: {}, }, }, { functionResponse: { name: "validate_plan", response: { status: "ok" } } }] } },
  ];

  const renderable = shouldShowApproval(
    "session-1",
    { state: { agent_mode: "normal" } },
    events,
    {
      suppressedPlanApprovalTurns: new Map([["session-1", { userText: "" }]]),
    },
  );

  assert.equal(renderable, false);
});

test("a newly validated plan can replace the prompt suppressed by its own user turn", () => {
  const text = "Please revise the plan";
  const events = [
    { author: "user", content: { parts: [{ text }] } },
    { author: "agent", content: { parts: [{ functionResponse: { name: "validate_plan", response: { status: "ok" } } }] } },
  ];
  assert.equal(shouldShowApproval("session-2", { state: { agent_mode: "normal" } }, events, {
    suppressedPlanApprovalTurns: new Map([["session-2", { userText: text }]]),
  }), true);
});
