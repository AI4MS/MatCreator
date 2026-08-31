import {
  applyAssistantMessageEvent,
  completeAssistantMessage,
  createAssistantMessage,
} from "../chat/timeline.js";
import { createMessageRenderScheduler, messageRenderInterval } from "../chat/messageRenderScheduler.js";
import {
  findConversationRequest,
  initializeRequestLifecycle,
  markRequestTerminal,
} from "./requestLifecycle.js";
import { TranscriptStore } from "./TranscriptStore.js";
import { VirtualTranscript } from "./VirtualTranscript.js";

function latestPendingPlan(events, getResponse = (part) => part?.functionResponse || part?.function_response || null) {
  const userIndex = events.reduce((result, event, index) => event?.author === "user" ? index : result, -1);
  if (userIndex < 0) return null;
  let pending = null;
  events.slice(userIndex + 1).forEach((event) => (event?.content?.parts || []).forEach((part) => {
    const response = getResponse(part);
    if (["validate_graph", "validate_plan"].includes(response?.name) && response.response?.status === "ok") pending = events[userIndex];
    if (["confirm_plan_and_start_execution", "resume_execution"].includes(response?.name) && response.response?.status === "ok") pending = null;
  }));
  return pending;
}

/** Return the durable events that belong to the newest conversational turn. */
export function latestConversationTurn(events = []) {
  const userIndex = events.findLastIndex((event) => event?.author === "user");
  if (userIndex < 0) return { userEvent: null, assistantEvents: [] };
  return {
    userEvent: events[userIndex],
    assistantEvents: events.slice(userIndex + 1).filter((event) => event?.author !== "user"),
  };
}

export function shouldShowApproval(sessionId, sessionData, events, options = {}) {
  const normalized = typeof options === "function" ? { isSuppressed: options } : (options || {});
  const suppressedPlanApprovalTurns = normalized.suppressedPlanApprovalTurns || new Map();
  const getResponse = normalized.getResponse || ((part) => part?.functionResponse || part?.function_response || null);
  const userEvent = latestPendingPlan(events, getResponse);
  const text = (userEvent?.content?.parts || []).map((part) => part.text || "").join("");
  const suppressed = typeof normalized.isSuppressed === "function"
    ? normalized.isSuppressed(sessionId, text)
    : suppressedPlanApprovalTurns.get(sessionId);
  return (sessionData?.state?.agent_mode || "normal") === "normal"
    && userEvent !== null
    && (!suppressed || Boolean(suppressed.userText && suppressed.userText === text));
}

