import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  AGENT_GRAPH_SPLIT_BASE_FIXTURE,
  AGENT_GRAPH_SPLIT_CHILD_FIXTURE,
  AGENT_GRAPH_SPLIT_UPDATE_FIXTURE,
  AGENT_GRAPH_VISUAL_FIXTURE,
} from "../src/dev/agentGraphVisualFixture.js";

const agentGraphViewUrl = new URL("../src/features/graphs/AgentGraphView.js", import.meta.url);
const agentNodeGeometryUrl = new URL("../src/features/graphs/agentNodeGeometry.js", import.meta.url);
const agentNodeLiquidStyleUrl = new URL("../src/features/graphs/agentNodeLiquidStyle.js", import.meta.url);
const agentGraphRecipesUrl = new URL("../src/features/graphs/agentGraphRecipes.js", import.meta.url);

test("agent graph visual fixture covers node identities and lifecycle states", () => {
  const nodes = Object.values(AGENT_GRAPH_VISUAL_FIXTURE.nodes);
  const types = new Set(nodes.map((node) => node.type));
  const statuses = new Set(nodes.map((node) => node.status));

  assert.deepEqual(
    types,
    new Set(["orchestrator", "planning", "execution", "tester", "step"]),
  );
  for (const status of ["running", "success", "failed", "waiting", "cancelled", "needs_replanning"]) {
    assert.ok(statuses.has(status), `missing ${status} fixture state`);
  }

  nodes.forEach((node) => assert.equal(AGENT_GRAPH_VISUAL_FIXTURE.nodes[node.id], node));
  AGENT_GRAPH_VISUAL_FIXTURE.edges.forEach(({ from, to }) => {
    assert.ok(AGENT_GRAPH_VISUAL_FIXTURE.nodes[from], `missing edge source ${from}`);
    assert.ok(AGENT_GRAPH_VISUAL_FIXTURE.nodes[to], `missing edge target ${to}`);
  });
});

test("fixture cannot expose the running-step cancellation action", () => {
  const runningSteps = Object.values(AGENT_GRAPH_VISUAL_FIXTURE.nodes)
    .filter((node) => node.type === "step" && node.status === "running");

  assert.deepEqual(runningSteps, []);
});

test("split fixture adds one running child to an already visible parent", () => {
  const initialNodeIds = new Set(Object.keys(AGENT_GRAPH_VISUAL_FIXTURE.nodes));
  const nextNodeIds = Object.keys(AGENT_GRAPH_SPLIT_UPDATE_FIXTURE.nodes);
  const addedNodeIds = nextNodeIds.filter((id) => !initialNodeIds.has(id));
  const addedEdges = AGENT_GRAPH_SPLIT_UPDATE_FIXTURE.edges.filter((edge) => (
    !AGENT_GRAPH_VISUAL_FIXTURE.edges.some((initial) => initial.id === edge.id)
  ));

  assert.deepEqual(addedNodeIds, ["step-splitting"]);
  assert.equal(AGENT_GRAPH_SPLIT_UPDATE_FIXTURE.nodes["step-splitting"].status, "running");
  assert.deepEqual(addedEdges, [
    { id: "fixture-build-splitting", from: "execution-build", to: "step-splitting" },
  ]);
  assert.ok(initialNodeIds.has(addedEdges[0].from));

  assert.deepEqual(Object.keys(AGENT_GRAPH_SPLIT_BASE_FIXTURE.nodes), [
    "orchestrator",
    "planning-split",
  ]);
  assert.deepEqual(Object.keys(AGENT_GRAPH_SPLIT_CHILD_FIXTURE.nodes), [
    "orchestrator",
    "planning-split",
    "execution-split",
  ]);
  assert.equal(AGENT_GRAPH_SPLIT_CHILD_FIXTURE.nodes["execution-split"].status, "running");
});

