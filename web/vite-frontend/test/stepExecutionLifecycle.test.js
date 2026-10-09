import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";
import { StepExecutionFeed } from "../src/features/graphs/StepExecutionFeed.js";
import { createSessionRuntime } from "../src/features/session/runtime.js";
import { VirtualTranscript } from "../src/features/session/VirtualTranscript.js";
import { createDisclosureController } from "../src/features/ui/disclosureState.js";
import { graphNodeSummary } from "../src/features/graphs/graphSummary.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));
const collapsed = () => new Promise((resolve) => setTimeout(resolve, 260));
function fixture(t, nodes, loader) {
  const { document, window } = parseHTML("<html><body><main></main></body></html>");
  globalThis.document = document;
  globalThis.window = window;
  const chat = document.querySelector("main");
  const requests = [];
  const render = (className, text) => {
    const el = document.createElement("div");
    el.className = className;
    el.textContent = text || "";
    return el;
  };
  const feed = new StepExecutionFeed({
    chatArea: chat, isSending: () => false,
    updatePreservingReadingPosition: (fn) => fn(),
    createAgentAvatarEl: () => render("avatar"),
    stepFeedTitle: (node) => ({ action: node.input?.action || node.id }),
    stepFeedStatusIcon: (status) => status,
    formatStepDuration: () => "1s",
    renderStepInput: (input) => render("input", input.prompt),
    renderStepConversationEvent: (event) => render("conversation", event.content),
    renderStepToolCall: (call) => render("tool", call.name),
    requestStepCancellation: async () => {},
    createArtifactListItem: () => render("artifact"),
    disclosureController: createDisclosureController(),
    loadNodeDetail: async (id, signal) => {
      requests.push({ id, signal });
      return loader ? loader(id, signal) : nodes.find((node) => node.id === id);
    },
  });
  t.after(() => feed.reset());
  feed.setHierarchy(nodes);
  const host = document.createElement("div");
  host.className = "delegation-task-host";
  chat.appendChild(host);
  const mount = (node = nodes[0], target = host) => feed.appendStatic(graphNodeSummary(node), target);
  const toggle = async (card, open) => {
    const details = card.querySelector(".step-feed-details");
    // linkedom supplies DOM but does not synthesize native details interaction.
    details.open = open;
    details.dispatchEvent(new window.Event("toggle"));
    await tick();
  };
  return { feed, host, chat, requests, mount, toggle };
}
function node(id, extra = {}) {
  return {
    id, type: "step", status: "success", revision: 1,
    input: { node_id: id, step_number: 1, action: "Work", prompt: "large input" },
    conversation: [{ type: "text", content: "large conversation" }],
    tool_calls: [{ name: "calculate", output: "large output" }],
    artifacts: ["artifact"], ...extra,
  };
}

test("completed cards fetch and mount history only on open, release it after collapse", async (t) => {
  const f = fixture(t, [node("a")]);
  const card = f.mount();
  assert.equal(card.querySelector(".step-feed-body"), null);
  assert.equal(f.requests.length, 0);
  assert.equal(card._stepNode.conversation, undefined);
  await f.toggle(card, true);
  assert.equal(f.requests.length, 1);
  assert.equal(card.querySelectorAll(".conversation, .tool").length, 2);
  await f.toggle(card, false);
  assert.ok(card.querySelector(".step-feed-body"), "keep DOM through collapse motion");
  await collapsed();
  assert.equal(card.querySelector(".step-feed-body"), null);
  assert.equal(card._stepDetail, null);
  await f.toggle(card, true);
  assert.equal(f.requests.length, 2);
  assert.equal(card.querySelectorAll(".conversation").length, 1);
});

test("collapsed parents own no descendants; collapse releases nested details and registries", async (t) => {
  const nodes = [node("parent"), node("child", { parent_id: "parent" }), node("grandchild", { parent_id: "child" })];
  const f = fixture(t, nodes);
  const parent = f.mount();
  assert.equal(f.feed._cards.size, 1);
  await f.toggle(parent, true);
  const child = f.feed._cards.get("child");
  assert.ok(child);
  assert.equal(child.querySelector(".step-feed-body"), null);
  await f.toggle(child, true);
  assert.ok(f.feed._cards.has("grandchild"));
  await f.toggle(parent, false);
  await collapsed();
  assert.equal(f.feed._cards.size, 1);
  assert.equal(parent.querySelector(".step-feed-child-section"), null);
  assert.equal(child._stepDetail, null);
  assert.equal(child._stepNode, null);
});

