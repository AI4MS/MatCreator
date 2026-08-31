import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_NODE_SHAPE,
  agentNodeShapeForRecipe,
  dropletGeometry,
  dropletMotionForNode,
  nodeShapeDimensions,
  nodeShapeVerticalExtent,
  runningMotionEnvelope,
  traceDropletPath,
} from "../src/features/graphs/agentNodeGeometry.js";
import {
  DEFAULT_AGENT_DROPLET_FILL_ALPHA,
  agentDropletBodyAlphas,
  resolveAgentDropletFillAlpha,
} from "../src/features/graphs/agentNodeLiquidStyle.js";
import { RACK_LAB_SKIN } from "../src/theme/builtinSkins.js";
import { SKIN_TOKEN_SCHEMA } from "../src/theme/SkinContract.js";

test("activates droplet nodes only for the reviewed Rack Lab recipe version", () => {
  assert.equal(agentNodeShapeForRecipe("rack-lab", 1), AGENT_NODE_SHAPE.DROPLET);
  assert.equal(agentNodeShapeForRecipe("rack-lab", "1"), AGENT_NODE_SHAPE.DROPLET);
  assert.equal(agentNodeShapeForRecipe("standard", 1), AGENT_NODE_SHAPE.CIRCLE);
  assert.equal(agentNodeShapeForRecipe("rack-lab", 2), AGENT_NODE_SHAPE.CIRCLE);
  assert.equal(agentNodeShapeForRecipe("downloaded-css", 1), AGENT_NODE_SHAPE.CIRCLE);
});

test("raises only the Rack Lab liquid body tint through validated variant tokens", () => {
  const token = "--skin-graph-droplet-fill-alpha";
  const creamToken = RACK_LAB_SKIN.variants.light.tokens[token];
  const graphiteToken = RACK_LAB_SKIN.variants.dark.tokens[token];

  assert.equal(SKIN_TOKEN_SCHEMA[token], "number");
  assert.equal(DEFAULT_AGENT_DROPLET_FILL_ALPHA, 0.07);
  assert.equal(resolveAgentDropletFillAlpha(creamToken), 0.11);
  assert.equal(resolveAgentDropletFillAlpha(graphiteToken), 0.12);
  assert.equal(resolveAgentDropletFillAlpha(""), 0.07);
  assert.equal(resolveAgentDropletFillAlpha("1.1"), 0.07);

  const cream = agentDropletBodyAlphas(creamToken);
  const graphite = agentDropletBodyAlphas(graphiteToken);
  assert.deepEqual(cream, {
    highlight: 0.18,
    sheen: 0.04,
    fill: 0.11,
    rim: 0.11,
  });
  assert.deepEqual(graphite, {
    highlight: 0.18,
    sheen: 0.04,
    fill: 0.12,
    rim: 0.11,
  });

  const cancelled = agentDropletBodyAlphas(graphiteToken, 0.48);
  assert.equal(cancelled.fill, 0.12 * 0.48);
  assert.equal(cancelled.highlight, 0.18 * 0.48);
  assert.equal(cancelled.rim, 0.11 * 0.48);
});

test("gives active droplets deterministic independent motion without a layout pulse", () => {
  const still = dropletMotionForNode("planner", 1200);
  assert.deepEqual(still, {
    scaleX: 1,
    scaleY: 1,
    shear: 0,
    lobe: 0,
    curl: 0,
    highlightX: 0,
    highlightY: 0,
    touchAngle: 0,
    touchDepth: 0,
    touchX: 0,
    touchY: 0,
  });

  const first = dropletMotionForNode("planner", 1200, { active: true });
  const repeat = dropletMotionForNode("planner", 1200, { active: true });
  const later = dropletMotionForNode("planner", 1900, { active: true });
  const neighbour = dropletMotionForNode("executor", 1200, { active: true });
  assert.deepEqual(first, repeat);
  assert.notDeepEqual(first, later);
  assert.notDeepEqual(first, neighbour);
  assert.ok(Math.abs(first.scaleX * first.scaleY - 1) < 1e-12);
  assert.ok(Object.values(first).every(Number.isFinite));

  const base = dropletGeometry(16);
  const moving = dropletGeometry(16, first);
  const baseBox = (base.bounds.right - base.bounds.left) * (base.bounds.bottom - base.bounds.top);
  const movingBox = (moving.bounds.right - moving.bounds.left)
    * (moving.bounds.bottom - moving.bounds.top);
  assert.ok(Math.abs(movingBox / baseBox - 1) < 0.18);
});