test("running droplets retain a static reduced-motion cue and a live preference listener", async () => {
  const source = await readFile(agentGraphViewUrl, "utf8");

  assert.match(source, /staticRunningCue\s*=\s*status === "running"/);
  assert.match(source, /staticRunningCue \? "▶" : null/);
  assert.match(source, /_motionPreference\?\.addEventListener\?\.\("change"/);
  assert.match(source, /runningMotionEnvelope\(status, transition\)/);
  assert.match(source, /const nodeUpdates = Object\.values\(this\._nodeData\)/);
  assert.match(source, /this\._nodes\.update\(nodeUpdates\)/);
});

test("Rack Lab droplets track pointer contact and render routed liquid arrows", async () => {
  const source = await readFile(agentGraphViewUrl, "utf8");

  assert.match(source, /getPropertyValue\("--skin-graph-droplet-fill-alpha"\)/);
  assert.match(source, /resolveAgentGraphRecipe\(/);
  assert.match(source, /agentDropletBodyAlphas\(\s*fillAlpha,\s*this\._agentGraphRecipe\.liquid,\s*stateAlpha/);
  assert.match(source, /ctx\.fillStyle = rgba\(palette\.fill, bodyAlphas\.underlay\)/);
  assert.match(source, /body\.addColorStop\(0\.62, rgba\(palette\.fill, bodyAlphas\.fill\)\)/);
  assert.match(source, /ctx\.strokeStyle = rgba\(palette\.border, bodyAlphas\.rim\)/);
  assert.match(source, /this\._container\?\.addEventListener\(\s*"pointermove"/);
  assert.match(source, /addEventListener\("mousemove", handleLiquidPointerMove/);
  assert.match(source, /this\._network\.on\("blurNode", \(\) => this\._clearLiquidTouch\(\)\)/);
  assert.match(source, /this\._network\.DOMtoCanvas\(DOM\)/);
  assert.doesNotMatch(source, /this\._network\.on\("mousemove"/);
  assert.match(source, /strength:\s*0\.46 \+ Math\.sqrt\(penetration\) \* 0\.54/);
  assert.match(source, /_drawLiquidGraphEdges\(ctx\)/);
  assert.match(source, /if \(!this\._network \|\| !this\._activeEdges\.length\) return/);
  assert.match(source, /gradient\.addColorStop\(\s*0\.46/);
  assert.match(source, /hidden:\s*this\._nodeShape\(\) === AGENT_NODE_SHAPE\.DROPLET/);
  assert.match(source, /boundaryPoint\(from, fromRaw, dx, dy\)/);
  assert.match(source, /boundaryPoint\(to, toRaw, dx, dy, true\)/);
  assert.match(source, /getPropertyValue\("--skin-graph-title-clearance"\)/);
  assert.match(source, /const remoteJobsOverlap = viewportRect && remoteJobsRect/);
  assert.match(source, /visibleViewportHeight = Math\.max\(1, viewportHeight - Math\.round\(remoteJobsOverlap\)\)/);
});

test("keeps Rack Lab graph decisions out of shared canvas renderer utilities", async () => {
  const [renderer, geometry, liquidStyle, recipes] = await Promise.all([
    readFile(agentGraphViewUrl, "utf8"),
    readFile(agentNodeGeometryUrl, "utf8"),
    readFile(agentNodeLiquidStyleUrl, "utf8"),
    readFile(agentGraphRecipesUrl, "utf8"),
  ]);

  for (const source of [renderer, geometry, liquidStyle]) {
    assert.doesNotMatch(source, /rack-lab/);
  }
  // The shared helper may read named recipe values, but must not own the
  // Rack Lab opacity profile itself.
  assert.doesNotMatch(liquidStyle, /0\.46|0\.52|0\.56|0\.86/);
  assert.match(recipes, /id: "rack-lab"/);
  assert.match(recipes, /defaultFillAlpha: 0\.46/);
  assert.match(recipes, /rimAlpha: 0\.86/);
});
