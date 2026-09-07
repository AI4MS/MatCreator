import assert from "node:assert/strict";
import test from "node:test";

import { applyGraphUpdate } from "../src/features/graphs/graphUpdates.js";

test("graph patches retain cold nodes and identify the hot update", () => {
  const cold = { id: "cold", type: "step", status: "success", conversation: [{ content: "large" }] };
  const hot = { id: "hot", type: "step", status: "running" };
  const initial = { nodes: { cold, hot }, edges: [{ from: "cold", to: "hot" }], updated_at: "one" };
  const nextHot = { ...hot, status: "success" };
  const result = applyGraphUpdate(initial, {
    delta: true,
    nodes: { hot: nextHot },
    removed_node_ids: [],
    updated_at: "two",
  });

  assert.strictEqual(result.graph.nodes.cold, cold);
  assert.strictEqual(result.graph.nodes.hot, nextHot);
  assert.deepEqual([...result.changedNodeIds], ["hot"]);
  assert.equal(result.layoutChanged, false);
  assert.strictEqual(result.graph.edges, initial.edges);
});

test("graph patches flag topology and layout changes", () => {
  const initial = { nodes: { a: { id: "a", label: "A" } }, edges: [] };
  const result = applyGraphUpdate(initial, {
    delta: true,
    layout_changed: true,
    nodes: { b: { id: "b", label: "B" } },
    edges: [{ from: "a", to: "b" }],
  });
  assert.equal(result.layoutChanged, true);
  assert.deepEqual(Object.keys(result.graph.nodes), ["a", "b"]);
});
