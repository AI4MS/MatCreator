import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { parseHTML } from "linkedom";
import { fileURLToPath } from "node:url";
import { graphNodeSummary } from "../src/features/graphs/graphSummary.js";

// Bundle production methods with the project's existing bundler. The canvas
// library is the only stub: these tests exercise scheduling and detail ownership.
const bundled = await build({
  entryPoints: [fileURLToPath(new URL("../src/features/graphs/AgentGraphView.js", import.meta.url))],
  bundle: true, format: "esm", platform: "node", write: false,
  plugins: [{ name: "canvas-stub", setup(build) {
    build.onResolve({ filter: /^vis-network\/standalone$/ }, () => ({ path: "canvas", namespace: "test" }));
    build.onLoad({ filter: /.*/, namespace: "test" }, () => ({ contents: "export class Network {} export class DataSet {}" }));
  } }],
});
const { AgentGraphView } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function viewFixture() {
  const { document, window } = parseHTML("<html><body></body></html>");
  globalThis.document = document;
  globalThis.window = window;
  const view = Object.create(AgentGraphView.prototype);
  const ids = ["actions-row", "stop-step-btn", "input-row", "toolcalls-row", "conversation-row"];
  ids.forEach((id) => { const el = document.createElement("div"); el.id = `detail-${id}`; document.body.appendChild(el); });
  for (const field of ["El", "Label", "Status", "Summary", "Timing", "Artifacts", "Input", "Toolcalls", "ToolcallsCount", "Conversation", "ConversationCount"]) {
    view[`_detail${field}`] = document.createElement("div");
  }
  view._detailDisclosures = { clear() {}, wire() {} };
  view._syncPanelResizerVisibility = () => {};
  view._stepExecutionFeed = { highlight() {} };
  view._renderStepToolCall = (call) => { const el = document.createElement("div"); el.textContent = call.name; return el; };
  view._renderStepConversationEvent = (event) => { const el = document.createElement("div"); el.textContent = event.content; return el; };
  view._currentSessionId = "first";
  view._nodeData = { a: { id: "a", type: "step", revision: 1, status: "running" } };
  return view;
}

test("graph animation adapts to size and stops while hidden, with a final static paint", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const view = viewFixture();
  let redraws = 0;
  const frames = new Map();
  let nextId = 0;
  globalThis.requestAnimationFrame = (fn) => { frames.set(++nextId, fn); return nextId; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  Object.assign(view, {
    _network: { redraw: () => redraws++ }, _nodes: { length: 20 },
    _animationFrame: null, _animationTimer: null, _viewportVisible: true, _lastAnimationPaint: 0,
    _hasRunningNodes: true, _nodeTransitions: new Map(), _hasLiveTransitions: () => false,
  });
  assert.equal(view._animationInterval(), 32);
  view._nodes.length = 200;
  assert.equal(view._animationInterval(), 100);
  view._nodes.length = 600;
  assert.equal(view._animationInterval(), 200);
  view._syncAnimation();
  const [id, animate] = [...frames][0]; frames.delete(id); animate(201);
  assert.equal(redraws, 1);
  t.mock.timers.tick(199);
  assert.equal(frames.size, 0);
  t.mock.timers.tick(1);
  assert.equal(frames.size, 1);
  document.hidden = true;
  view._syncAnimation();
  assert.equal(frames.size, 0);
  assert.equal(view._animationTimer, null);
  document.hidden = false;
  view._viewportVisible = false;
  view._syncAnimation();
  assert.equal(frames.size, 0);
  view._viewportVisible = true;
  view._syncAnimation();
  assert.equal(frames.size, 1);
  const before = redraws;
  view._hasRunningNodes = false;
  view._syncAnimation();
  assert.equal(frames.size, 0);
  assert.equal(redraws, before + 1);
});

test("graph detail is fetched on selection, refreshed by revision, and cleared on close", async (t) => {
  const view = viewFixture();
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    requests.push({ url, ...options });
    return { ok: true, json: async () => ({ ...view._nodeData.a, input: { prompt: "large" }, tool_calls: [{ name: "heavy tool" }] }) };
  });
  await view._showDetail("a");
  assert.equal(requests.length, 1);
  assert.equal(view._detailToolcalls.childElementCount, 1);
  assert.equal(view._nodeData.a.tool_calls, undefined);
  view._nodeData.a.revision++;
  assert.notEqual(view._nodeDetailKey(view._nodeData.a), view._detailRenderKey);
  await view._showDetail("a");
  assert.equal(requests.length, 2);
  view._hideDetail();
  assert.equal(view._detailToolcalls.childElementCount, 0);
  assert.equal(view._detailInput.textContent, "");
});

test("graph selection changes and session switches discard late detail responses", async (t) => {
  const view = viewFixture();
  let finish;
  let signal;
  t.mock.method(globalThis, "fetch", (_url, options) => {
    signal = options.signal;
    return new Promise((resolve) => { finish = resolve; });
  });
  const pending = view._showDetail("a");
  view._hideDetail();
  view._currentSessionId = "second";
  assert.equal(signal.aborted, true);
  finish({ ok: true, json: async () => ({ id: "a", tool_calls: [{ name: "stale" }] }) });
  await pending;
  await tick();
  assert.equal(view._detailToolcalls.childElementCount, 0);
  assert.equal(view._activeDetailNodeId, null);
});

test("graph keys and session summaries never access execution payloads", () => {
  const view = viewFixture();
  const node = { id: "a", revision: 7, input: { node_id: "a" } };
  for (const key of ["conversation", "tool_calls", "artifacts", "output"]) {
    Object.defineProperty(node, key, { get() { throw Error(`read heavy ${key}`); } });
  }
  assert.equal(graphNodeSummary(node).revision, 7);
  const before = view._nodeDetailKey(node);
  node.revision++;
  assert.notEqual(view._nodeDetailKey(node), before);
});