test("running revisions update only open cards and terminal cancellation compacts once", async (t) => {
  const a = node("a", { status: "running" });
  const f = fixture(t, [a]);
  const card = f.mount();
  a.revision++;
  f.feed.update({ nodes: { a } });
  assert.equal(f.requests.length, 0);
  await f.toggle(card, true);
  a.revision++;
  a.conversation.push({ type: "text", content: "more" });
  f.feed.update({ nodes: { a } });
  await tick();
  assert.equal(f.requests.length, 2);
  assert.equal(card.querySelectorAll(".conversation").length, 2);
  f.feed.update({ nodes: { a } });
  await tick();
  assert.equal(f.requests.length, 2);
  a.status = "cancelled";
  a.revision++;
  f.feed.update({ nodes: { a } });
  await collapsed();
  assert.equal(card.querySelector(".step-feed-body"), null);
  assert.equal(card.querySelector(".step-feed-stop-btn"), null);
  await f.toggle(card, true);
  a.revision++;
  f.feed.update({ nodes: { a } });
  await tick();
  assert.equal(card.querySelector(".step-feed-details").open, true);
});

test("virtual row eviction and replacement release ownership; scrolling back creates one card", async (t) => {
  const a = node("a");
  const f = fixture(t, [a]);
  const viewport = Object.create(VirtualTranscript.prototype);
  let visible = true;
  viewport.canvas = f.chat;
  viewport.rows = [{ id: "row", type: "assistant", revision: 1 }];
  viewport.rowElements = new Map();
  viewport.virtualizer = {
    getTotalSize: () => 100,
    getVirtualItems: () => visible ? [{ index: 0, start: 0, size: 100 }] : [],
    measureElement() {},
  };
  viewport.releaseRow = (host) => f.feed.releaseWithin(host);
  viewport.renderRow = (_row, host) => f.mount(a, host);
  viewport.renderPass();
  const old = f.feed._cards.get("a");
  await f.toggle(old, true);
  visible = false;
  viewport.renderPass();
  assert.equal(f.feed._cards.size, 0);
  assert.equal(old._stepDetail, null);
  f.feed.update({ nodes: { a } });
  assert.equal(f.feed._cards.size, 0, "graph updates must not recreate evicted historical cards");
  visible = true;
  viewport.renderPass();
  assert.equal(f.chat.querySelectorAll(".step-feed-message").length, 1);
  assert.notEqual(f.feed._cards.get("a"), old);
  viewport.rows[0].revision++;
  viewport.renderPass();
  assert.equal(f.feed._cards.size, 1);
  assert.equal(f.chat.querySelectorAll(".step-feed-message").length, 1);
});

test("in-flight detail cannot resurrect released cards or leak across session reset", async (t) => {
  let finish;
  const f = fixture(t, [node("a")], () => new Promise((resolve) => { finish = resolve; }));
  const card = f.mount();
  await f.toggle(card, true);
  f.feed.reset();
  assert.equal(f.requests[0].signal.aborted, true);
  finish(node("a"));
  await tick();
  assert.equal(f.feed._cards.size, 0);
  assert.equal(card._stepDetail, null);
  assert.equal(card.querySelector(".step-feed-body"), null);
});

test("rapid collapse/reopen and slow detail fetch coalesce to the newest revision", async (t) => {
  const a = node("a", { status: "running" });
  const pending = [];
  const f = fixture(t, [a], () => new Promise((resolve) => pending.push(resolve)));
  const card = f.mount();
  await f.toggle(card, true);
  for (let i = 0; i < 10; i++) { a.revision++; f.feed.update({ nodes: { a } }); }
  assert.equal(f.requests.length, 1);
  pending.shift()(a);
  await tick();
  assert.equal(f.requests.length, 2);
  pending.shift()(a);
  await tick();
  await f.toggle(card, false);
  await f.toggle(card, true);
  await collapsed();
  assert.ok(card.querySelector(".step-feed-body"));
  assert.equal(f.requests.length, 2);
});