test("uses a fixed low-amplitude hover pose instead of starting an idle animation", () => {
  const hoverA = dropletMotionForNode("planner", 100, { hover: true });
  const hoverB = dropletMotionForNode("planner", 9800, { hover: true });
  assert.deepEqual(hoverA, hoverB);
  assert.ok(Math.abs(hoverA.scaleX - 1) < 0.03);
});

test("indents the liquid boundary around a pointer contact without moving its centre", () => {
  const untouched = dropletGeometry(20);
  const touchedMotion = dropletMotionForNode("planner", 1200, {
    touch: { x: 0.9, y: 0.05, strength: 1 },
  });
  const touched = dropletGeometry(20, touchedMotion);

  assert.equal(touchedMotion.touchDepth, 1);
  assert.ok(touched.curves[0].end.x < untouched.curves[0].end.x - 2);
  assert.ok(touched.bounds.left <= untouched.bounds.left + 0.5);
  assert.ok(Math.abs(touched.opticalCenterY - untouched.opticalCenterY) < 0.5);
});

test("eases liquid motion into and out of the running lifecycle", () => {
  assert.equal(runningMotionEnvelope("idle"), 0);
  assert.equal(runningMotionEnvelope("running"), 1);
  assert.equal(runningMotionEnvelope("running", { to: "running", progress: 0 }), 0);
  assert.equal(runningMotionEnvelope("running", { to: "running", progress: 0.5 }), 0.5);
  assert.equal(runningMotionEnvelope("running", { to: "running", progress: 1 }), 1);
  assert.equal(runningMotionEnvelope("success", { from: "running", progress: 0 }), 1);
  assert.equal(runningMotionEnvelope("success", { from: "running", progress: 0.5 }), 0.5);
  assert.equal(runningMotionEnvelope("success", { from: "running", progress: 1 }), 0);

  const full = dropletMotionForNode("planner", 1200, { active: true, strength: 1 });
  const half = dropletMotionForNode("planner", 1200, { active: true, strength: 0.5 });
  const none = dropletMotionForNode("planner", 1200, { active: true, strength: 0 });
  assert.ok(Math.abs(half.lobe - full.lobe * 0.5) < 1e-12);
  assert.ok(Math.abs(half.curl - full.curl * 0.5) < 1e-12);
  assert.deepEqual(none, dropletMotionForNode("planner", 1200));
});

test("defines a stable full-bodied organic droplet with room for badges and edges", () => {
  const geometry = dropletGeometry(10);

  assert.ok(Math.abs(geometry.start.x + 1.4) < Number.EPSILON * 10);
  assert.equal(geometry.start.y, -10);
  assert.equal(geometry.curves.length, 4);
  assert.equal(geometry.bounds.top, -10.8);
  assert.equal(geometry.bounds.bottom, 10.8);
  assert.ok(geometry.bounds.left < -10);
  assert.ok(geometry.bounds.right > 9);
  assert.ok(geometry.opticalCenterY > 0);
  assert.ok(geometry.statusAnchor.x > 0 && geometry.statusAnchor.y < 0);
  assert.equal(nodeShapeVerticalExtent(10, AGENT_NODE_SHAPE.DROPLET, "top"), 10.8);
  assert.equal(nodeShapeVerticalExtent(10, AGENT_NODE_SHAPE.DROPLET, "bottom"), 10.8);
});

test("traces one closed four-curve path and expands the custom-node hit box", () => {
  const calls = [];
  const ctx = {
    beginPath: () => calls.push(["beginPath"]),
    moveTo: (...args) => calls.push(["moveTo", ...args]),
    bezierCurveTo: (...args) => calls.push(["bezierCurveTo", ...args]),
    closePath: () => calls.push(["closePath"]),
  };

  traceDropletPath(ctx, 20, 30, 10);

  assert.equal(calls.filter(([name]) => name === "beginPath").length, 1);
  assert.equal(calls.filter(([name]) => name === "moveTo").length, 1);
  assert.equal(calls.filter(([name]) => name === "bezierCurveTo").length, 4);
  assert.equal(calls.at(-1)[0], "closePath");
  assert.ok(calls.flat().filter((value) => typeof value === "number").every(Number.isFinite));

  const circle = nodeShapeDimensions(13, AGENT_NODE_SHAPE.CIRCLE);
  const droplet = nodeShapeDimensions(13, AGENT_NODE_SHAPE.DROPLET);
  assert.deepEqual(circle, { width: 40, height: 40 });
  assert.ok(droplet.height > circle.height);
  assert.ok(droplet.width >= 39);
});
