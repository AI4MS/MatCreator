import {
  upsertTimelineEvent,
  upsertTimelineText,
  upsertTimelineThought,
} from "../chat/timeline.js";

/** Owns restoring a persisted session and reconnecting to an active managed run. */
export function createSessionRuntime({
  state,
  chatArea,
  stepExecutionFeed,
  sessionRequestKey,
  activeSessionRequest,
  releaseSessionRequest,
  updateSendButtonState,
  managedRunEventsUrl,
  isExecutorLauncherTool,
  getFunctionResponse,
  displayMessageFromStoredUserText,
  addMessage,
  addAgentTimelineMessage,
  addPlanApprovalActions,
  beginScrollTransaction,
  endScrollTransaction,
  renderSessionBanner,
  renderSessionFilesTree,
  refreshSessionFiles,
  generateSessionSummary,
  workdirDisplay,
}) {
  let renderedSessionKey = null;
  // Plan approval is UI-derived from persisted events. A cancelled turn can
  // still contain a successful validation event, so remember that its prompt
  // was dismissed until the user deliberately starts another turn.
  const suppressedPlanApprovalTurns = new Map();

  async function fetchSessionData(sessionId) {
    const owner = state.activeSessionUserId || state.userId;
    const response = await fetch(`/api/users/${encodeURIComponent(owner)}/sessions/${encodeURIComponent(sessionId)}`, {
      headers: { "Content-Type": "application/json" },
    });
    return response.ok ? response.json() : null;
  }

  async function fetchSessionStepNodes(sessionId) {
    try {
      const response = await fetch(`/api/agent-graph/${encodeURIComponent(sessionId)}`);
      if (!response.ok) return [];
      const graph = await response.json();
      return Object.values(graph.nodes || {})
        .filter((node) => node.type === "step")
        .sort((left, right) => stepNodeTimestamp(left) - stepNodeTimestamp(right));
    } catch (_) {
      return [];
    }
  }

  function eventTimestamp(event, fallbackOrder) {
    if (event.timestamp) {
      const raw = Number(event.timestamp);
      return raw < 1e12 ? raw * 1000 : raw;
    }
    return event.createTime ? new Date(event.createTime).getTime() : fallbackOrder;
  }

  function stepNodeTimestamp(node) {
    return node.start_time ? new Date(node.start_time).getTime() : Infinity;
  }

  function collectFunctionResponsesById(events) {
    const responses = {};
    for (const event of events) {
      for (const part of event.content?.parts || []) {
        const response = getFunctionResponse(part);
        if (response?.id) responses[response.id] = response;
      }
    }
    return responses;
  }

  function eventToTimelineParts(event, responsesById, pairedResponseIds = new Set(), timeline = []) {
    for (const part of event.content?.parts || []) {
      if (part.thought) {
        upsertTimelineThought(timeline, part.text || "");
      } else if (part.functionCall || part.function_call) {
        const call = part.functionCall || part.function_call;
        const matchedResponse = responsesById[call.id];
        upsertTimelineEvent(timeline, { type: "function_call", id: call.id, name: call.name || "Unknown", args: call.args || {} });
        if (matchedResponse) {
          if (matchedResponse.id) pairedResponseIds.add(matchedResponse.id);
          upsertTimelineEvent(timeline, { type: "function_response", id: matchedResponse.id, name: matchedResponse.name || "Unknown", response: matchedResponse.response || {} });
        }
      } else if (getFunctionResponse(part)) {
        const response = getFunctionResponse(part);
        if (response.id && pairedResponseIds.has(response.id)) continue;
        upsertTimelineEvent(timeline, { type: "function_response", id: response.id, name: response.name || "Unknown", response: response.response || {} });
      } else if (part.text) {
        upsertTimelineText(timeline, part.text);
      }
    }
    return timeline;
  }

  function attachStepNodes(timeline, pendingStepNodes) {
    const launcherCalls = timeline
      .filter((item) => item.type === "activity_action")
      .flatMap((action) => action.toolCalls || [])
      .filter((call) => isExecutorLauncherTool(call.name));
    if (!launcherCalls.length) return timeline;

    // A resumed session may contain several parallel run_node_executor calls
    // in one assistant event. Match their persisted step cards by node ID
    // first; consuming them one-at-a-time made the remaining cards fall back
    // to the chat root when the session was switched mid-run.
    for (const item of launcherCalls) {
      const requestedNodeId = item.args?.node_id;
      const matchIndex = pendingStepNodes.findIndex((node) =>
        requestedNodeId && (node.input?.node_id === requestedNodeId || node.id?.endsWith(`__node_${requestedNodeId}`)),
      );
      if (matchIndex >= 0) {
        const [node] = pendingStepNodes.splice(matchIndex, 1);
        item.stepNodes = [node];
      }
    }

    // Older runs and flash-step calls do not always carry a stable node ID.
    // Keep their cards inside an executor call bubble using chronological
    // fallback, rather than appending standalone agent-message cards.
    for (const item of launcherCalls) {
      if (item.stepNodes?.length) continue;
      const nextStep = pendingStepNodes.shift();
      if (nextStep) item.stepNodes = [nextStep];
    }
    return timeline;
  }

  function latestTurnPendingPlan(events) {
    const latestUserIndex = events.reduce((index, event, current) => event?.author === "user" ? current : index, -1);
    if (latestUserIndex < 0) return null;
    let pendingPlan = null;
    events.slice(latestUserIndex + 1).forEach((event) => {
      (event.content?.parts || []).forEach((part) => {
        const response = getFunctionResponse(part);
        if ((response?.name === "validate_graph" || response?.name === "validate_plan")
          && response.response?.status === "ok") {
          pendingPlan = { event, response, userEvent: events[latestUserIndex] };
        } else if ((response?.name === "confirm_plan_and_start_execution" || response?.name === "resume_execution")
          && response.response?.status === "ok") {
          // Approval consumes the most recently validated plan. This must be
          // derived in event order because a persisted snapshot can contain
          // both validation and approval responses from the same user turn.
          pendingPlan = null;
        }
      });
    });
    return pendingPlan;
  }

  function shouldShowPlanApprovalActions(sessionId, sessionData, events) {
    // This is deliberately UI-derived state. A validation creates a pending
    // prompt, while a later approval in the same turn consumes it.
    const state = sessionData?.state || {};
    const pendingPlan = latestTurnPendingPlan(events);
    const suppressedTurn = suppressedPlanApprovalTurns.get(sessionId);
    const latestUserText = (pendingPlan?.userEvent?.content?.parts || [])
      .map((part) => part.text || "").join("");
    const validationBelongsToNewTurn = !suppressedTurn
      || (suppressedTurn.userText && latestUserText === suppressedTurn.userText);
    return (state.agent_mode || "normal") === "normal"
      && pendingPlan !== null
      && validationBelongsToNewTurn;
  }

  function suppressPlanApproval(sessionId, userText = "") {
    if (sessionId) suppressedPlanApprovalTurns.set(sessionId, { userText });
  }

  function restorePlanApproval(sessionId) {
    if (sessionId) suppressedPlanApprovalTurns.delete(sessionId);
  }

  function markSessionRendered(sessionId, owner = state.activeSessionUserId || state.userId) {
    renderedSessionKey = sessionRequestKey(sessionId, owner);
  }

  function renderSessionTimeline(events, stepNodes, awaitingPlanApproval = false, preserveDisclosures = false) {
    beginScrollTransaction();
    try {
      // Running cards are initially open by default, so their state is not in
      // the user-choice map yet. Snapshot the actual DOM before an in-place
      // refresh changes running nodes to completed/cancelled and changes that
      // default to closed.
      if (preserveDisclosures) stepExecutionFeed.captureDisclosureState();
      chatArea.innerHTML = "";
      stepExecutionFeed.reset({ preserveDisclosures });
      stepExecutionFeed.setHierarchy(stepNodes || []);
    const sortedEvents = (events || []).map((event, index) => ({ event, timestamp: eventTimestamp(event, index), index }))
      .sort((left, right) => left.timestamp - right.timestamp || left.index - right.index).map(({ event }) => event);
    const pendingStepNodes = (stepNodes || []).filter((node) => stepExecutionFeed.isRootStep(node)).slice()
      .sort((left, right) => stepNodeTimestamp(left) - stepNodeTimestamp(right));
    const responsesById = collectFunctionResponsesById(events || []);
    const pairedResponseIds = new Set();
    let shownPlotPaths = new Set();
    let messageIndex = 0;
    let lastAgentTimeline = null;
    let pendingAgentTimeline = [];
    const flushAgentTimeline = () => {
      if (!pendingAgentTimeline.length) return;
      const timeline = attachStepNodes(pendingAgentTimeline, pendingStepNodes);
      lastAgentTimeline = addAgentTimelineMessage(timeline, shownPlotPaths, messageIndex++);
      pendingAgentTimeline = [];
    };

    for (const event of sortedEvents) {
      if (event.author === "user") {
        flushAgentTimeline();
        const text = displayMessageFromStoredUserText((event.content?.parts || []).map((part) => part.text || "").join(""));
        if (text) addMessage("user", text, messageIndex++);
        shownPlotPaths = new Set();
        continue;
      }
      // Persisted ADK events are often split into separate records while the
      // managed SSE stream delivers them as one assistant turn. Accumulating
      // adjacent non-user events gives history and live output identical
      // Thinking / IN / OUT grouping, including the same upsert semantics.
      eventToTimelineParts(event, responsesById, pairedResponseIds, pendingAgentTimeline);
    }
    flushAgentTimeline();
    // Preserve the assistant-message containment even if an older/incomplete
    // persisted event stream cannot be matched to a specific executor call.
    // This is particularly important while reconnecting after a session
    // switch: standalone cards become visual siblings of the chat bubble.
    if (pendingStepNodes.length) {
      // A session can be switched to after the backend has recorded its user
      // turn and graph nodes, but before it has persisted the assistant's
      // executor function-call event. Provide that in-flight turn with a real
      // bubble instead of rendering the recovered cards as chat-root siblings.
      if (!lastAgentTimeline) {
        lastAgentTimeline = addAgentTimelineMessage([], shownPlotPaths, messageIndex++);
      }
      const fallbackHost = document.createElement("div");
      fallbackHost.className = "step-feed-inline-region";
      lastAgentTimeline.appendChild(fallbackHost);
      pendingStepNodes.forEach((node) => stepExecutionFeed.appendStatic(node, fallbackHost));
    }
      if (awaitingPlanApproval && lastAgentTimeline) addPlanApprovalActions(lastAgentTimeline);
    } finally {
      // Loading/polling a persisted snapshot is passive. The approval card is
      // visible immediately only for an already attached viewport; a reader
      // elsewhere keeps the same anchor and can reach it deliberately.
      endScrollTransaction();
    }
  }

  function updateSessionWorkdirDisplay(sessionData) {
    if (!workdirDisplay) return;
    const workdir = sessionData.state?.workdir || sessionData.state?.custom_workdir || state.defaultWorkdir || "";
    workdirDisplay.textContent = workdir;
    workdirDisplay.style.display = workdir ? "" : "none";
  }

  async function loadSession(sessionId, owner = state.activeSessionUserId || state.userId, { render = true } = {}) {
    const viewKey = sessionRequestKey(sessionId, owner);
    const requestAtStart = activeSessionRequest();
    const isCurrentView = () => sessionRequestKey() === viewKey;
    try {
      const [sessionData, graphNodes] = await Promise.all([fetchSessionData(sessionId), fetchSessionStepNodes(sessionId)]);
      if (!sessionData) {
        if (isCurrentView()) state.sessionReady = false;
        return;
      }
      if (!isCurrentView()) return;
      state.sessionReady = true;
      if (state.deploymentMode === "local" && sessionData.userId) state.activeSessionUserId = sessionData.userId;
      const events = sessionData.events || [];
      if (sessionData.summary) {
        state.sessionSummaries[sessionId] = sessionData.summary;
        state.summaryGeneratedFor.add(sessionId);
      }
      const summary = sessionData.summary || state.sessionSummaries[sessionId] || "";
      const preserveDisclosures = renderedSessionKey === viewKey;
      // Snapshot probing (`render: false`) is the first half of the managed
      // stream reload. Remember its view so the following rendered snapshot
      // is treated as an in-place refresh, including the refresh immediately
      // before the Approve plan prompt appears.
      renderedSessionKey = viewKey;
      if (render) {
        renderSessionBanner(summary);
        renderSessionTimeline(
          events,
          graphNodes,
          shouldShowPlanApprovalActions(sessionId, sessionData, events),
          preserveDisclosures,
        );
      }
      state.sessionViewCache.set(viewKey, { sessionData, events, graphNodes, files: [], summary });
      if (state.sessionViewCache.size > 10) state.sessionViewCache.delete(state.sessionViewCache.keys().next().value);
      if (render && events.some((event) => event?.author === "user") && !summary && !state.summaryGeneratedFor.has(sessionId)) {
        generateSessionSummary(sessionId);
      }
      if (requestAtStart && requestAtStart !== activeSessionRequest()) return;
      if (render) {
        void refreshSessionFiles(sessionId, owner);
        updateSessionWorkdirDisplay(sessionData);
      }
      return { sessionData, events, graphNodes, summary };
    } catch (error) {
      console.error("Failed to load session:", error);
    }
  }

  async function discoverManagedRun(sessionId, owner = state.activeSessionUserId || state.userId) {
    if (!owner || !sessionId) return null;
    try {
      const query = new URLSearchParams({ user_id: owner, session_id: sessionId });
      const response = await fetch(`/api/runs/active?${query}`);
      if (!response.ok) return null;
      return (await response.json()).run || null;
    } catch (_) {
      return null;
    }
  }

  const MANAGED_RUN_RETRY_INITIAL_DELAY_MS = 500;
  const MANAGED_RUN_RETRY_MAX_DELAY_MS = 5000;
  const MANAGED_RUN_REFRESH_DELAY_MS = 250;

  function startManagedRunReconnect(activeRun, sessionId, owner = state.activeSessionUserId || state.userId) {
    if (!activeRun?.run_id) return;
    const key = sessionRequestKey(sessionId, owner);
    if (state.activeRequests.get(key)) return;
    const request = {
      key,
      sessionId,
      owner,
      backendUserId: owner,
      controller: new AbortController(),
      lastSequence: activeRun.latest_sequence || 0,
      runId: activeRun.run_id,
      refreshTimer: null,
      retryDelayMs: MANAGED_RUN_RETRY_INITIAL_DELAY_MS,
    };
    state.activeRequests.set(key, request);
    updateSendButtonState();
    void streamManagedRunEvents(request);
  }

  function isCurrentManagedRunRequest(request) {
    return state.activeRequests.get(request.key) === request;
  }

  function scheduleManagedRunRefresh(request, { immediate = false } = {}) {
    if (!isCurrentManagedRunRequest(request)) return;
    if (request.refreshTimer !== null) {
      if (!immediate) return;
      clearTimeout(request.refreshTimer);
    }
    const refresh = async () => {
      request.refreshTimer = null;
      if (isCurrentManagedRunRequest(request)) {
        await loadSession(request.sessionId, request.owner);
      }
    };
    request.refreshTimer = setTimeout(refresh, immediate ? 0 : MANAGED_RUN_REFRESH_DELAY_MS);
  }

  async function managedRunStillActive(request) {
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(request.runId)}`);
      if (response.ok) {
        const run = await response.json();
        if (["starting", "running", "cancelling"].includes(run.status)) return true;
        return false;
      }
      if (response.status !== 404) return true;
    } catch (_) {
      return true;
    }
    const activeRun = await discoverManagedRun(request.sessionId, request.owner);
    if (activeRun?.run_id) {
      request.runId = activeRun.run_id;
      request.lastSequence = activeRun.latest_sequence || 0;
      return true;
    }
    return false;
  }

  async function waitForManagedRunRetry(request) {
    const delay = request.retryDelayMs;
    request.retryDelayMs = Math.min(delay * 2, MANAGED_RUN_RETRY_MAX_DELAY_MS);
    await new Promise((resolve) => setTimeout(resolve, delay));
    return isCurrentManagedRunRequest(request) && !request.controller.signal.aborted;
  }

  async function streamManagedRunEvents(request) {
    let shouldRetry = true;
    try {
      while (shouldRetry && isCurrentManagedRunRequest(request) && !request.controller.signal.aborted) {
        try {
          const response = await fetch(managedRunEventsUrl(request), {
            headers: { Accept: "text/event-stream" },
            signal: request.controller.signal,
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let terminal = false;
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();
            for (const line of lines) {
              if (!line.trim().startsWith("data: ")) continue;
              try {
                const event = JSON.parse(line.trim().slice(6));
                if (event.type === "event") {
                  request.lastSequence = event.sequence || request.lastSequence;
                  request.retryDelayMs = MANAGED_RUN_RETRY_INITIAL_DELAY_MS;
                  scheduleManagedRunRefresh(request);
                } else if (event.type === "snapshot_required") {
                  request.lastSequence = event.latest_sequence || request.lastSequence;
                  scheduleManagedRunRefresh(request, { immediate: true });
                } else if (event.type === "terminal") {
                  request.lastSequence = event.latest_sequence || request.lastSequence;
                  terminal = true;
                }
              } catch (_) { /* Ignore malformed SSE events. */ }
            }
          }
          if (terminal) {
            shouldRetry = false;
            continue;
          }
        } catch (error) {
          if (error?.name === "AbortError") break;
        }
        if (!await managedRunStillActive(request) || !await waitForManagedRunRetry(request)) {
          shouldRetry = false;
        }
      }
    } finally {
      if (request.refreshTimer !== null) clearTimeout(request.refreshTimer);
      if (isCurrentManagedRunRequest(request)) {
        releaseSessionRequest(request);
        await loadSession(request.sessionId, request.owner);
      }
    }
  }

  return {
    discoverManagedRun,
    loadSession,
    markSessionRendered,
    renderSessionTimeline,
    restorePlanApproval,
    startManagedRunReconnect,
    suppressPlanApproval,
    updateSessionWorkdirDisplay,
  };
}