test("stable live launcher slots reconcile delayed parents and newest attempts without duplicates", async (t) => {
  const a = node("execution_1__node_a", { status: "running" });
  const nodes = [a];
  const f = fixture(t, nodes);
  f.feed._isSending = () => true;
  f.feed.startLiveTurn(null, Date.now());
  f.feed.update({ nodes: { [a.id]: a } });
  const card = f.feed._cards.get(a.id);
  assert.equal(card.isConnected, false);
  f.feed.bindRootHost(f.host, a.id);
  assert.equal(f.host.firstElementChild, card);
  const parent = node("parent", { status: "running" });
  nodes.push(parent);
  a.parent_id = parent.id;
  f.feed.update({ nodes: { [a.id]: a, parent } });
  assert.equal(f.feed._cards.has(a.id), false, "closed parent releases former root");
  const parentCard = f.feed._cards.get(parent.id);
  f.feed.bindRootHost(f.host, parent.id);
  await f.toggle(parentCard, true);
  assert.equal(f.chat.querySelectorAll(`[data-step-node-id="${a.id}"]`).length, 1);
  f.feed.finishLiveTurn();
});

test("200 tasks × 100 events mount no heavy bodies and retain no evicted cards", (t) => {
  const nodes = Array.from({ length: 200 }, (_, i) => node(`task-${i}`, {
    conversation: Array.from({ length: 100 }, () => ({ type: "text", content: "x".repeat(1000) })),
    tool_calls: Array.from({ length: 100 }, () => ({ name: "tool", output: "x".repeat(1000) })),
  }));
  const f = fixture(t, nodes);
  // Fail if any comparison or summary attempts to serialize heavy payloads.
  nodes.forEach((node) => { node.conversation.toJSON = () => { throw Error("history serialized"); }; });
  nodes.forEach((node) => {
    const row = document.createElement("div");
    f.chat.appendChild(row);
    f.mount(node, row);
    f.feed._renderKey(node);
  });
  assert.equal(f.chat.querySelectorAll(".step-feed-body").length, 0);
  assert.equal(f.feed._cards.size, 200);
  assert.equal(f.requests.length, 0);
  f.feed.releaseWithin(f.chat);
  f.chat.replaceChildren();
  assert.equal(f.feed._cards.size, 0);
  assert.equal([...f.feed._stepById.values()].some((node) => "conversation" in node), false);
});

test("detail failures remain retryable and closing aborts an outstanding load", async (t) => {
  let attempts = 0;
  const f = fixture(t, [node("a")], () => {
    attempts++;
    if (attempts === 1) throw new Error("offline");
    return node("a");
  });
  const card = f.mount();
  await f.toggle(card, true);
  assert.match(card.querySelector(".step-feed-body").textContent, /Retry/);
  card.querySelector(".step-feed-body button").click();
  await tick();
  assert.equal(card.querySelectorAll(".conversation").length, 1);
  f.feed.releaseWithin(f.host);
  assert.equal(f.feed._cards.size, 0);
});

test("virtual eviction unregisters live launcher hosts without disturbing the active slot", (t) => {
  const nodes = [node("old"), node("live", { status: "running" })];
  const f = fixture(t, nodes);
  const old = f.mount(nodes[0]);
  f.feed.bindRootHost(f.host, "old");
  const liveHost = document.createElement("div");
  liveHost.className = "delegation-task-host";
  f.chat.appendChild(liveHost);
  f.feed.bindRootHost(liveHost, "live");
  const live = f.feed._cards.get("live");
  f.feed.releaseWithin(f.host);
  f.host.remove();
  assert.equal(f.feed._rootHosts.has("old"), false);
  assert.equal(f.feed._cards.has("old"), false);
  assert.equal(old._stepNode, null);
  assert.equal(f.feed._rootHosts.get("live"), liveHost);
  assert.equal(f.feed._cards.get("live"), live);
  assert.equal(live.parentElement, liveHost);
});