/** Persisted history mutates a store; VirtualTranscript alone owns geometry. */
export function createSessionRuntime({
  session: { state, requestKey: sessionRequestKey, releaseRequest: releaseSessionRequest },
  timeline: {
    chatArea, stepExecutionFeed, getFunctionResponse,
    displayStoredUserText: displayMessageFromStoredUserText,
    addMessage, addAgentTimelineMessage, addPlanApprovalActions, renderTimeline,
    clearDisclosures: clearChatDisclosures,
  },
  ui: {
    updateSendButtonState, renderSessionBanner, refreshSessionFiles, workdirDisplay,
    onRequestStateChange, attachAgentRunningIndicator, updateAgentRunningStatus,
  },
  managedRun: { eventsUrl: managedRunEventsUrl },
}) {
  const pageSize = 40;
  const contextLimit = 3;
  const contexts = new Map();
  const suppressedPlanApprovalTurns = new Map();
  let activeContext = null;
  let unsubscribeStore = null;
  let sessionFetchController = null;
  const metrics = { sessionLoads: 0, historyFetches: 0, managedSnapshotRecoveries: 0 };

  const viewport = new VirtualTranscript({
    chatArea,
    renderRow: (row, host) => renderPersistedRow(row, host),
    estimateRow: (row) => activeContext?.store.estimateRow(row) || 132,
    onNeedRange: ({ targetIndex }) => {
      if (!activeContext) return;
      activeContext.store.setFocusIndex(targetIndex);
      void loadRange(activeContext, targetIndex);
    },
  });

  function rememberContext(context) {
    contexts.delete(context.viewKey);
    contexts.set(context.viewKey, context);
    while (contexts.size > contextLimit) contexts.delete(contexts.keys().next().value);
  }

  function activeRequestForContext(context) {
    if (!context) return null;
    return findConversationRequest(state.activeRequests, {
      key: context.viewKey,
      sessionId: context.sessionId,
      owner: context.owner,
    });
  }

  function activateContext(context, { follow = false } = {}) {
    const switching = activeContext !== context;
    const preserveLiveTurn = Boolean(activeRequestForContext(context));
    if (switching) {
      if (activeContext) {
        activeContext.viewportOffset = viewport.currentOffset();
        activeContext.rangeControllers.forEach((controller) => controller.abort());
        activeContext.rangeControllers.clear();
      }
      unsubscribeStore?.();
      activeContext = context;
      unsubscribeStore = context.store.subscribe(() => refreshRows(context));
      if (!preserveLiveTurn) viewport.clearLive();
      clearChatDisclosures?.();
      stepExecutionFeed.reset({ preserveDisclosures: false });
    }
    stepExecutionFeed.setHierarchy([...context.graphNodes.values()]);
    const hasSavedOffset = Number.isFinite(context.viewportOffset);
    refreshRows(context, { follow: follow && !hasSavedOffset });
    if (switching && hasSavedOffset) viewport.restoreOffset(context.viewportOffset);
    if (preserveLiveTurn || !switching) restoreActiveLiveView(context);
  }

  function restoreActiveLiveView(context) {
    const request = activeRequestForContext(context);
    if (!request) return;
    const records = [...context.store.records.values()].sort((left, right) => left.index - right.index);
    const durableUserIndex = records.findLastIndex(({ event }) => event?.author === "user"
      && (event.content?.parts || []).some((part) => {
        const text = String(part.text || "").trim();
        const backendText = String(request.backendMessage || "").trim();
        return !backendText || text.includes(backendText) || backendText.includes(text);
      }));
    const durableUserPresent = durableUserIndex >= 0;
    if (request.userMessage && !request.userMessage.isConnected && !durableUserPresent) {
      viewport.liveHost.appendChild(request.userMessage);
    }
    if (request.messageView && !request.messageView.element.isConnected) {
      viewport.liveHost.appendChild(request.messageView.element);
    }
    if (request.messageView && request.message?.lifecycle !== "completed") {
      stepExecutionFeed.resumeLiveTurn(request.messageView.stepFeedLiveHost, request.message.startedAt);
    }
  }

  function refreshRows(context, { follow = false } = {}) {
    if (activeContext !== context || sessionRequestKey() !== context.viewKey) return;
    const request = activeRequestForContext(context);
    // The active run always belongs to the newest persisted user turn. Do not
    // compare text here: the durable message can contain upload context or
    // other normalization that differs from `request.backendMessage`.
    const activeUserIndex = request
      ? [...context.store.records.values()].sort((left, right) => left.index - right.index)
        .findLast(({ event }) => event?.author === "user")?.index
      : null;
    const rows = context.store.rows()
      // The live message owns the active turn until the request is released.
      // Session snapshots can arrive before then; rendering their partial
      // assistant row as well creates a raw/Markdown duplicate.
      .filter((row) => !(request && Number.isInteger(activeUserIndex)
        && row.type === "assistant" && row.startIndex > activeUserIndex))
      .map((row) => {
        if (row.type !== "assistant") return row;
        const graphRevision = launcherNodeIds(row.records.map((record) => record.event))
          .map((id) => context.graphRevisions.get(id) || "")
          .join("|");
        return {
          ...row,
          id: `${context.viewKey}:${row.id}`,
          revision: `${row.revision}:g${graphRevision}:a${context.awaitingPlanApproval ? 1 : 0}`,
        };
      }).map((row) => row.type === "assistant" ? row : ({ ...row, id: `${context.viewKey}:${row.id}` }));
    viewport.setRows(rows, { follow });
  }

  async function fetchSessionData(sessionId, owner, { offset = null, signal } = {}) {
    const query = new URLSearchParams({ compact: "1", limit: String(pageSize) });
    if (Number.isInteger(offset)) query.set("offset", String(Math.max(0, offset)));
    const response = await fetch(
      `/api/users/${encodeURIComponent(owner)}/sessions/${encodeURIComponent(sessionId)}?${query}`,
      { headers: { "Content-Type": "application/json" }, signal },
    );
    return response.ok ? response.json() : null;
  }

  function launcherNodeId(input = {}) {
    const value = input.node_id ?? input.step_id ?? input.step_number;
    return value === undefined || value === null ? "" : String(value);
  }

  function launcherNodeIds(events) {
    const ids = new Set();
    (events || []).forEach((event) => (event?.content?.parts || []).forEach((part) => {
      const call = part?.functionCall || part?.function_call;
      // Child executors are included with their root executor's graph
      // subtree. Fetching them by local step_number would match unrelated
      // children from other parents (many have step number 1).
      if (!call || !["run_flash_step", "run_node_executor"].includes(call.name)) return;
      const id = launcherNodeId(call.args || {});
      if (id) ids.add(id);
    }));
    return [...ids];
  }

  async function fetchStepNodes(sessionId, events, signal) {
    const ids = launcherNodeIds(events);
    if (!ids.length) return [];
    try {
      const query = new URLSearchParams();
      ids.forEach((id) => query.append("node_id", id));
      const response = await fetch(`/api/agent-graph/${encodeURIComponent(sessionId)}?${query}`, { signal });
      if (!response.ok) return [];
      const graph = await response.json();
      return Object.values(graph.nodes || {}).filter((node) => node.type === "step");
    } catch (error) {
      if (error?.name !== "AbortError") console.error("Failed to load transcript steps:", error);
      return [];
    }
  }

  function mergeGraphNodes(context, nodes) {
    let changed = false;
    nodes.forEach((node) => {
      if (!node?.id) return;
      const requestedId = launcherNodeId(node.input) || node.id;
      const revision = String(node.updated_at || node.end_time || node.status || "idle");
      if (context.graphRevisions.get(requestedId) === revision) return;
      context.graphNodes.set(node.id, node);
      context.graphRevisions.set(requestedId, revision);
      changed = true;
    });
    if (!changed) return;
    context.graphRevision += 1;
    if (activeContext === context) stepExecutionFeed.setHierarchy([...context.graphNodes.values()]);
  }

  async function loadRange(context, targetIndex) {
    if (activeContext !== context || sessionRequestKey() !== context.viewKey) return;
    const offset = Math.max(0, Math.min(
      Math.floor(targetIndex / pageSize) * pageSize,
      Math.max(0, context.store.totalCount - pageSize),
    ));
    if (!context.store.beginLoad(offset)) return;
    const controller = new AbortController();
    context.rangeControllers.set(offset, controller);
    metrics.historyFetches += 1;
    try {
      const response = await fetchSessionData(context.sessionId, context.owner, { offset, signal: controller.signal });
      if (!response || activeContext !== context || sessionRequestKey() !== context.viewKey) return;
      const nodes = await fetchStepNodes(context.sessionId, response.events || [], controller.signal);
      if (activeContext !== context) return;
      mergeGraphNodes(context, nodes);
      context.store.insertPage(response);
    } catch (error) {
      if (error?.name !== "AbortError") console.error("Failed to load transcript range:", error);
    } finally {
      if (context.rangeControllers.get(offset) === controller) context.rangeControllers.delete(offset);
      context.store.endLoad(offset);
    }
  }

  function eventTimestamp(event, fallback = null) {
    if (event?.timestamp !== undefined) {
      const value = Number(event.timestamp);
      return value < 1e12 ? value * 1000 : value;
    }
    return event?.createTime ? new Date(event.createTime).getTime() : fallback;
  }

  function appendEvent(message, event) {
    applyAssistantMessageEvent(message, event);
  }

  function attachStepNodes(timeline, context) {
    const nodes = [...context.graphNodes.values()];
    timeline.filter((item) => item.type === "activity_action").flatMap((item) => item.toolCalls || [])
      .filter((call) => ["run_flash_step", "run_node_executor"].includes(call.name)).forEach((call) => {
        const id = launcherNodeId(call.input);
        const node = nodes.find((candidate) => id
          && (launcherNodeId(candidate.input) === id || candidate.id?.endsWith(`__node_${id}`)));
        if (node) call.stepNodes = [node];
      });
  }

  function renderPersistedRow(row, host) {
    const context = activeContext;
    if (!context) return;
    const events = row.records.map((record) => record.event);
    if (row.type === "user") {
      const text = displayMessageFromStoredUserText((events[0]?.content?.parts || []).map((part) => part.text || "").join(""));
      if (text) addMessage("user", text, row.startIndex, host, { messageKey: row.id });
      return;
    }
    const message = createAssistantMessage({
      id: row.id,
      startedAt: eventTimestamp(events[0]),
    });
    events.forEach((event) => appendEvent(message, event));
    completeAssistantMessage(message, eventTimestamp(events.at(-1)));
    attachStepNodes(message.items, context);
    const view = addAgentTimelineMessage(message, new Set(), row.startIndex, host, {
      startedAt: message.startedAt, endedAt: message.endedAt, messageKey: row.id,
    });
    if (context.awaitingPlanApproval && row.endIndex === context.store.totalCount) {
      const pendingUserText = context.pendingPlanUserText;
      if (pendingUserText && canRevealPlanApproval(context.sessionId, pendingUserText)) {
        addPlanApprovalActions(view);
      }
    }
  }

  function makeContext(sessionId, owner, viewKey) {
    return {
      sessionId, owner, viewKey, store: new TranscriptStore(), graphNodes: new Map(), graphRevisions: new Map(),
      graphRevision: 0, sessionData: null, summary: "", awaitingPlanApproval: false, pendingPlanUserText: "", viewportOffset: null,
      rangeControllers: new Map(),
    };
  }

  async function loadSession(sessionId, owner = state.activeSessionUserId || state.userId, { render = true } = {}) {
    metrics.sessionLoads += 1;
    const viewKey = sessionRequestKey(sessionId, owner);
    const isCurrent = () => sessionRequestKey() === viewKey;
    sessionFetchController?.abort();
    const controller = new AbortController();
    sessionFetchController = controller;
    const filesPromise = render ? refreshSessionFiles(sessionId, owner) : Promise.resolve();
    try {
      const sessionData = await fetchSessionData(sessionId, owner, { signal: controller.signal });
      if (!sessionData || !isCurrent()) return null;
      const events = sessionData.events || [];
      if (!render) return { sessionData, events, graphNodes: [], summary: sessionData.summary || "" };
      const nodes = await fetchStepNodes(sessionId, events, controller.signal);
      if (!isCurrent()) return null;
      let context = contexts.get(viewKey) || makeContext(sessionId, owner, viewKey);
      context.sessionData = sessionData;
      context.summary = sessionData.summary || state.sessionSummaries[sessionId] || "";
      const pendingPlan = latestPendingPlan(events, getFunctionResponse);
      context.pendingPlanUserText = (pendingPlan?.content?.parts || []).map((part) => part.text || "").join("");
      context.awaitingPlanApproval = shouldShowApproval(sessionId, sessionData, events, {
        suppressedPlanApprovalTurns,
        getResponse: getFunctionResponse,
      });
      mergeGraphNodes(context, nodes);
      // Snapshot loading only mutates durable state. It never removes live
      // DOM: context activation, explicit handoff, and reset are the sole
      // owners of that destructive transition.
      const liveRequest = activeRequestForContext(context);
      context.store.insertPage(sessionData);
      hydrateManagedPresentation(liveRequest, events, sessionData.revision);
      rememberContext(context);
      const changedContext = activeContext !== context;
      activateContext(context, { follow: changedContext });
      state.sessionReady = true;
      if (state.deploymentMode === "local" && sessionData.userId) state.activeSessionUserId = sessionData.userId;
      if (sessionData.summary) {
        state.sessionSummaries[sessionId] = sessionData.summary;
        state.summaryGeneratedFor.add(sessionId);
      }
      renderSessionBanner(context.summary);
      updateSessionWorkdirDisplay(sessionData);
      state.sessionViewCache.delete(viewKey);
      state.sessionViewCache.set(viewKey, {
        transcriptContext: context, sessionData: { state: sessionData.state }, files: [],
        summary: context.summary, revision: sessionData.revision,
      });
      while (state.sessionViewCache.size > contextLimit) state.sessionViewCache.delete(state.sessionViewCache.keys().next().value);
      void filesPromise;
      return { sessionData, events, graphNodes: nodes, summary: context.summary };
    } catch (error) {
      if (error?.name !== "AbortError") console.error("Failed to load session:", error);
      return null;
    } finally {
      if (sessionFetchController === controller) sessionFetchController = null;
    }
  }

  function restoreSessionSnapshot(snapshot) {
    if (!snapshot?.transcriptContext) return false;
    // Cached recovery must also repair a detached live view. Merely declining
    // the snapshot leaves an active request with no DOM owner—the exact state
    // represented by a stop button alongside a missing running bubble.
    if (activeRequestForContext(snapshot.transcriptContext)) {
      activateContext(snapshot.transcriptContext);
      return true;
    }
    activateContext(snapshot.transcriptContext);
    renderSessionBanner(snapshot.summary || "");
    updateSessionWorkdirDisplay(snapshot.sessionData || {});
    return true;
  }

  function renderSessionTimeline(events, graphNodes = [], awaitingPlanApproval = false) {
    const context = makeContext(state.sessionId, state.activeSessionUserId || state.userId, sessionRequestKey());
    context.awaitingPlanApproval = awaitingPlanApproval;
    const pendingPlan = latestPendingPlan(events, getFunctionResponse);
    context.pendingPlanUserText = (pendingPlan?.content?.parts || []).map((part) => part.text || "").join("");
    mergeGraphNodes(context, graphNodes);
    context.store.insertPage({
      events,
      event_meta: events.map((event, index) => ({ index, cursor: String(index), turn_id: event.invocationId || String(index) })),
      pagination: { start_index: 0, end_index: events.length, total_count: events.length },
      revision: `legacy:${events.length}`,
    });
    rememberContext(context);
    activateContext(context, { follow: true });
  }

  function beginLiveOutput() {
    viewport.clearLive();
    viewport.followOutput();
    return viewport.liveHost;
  }
  function getLiveHost() { return viewport.liveHost; }
  async function handoffLiveTurn(request) {
    if (!request || state.activeRequests.get(request.key) !== request) return false;
    const isVisible = sessionRequestKey(request.sessionId, request.owner) === sessionRequestKey();
    // A live assistant view and its persisted transcript row are alternate
    // representations of one turn.  Clear the live owner *before* releasing
    // its request lock; otherwise a concurrent session refresh can mount the
    // durable Markdown row while the live DOM is still present.
    if (isVisible) viewport.clearLive();
    releaseSessionRequest(request);
    if (isVisible && activeContext?.viewKey === request.key) {
      // `reloadSessionSnapshot({ handoff: true })` has already committed the
      // durable events to this store. Removing the ownership filter mounts
      // that row immediately, without a blank network round trip.
      refreshRows(activeContext, { follow: true });
    }
    return true;
  }
  function resetTranscript() {
    activeContext?.rangeControllers.forEach((controller) => controller.abort());
    activeContext?.rangeControllers.clear();
    unsubscribeStore?.();
    unsubscribeStore = null;
    activeContext = null;
    viewport.reset();
  }

  function suppressPlanApproval(sessionId, userText = "") {
    if (!sessionId) return;
    suppressedPlanApprovalTurns.set(sessionId, { userText });
  }
  function restorePlanApproval(sessionId) { if (sessionId) suppressedPlanApprovalTurns.delete(sessionId); }
  function canRevealPlanApproval(sessionId, userText = "") {
    const suppressed = suppressedPlanApprovalTurns.get(sessionId);
    return !suppressed || Boolean(suppressed.userText && suppressed.userText === userText);
  }
  function updateSessionWorkdirDisplay(sessionData) {
    if (!workdirDisplay) return;
    const workdir = sessionData?.state?.workdir || sessionData?.state?.custom_workdir || state.defaultWorkdir || "";
    workdirDisplay.textContent = workdir;
    workdirDisplay.style.display = workdir ? "" : "none";
  }

  async function discoverManagedRun(sessionId, owner = state.activeSessionUserId || state.userId) {
    if (!owner || !sessionId) return null;
    try {
      const response = await fetch(`/api/runs/active?${new URLSearchParams({ user_id: owner, session_id: sessionId })}`);
      return response.ok ? (await response.json()).run || null : null;
    } catch (_) { return null; }
  }

  function beginManagedRunDiscovery(sessionId, owner = state.activeSessionUserId || state.userId) {
    const key = sessionRequestKey(sessionId, owner);
    const existing = state.activeRequests.get(key);
    if (existing) return existing;
    const request = initializeRequestLifecycle({
      key, sessionId, owner, backendUserId: owner, controller: new AbortController(),
      lastSequence: 0, runId: null, retryDelayMs: 500,
      managedReconnect: true, liveTurnClaimed: true, awaitingRunDiscovery: true,
    });
    state.activeRequests.set(key, request);
    createManagedPresentation(request);
    updateAgentRunningStatus?.("connecting");
    updateSendButtonState();
    return request;
  }

  function discardManagedRunDiscovery(sessionId, owner = state.activeSessionUserId || state.userId) {
    const key = sessionRequestKey(sessionId, owner);
    const request = state.activeRequests.get(key);
    if (!request?.awaitingRunDiscovery) return false;
    const isVisible = sessionRequestKey() === key;
    if (isVisible) viewport.clearLive();
    releaseSessionRequest(request);
    if (isVisible && activeContext?.viewKey === key) refreshRows(activeContext, { follow: true });
    return true;
  }

  function startManagedRunReconnect(activeRun, sessionId, owner = state.activeSessionUserId || state.userId) {
    if (!activeRun?.run_id) return;
    const key = sessionRequestKey(sessionId, owner);
    let request = state.activeRequests.get(key);
    if (request && !request.awaitingRunDiscovery) return request;
    if (request) {
      request.runId = activeRun.run_id;
      request.lastSequence = activeRun.latest_sequence || 0;
      request.awaitingRunDiscovery = false;
    } else {
      request = initializeRequestLifecycle({
        key, sessionId, owner, backendUserId: owner, controller: new AbortController(),
        lastSequence: activeRun.latest_sequence || 0, runId: activeRun.run_id, retryDelayMs: 500,
        managedReconnect: true, liveTurnClaimed: true, awaitingRunDiscovery: false,
      });
      state.activeRequests.set(key, request);
    }
    // Claim the newest assistant row before mounting its live representation.
    // This makes persisted history and reconnect mutually exclusive render
    // owners regardless of which refresh request finished first.
    if (activeContext?.viewKey === key) refreshRows(activeContext);
    createManagedPresentation(request);
    updateSendButtonState();
    void streamManagedRunEvents(request);
    return request;
  }

  function createManagedPresentation(request) {
    if (!request || request.messageView || sessionRequestKey() !== request.key) return null;
    const message = createAssistantMessage({
      id: `managed:${request.runId}`,
      startedAt: Date.now(),
    });
    const shownPlots = new Set();
    const view = addAgentTimelineMessage(message, shownPlots, undefined, beginLiveOutput(), {
      startedAt: message.startedAt, live: true, messageKey: message.id,
    });
    const scheduler = createMessageRenderScheduler({
      intervalMs: () => messageRenderInterval(message.items
        .filter((item) => item.type === "text" || item.type === "reasoning")
        .reduce((total, item) => total + String(item.text || "").length, 0)),
      render: () => {
        if (view.element.isConnected) renderTimeline(view, message, shownPlots);
      },
    });
    Object.assign(request, {
      message, messageView: view,
      managedPresentation: { message, shownPlots, view, scheduler, lineBuffer: "", hydratedRevision: null },
    });
    stepExecutionFeed.startLiveTurn(null, message.startedAt, view.stepFeedLiveHost);
    const storedEvents = activeContext?.viewKey === request.key
      ? [...activeContext.store.records.values()].sort((left, right) => left.index - right.index).map(({ event }) => event)
      : [];
    hydrateManagedPresentation(request, storedEvents, activeContext?.store.revision);
    attachAgentRunningIndicator?.(view);
    updateAgentRunningStatus?.("working");
    return request.managedPresentation;
  }

  function hydrateManagedPresentation(request, events, revision = null) {
    const live = request?.managedPresentation;
    if (!live || live.message.lifecycle === "completed") return false;
    if (revision && live.hydratedRevision === revision) return false;
    const { assistantEvents } = latestConversationTurn(events || []);
    assistantEvents.forEach((event) => appendEvent(live.message, event));
    if (revision) live.hydratedRevision = revision;
    // Snapshot hydration is a recovery transaction, not a streamed update:
    // commit it synchronously so the recovered bubble never waits for a
    // throttle interval or another SSE token before becoming visible.
    renderTimeline(live.view, live.message, live.shownPlots);
    if (live.streamFinished && live.message.items.length) finishManagedPresentation(live);
    return assistantEvents.length > 0;
  }

  function finishManagedPresentation(live) {
    if (!live || !live.message.items.length) return null;
    if (live.finishPromise) return live.finishPromise;
    completeAssistantMessage(live.message);
    updateSendButtonState();
    live.finishPromise = live.scheduler.finish();
    return live.finishPromise;
  }

  function applyManagedPayload(live, payload) {
    let streamFinished = false;
    live.lineBuffer = `${live.lineBuffer || ""}${String(payload || "")}`;
    const lines = live.lineBuffer.split("\n");
    live.lineBuffer = lines.pop() || "";
    lines.forEach((line) => {
      if (!line.trim().startsWith("data: ")) return;
      const data = line.trim().slice(6);
      if (data === "[DONE]") {
        streamFinished = true;
        return;
      }
      try { appendEvent(live.message, JSON.parse(data)); } catch (_) { /* malformed replay event */ }
    });
    live.scheduler.request();
    if (streamFinished) {
      live.streamFinished = true;
      // `[DONE]` only means the model stream ended. A refreshed client can
      // receive it before replay/snapshot hydration supplies any visible
      // content, so keep the waiting presentation alive in that interval.
      if (!finishManagedPresentation(live)) updateAgentRunningStatus?.("saving");
    }
  }

  async function streamManagedRunEvents(request) {
    const isCurrent = () => state.activeRequests.get(request.key) === request;
    const live = request.managedPresentation || createManagedPresentation(request);
    try {
      while (isCurrent() && !request.controller.signal.aborted) {
        const response = await fetch(managedRunEventsUrl(request), {
          headers: { Accept: "text/event-stream" }, signal: request.controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        updateAgentRunningStatus?.("connected");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let terminal = false;
        while (!terminal) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.trim().startsWith("data: ")) continue;
            const envelope = JSON.parse(line.trim().slice(6));
            if (envelope.type === "event") {
              request.lastSequence = envelope.sequence || request.lastSequence;
              if (live) applyManagedPayload(live, envelope.data);
            } else if (envelope.type === "snapshot_required") {
              request.lastSequence = envelope.latest_sequence || request.lastSequence;
              metrics.managedSnapshotRecoveries += 1;
              // Reconnect requests have no optimistic user node, so the full
              // snapshot can safely restore history while live ownership
              // filters only the newest assistant row. Hydration happens as
              // part of that same load transaction.
              await loadSession(request.sessionId, request.owner);
            } else if (envelope.type === "terminal") {
              request.lastSequence = envelope.latest_sequence || request.lastSequence;
              markRequestTerminal(request, envelope.status);
              updateSendButtonState();
              onRequestStateChange?.();
              if (live) {
                // Flush a final unterminated line for runs recorded by an
                // older server; current servers publish complete SSE records.
                applyManagedPayload(live, "\n");
                const presentationFinished = finishManagedPresentation(live);
                if (presentationFinished) await presentationFinished;
              }
              terminal = true;
            }
          }
        }
        if (terminal) break;
        await new Promise((resolve) => setTimeout(resolve, request.retryDelayMs));
        request.retryDelayMs = Math.min(5000, request.retryDelayMs * 2);
      }
    } catch (error) {
      if (error?.name !== "AbortError") console.error("Managed run reconnect failed:", error);
    } finally {
      live?.scheduler.cancel();
      if (live) stepExecutionFeed.finishLiveTurn();
      if (isCurrent()) {
        // Load durable history while the live turn still owns visibility,
        // then swap owners in one synchronous handoff. If persistence is not
        // available, release without clearing the already rendered response.
        let restored = null;
        let durableReady = false;
        for (let attempt = 0; attempt < 4 && isCurrent(); attempt += 1) {
          restored = await loadSession(request.sessionId, request.owner);
          durableReady = latestConversationTurn(restored?.events || []).assistantEvents.length > 0;
          if (durableReady || attempt === 3) break;
          await new Promise((resolve) => setTimeout(resolve, 200 * (attempt + 1)));
        }
        if (restored && durableReady && isCurrent()) await handoffLiveTurn(request);
        else if (isCurrent()) releaseSessionRequest(request);
      }
    }
  }

  return {
    beginLiveOutput, beginManagedRunDiscovery, discardManagedRunDiscovery, getLiveHost, handoffLiveTurn,
    canRevealPlanApproval, discoverManagedRun, loadSession, renderSessionTimeline, resetTranscript,
    restorePlanApproval, restoreSessionSnapshot, startManagedRunReconnect, suppressPlanApproval,
    updateSessionWorkdirDisplay,
    metrics: () => ({ ...metrics, ...viewport.metrics(), totalEvents: activeContext?.store.totalCount || 0 }),
  };
}