test("session switching aborts detail loads and cached contexts contain only summaries", async (t) => {
  const a = node("a");
  let finish;
  const f = fixture(t, [a], () => new Promise((resolve) => { finish = resolve; }));
  const state = {
    sessionId: "first", userId: "owner", activeSessionUserId: "owner",
    activeRequests: new Map(), sessionViewCache: new Map(), sessionSummaries: {}, summaryGeneratedFor: new Set(),
  };
  const sessionData = { state: {}, events: [{
    id: "launch", author: "agent", content: { parts: [{ functionCall: { name: "run_node_executor", args: { node_id: "a" } } }] },
  }] };
  const requestedUrls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    requestedUrls.push(url);
    return { ok: true, json: async () => url.startsWith("/api/agent-graph/") ? { nodes: { a } } : sessionData };
  });
  let viewportOptions;
  const runtime = createSessionRuntime({
    session: { state, requestKey: (id = state.sessionId, owner = "owner") => `${owner}:${id}`, releaseRequest() {} },
    timeline: { chatArea: f.chat, stepExecutionFeed: f.feed, getFunctionResponse: (part) => part.functionResponse },
    ui: { refreshSessionFiles: async () => {}, renderSessionBanner() {} },
    managedRun: {},
    createViewport: (options) => {
      viewportOptions = options;
      return { setRows() {}, clearLive() {}, currentOffset: () => 0, restoreOffset() {}, reset() {}, liveHost: f.chat };
    },
  });
  assert.ok(await runtime.loadSession("first"));
  const first = state.sessionViewCache.get("owner:first");
  assert.equal(first.transcriptContext.graphNodes.get("a").conversation, undefined);
  assert.equal(first.transcriptContext.graphNodes.get("a").input.prompt, undefined);
  const card = f.mount();
  await f.toggle(card, true);
  state.sessionId = "second";
  assert.ok(await runtime.loadSession("second"));
  assert.equal(f.requests[0].signal.aborted, true);
  finish(a);
  await tick();
  assert.equal(card._stepDetail, null);
  state.sessionId = "first";
  assert.equal(runtime.restoreSessionSnapshot(first), true);
  assert.equal(f.feed._stepById.get("a").conversation, undefined);
  f.mount();
  viewportOptions.releaseRow(f.host);
  assert.equal(f.feed._cards.size, 0);
  assert.equal(requestedUrls.some((url) => url.includes("/nodes/")), false, "session restoration never preloads detail");
});


test("closing task bodies also releases saved per-event disclosure metadata", async (t) => {
  const f = fixture(t, [node("a")]);
  f.feed._renderStepToolCall = () => document.createElement("details");
  const card = f.mount();
  await f.toggle(card, true);
  f.feed.captureDisclosureState();
  assert.ok([...f.feed._disclosures.state.keys()].some((key) => key.startsWith("step:a:nested:")));
  await f.toggle(card, false);
  await collapsed();
  assert.equal([...f.feed._disclosures.state.keys()].some((key) => key.startsWith("step:a:nested:")), false);
});


test("failed detail refresh releases nested ownership and retry rebuilds it", async (t) => {
  const nodes = [node("parent"), node("child", { parent_id: "parent" })];
  let parentRequests = 0;
  const f = fixture(t, nodes, (id) => {
    if (id === "parent" && ++parentRequests === 2) throw new Error("offline");
    return nodes.find((node) => node.id === id);
  });
  const parent = f.mount();
  await f.toggle(parent, true);
  const child = f.feed._cards.get("child");
  await f.toggle(child, true);
  nodes[0].revision++;
  f.feed.update({ nodes: Object.fromEntries(nodes.map((node) => [node.id, node])) });
  await tick();
  assert.equal(f.feed._cards.has("child"), false);
  assert.equal(child._stepDetail, null);
  parent.querySelector(".step-feed-body button").click();
  await tick();
  assert.equal(parentRequests, 3);
  assert.equal(parent.querySelectorAll('[data-step-node-id="child"]').length, 1);
});
