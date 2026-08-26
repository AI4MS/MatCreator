import {
  upsertTimelineEvent,
  upsertTimelineText,
  upsertTimelineThought,
} from "./timeline.js";

/** Coordinates a single composer request from optimistic UI through SSE completion. */
export function createMessageStreamController(deps) {
  const {
    state, appName, chatArea, textInput, activeSessionRequest, sessionRequestKey, activeSessionBackendUserId,
    canWriteActiveSession, showLoginModal, createSession, addMessage, addAgentTimelineMessage,
    addPlanApprovalActions, renderTimeline, messageWithUploadNames, messageWithUploadContext, clearCurrentUploads,
    autoResizeTextInput, stepExecutionFeed, agentGraph, planGraph, updateSendButtonState, updateAgentRunningStatus,
    attachAgentRunningIndicator,
    releaseSessionRequest, managedRunEventsUrl, shouldRefreshPlanGraphForTool,
    generateSessionSummary, refreshSessionFiles, sessionRuntime, showPlanGraph,
  } = deps;

  function renderStopStatus(request) {
    if (!request.stopStatus || sessionRequestKey(request.sessionId, request.owner) !== sessionRequestKey()) return;
    const content = request.stopStatus === "stopped" ? "✓ Execution stopped." : "Still executing…";
    if (!request.stopStatusMessage?.isConnected) {
      request.stopStatusMessage = addMessage("agent", content);
      return;
    }
    const inner = request.stopStatusMessage.querySelector(".markdown-content");
    if (inner) inner.textContent = content;
  }

  async function pollCancellationConfirmed(request, attempts = 0) {
    await new Promise((resolve) => setTimeout(resolve, attempts ? Math.min(1000 + attempts * 100, 3000) : 0));
    try {
      let run = null;
      if (request.runId) {
        const response = await fetch(`/api/runs/${encodeURIComponent(request.runId)}`);
        if (response.ok) run = await response.json();
      } else {
        const query = new URLSearchParams({ user_id: request.owner || state.userId, session_id: request.sessionId });
        const response = await fetch(`/api/runs/active?${query}`);
        if (response.ok) run = (await response.json()).run;
      }
      // A stop can be clicked while POST /api/runs is still returning. Give
      // active-run discovery a short grace period before treating "not found"
      // as terminal, so a just-created run cannot slip past the stop lock.
      if (!run && !request.runId && attempts < 3) {
        request.stopStatus = "waiting";
        renderStopStatus(request);
        void pollCancellationConfirmed(request, attempts + 1);
        return;
      }
      if (!run || ["completed", "failed", "cancelled"].includes(run.status)) {
        request.stopStatus = "stopped";
        releaseSessionRequest(request);
        if (sessionRequestKey(request.sessionId, request.owner) === sessionRequestKey()) {
          await sessionRuntime.loadSession(request.sessionId, request.owner);
        }
        renderStopStatus(request);
        return;
      }
      request.stopStatus = "waiting";
      renderStopStatus(request);
    } catch (_) {
      request.stopStatus = "waiting";
      renderStopStatus(request);
    }
    void pollCancellationConfirmed(request, attempts + 1);
  }

  function stop() {
    const request = activeSessionRequest();
    if (!request) return;
    sessionRuntime.suppressPlanApproval(request.sessionId);
    const query = new URLSearchParams({ user_id: request.owner || state.userId });
    fetch(`/api/sessions/${request.sessionId}/cancel?${query}`, { method: "POST" }).catch(() => {});
    request.stopStatus = "waiting";
    renderStopStatus(request);
    request.controller.abort();
    updateAgentRunningStatus("working");
    void pollCancellationConfirmed(request);
  }

  async function send(message) {
    if (!message.trim() || activeSessionRequest()) return;
    if (!state.userId) { showLoginModal(); return; }
    if (!canWriteActiveSession()) {
      addMessage("agent", `Admin view is read-only for ${state.activeSessionUserId}'s session.`);
      return;
    }
    const uploads = state.currentUploads.slice();
    const backendMessage = messageWithUploadContext(message, uploads);
    // Sending any reply consumes the currently displayed plan prompt.  Keep it
    // suppressed until this exact new user turn validates a fresh plan.
    sessionRuntime.suppressPlanApproval(state.sessionId, backendMessage);
    chatArea.querySelectorAll(".plan-approval-message").forEach((item) => item.remove());
    const cancellationQuery = new URLSearchParams({
      user_id: state.activeSessionUserId || state.userId,
    });
    try { await fetch(`/api/sessions/${state.sessionId}/cancel?${cancellationQuery}`, { method: "DELETE" }); } catch (_) {}
    const userMessage = addMessage("user", messageWithUploadNames(message, uploads));
    const startedAt = Date.now();
    textInput.value = "";
    clearCurrentUploads();
    autoResizeTextInput();
    if (!state.sessionReady) await createSession();
    if (!state.sessionReady) {
      addMessage("agent", "Failed to create session — the backend may still be loading. Please try again in a moment.");
      stepExecutionFeed.finishLiveTurn();
      return;
    }

    const timeline = [];
    const shownPlotPaths = new Set();
    const timelineContainer = addAgentTimelineMessage(timeline, shownPlotPaths, undefined, chatArea, {
      startedAt,
      live: true,
    });
    // The agent shell is visible immediately while the first SSE event is
    // pending, so the user sees progress in the conversational flow rather
    // than an isolated indicator above the composer.
    attachAgentRunningIndicator(timelineContainer);

    agentGraph.reset();
    planGraph.reset();
    const liveTurn = stepExecutionFeed.startLiveTurn(userMessage, startedAt, timelineContainer._stepFeedLiveHost);
    agentGraph.startPolling(state.sessionId);
    planGraph.startPolling(state.sessionId);
    const owner = state.activeSessionUserId || state.userId;
    // The optimistic user message and live assistant shell already represent
    // this session. Mark it before the first persisted snapshot arrives so
    // stop/plan-completion refreshes preserve the visible disclosure state.
    sessionRuntime.markSessionRendered(state.sessionId, owner);
    const request = {
      key: sessionRequestKey(state.sessionId, owner), sessionId: state.sessionId, owner,
      backendUserId: activeSessionBackendUserId(), controller: new AbortController(), lastSequence: 0, runId: null,
    };
    state.activeRequests.set(request.key, request);
    // Make connection progress explicit even before the agent has emitted its
    // first thought, tool call, or text token.  This is especially important
    // while a server-mode worker is starting.
    updateAgentRunningStatus("connecting");
    updateSendButtonState();

    let lineBuffer = "";
    let summaryTriggered = false;
    let validatedPlanThisTurn = false;
    let executionApprovedThisTurn = false;
    let terminalStatus = null;
    let roadmapOpenedForPlan = false;
    let pendingTimelineFrame = null;
    const renderPendingTimeline = () => {
      if (!timeline.length || pendingTimelineFrame !== null) return;
      // A single SSE message can contain several parts. Rendering each part
      // independently creates competing height changes in the activity and
      // assistant text; coalesce them into one browser frame instead.
      pendingTimelineFrame = requestAnimationFrame(() => {
        pendingTimelineFrame = null;
        if (timelineContainer.isConnected) renderTimeline(timelineContainer, timeline, shownPlotPaths);
      });
    };
    const flushPendingTimeline = () => {
      if (pendingTimelineFrame !== null) {
        cancelAnimationFrame(pendingTimelineFrame);
        pendingTimelineFrame = null;
      }
      if (timeline.length && timelineContainer.isConnected) {
        renderTimeline(timelineContainer, timeline, shownPlotPaths);
      }
    };
    const revealPlanApproval = () => {
      if (terminalStatus !== "completed" || !validatedPlanThisTurn || executionApprovedThisTurn
        || sessionRequestKey(request.sessionId, request.owner) !== sessionRequestKey()) return;
      if (!sessionRuntime.canRevealPlanApproval(request.sessionId, backendMessage)) return;
      // The terminal event is the safe handoff boundary: the backend no longer
      // owns this session, so approval can start a new run immediately. Do not
      // make the user wait for graph, file, and persisted-session refreshes.
      flushPendingTimeline();
      sessionRuntime.restorePlanApproval(request.sessionId);
      const latestTimeline = timelineContainer.isConnected
        ? timelineContainer
        : Array.from(chatArea.querySelectorAll(".agent-message .timeline-container")).at(-1);
      if (latestTimeline) addPlanApprovalActions(latestTimeline);
      // Open once at the completed-plan handoff, not when validate_graph first
      // creates the graph. Closing it afterward remains the user's choice.
      if (!roadmapOpenedForPlan) {
        roadmapOpenedForPlan = true;
        showPlanGraph();
      }
    };
    const handleAdkData = (data) => {
      if (data === "[DONE]") return;
      try {
        for (const part of JSON.parse(data)?.content?.parts || []) {
          if (part.thought) {
            updateAgentRunningStatus("thinking");
            upsertTimelineThought(timeline, part.text || "");
          } else if (part.functionCall) {
            const name = part.functionCall.name || "Unknown";
            updateAgentRunningStatus(phaseForTool(name));
            upsertTimelineEvent(timeline, { type: "function_call", id: part.functionCall.id, name, args: part.functionCall.args || {} });
          }
          else if (part.functionResponse) {
            const response = part.functionResponse;
            upsertTimelineEvent(timeline, { type: "function_response", id: response.id, name: response.name || "Unknown", response: response.response || {} });
            updateAgentRunningStatus(phaseForTool(response.name));
            if (shouldRefreshPlanGraphForTool(response.name)) planGraph.refresh(request.sessionId);
            if ((response.name === "validate_graph" || response.name === "validate_plan")
              && response.response?.status === "ok") {
              validatedPlanThisTurn = true;
              updateAgentRunningStatus("finalizing_plan");
            }
            if ((response.name === "confirm_plan_and_start_execution" || response.name === "resume_execution")
              && response.response?.status === "ok") executionApprovedThisTurn = true;
          } else if (part.text) {
            updateAgentRunningStatus(validatedPlanThisTurn && !executionApprovedThisTurn
              ? "finalizing_plan"
              : "thinking");
            upsertTimelineText(timeline, part.text);
            if (!summaryTriggered && !state.summaryGeneratedFor.has(request.sessionId) && !state.sessionSummaries[request.sessionId]) {
              summaryTriggered = true;
              generateSessionSummary(request.sessionId, request.owner);
            }
          }
          renderPendingTimeline();
        }
      } catch (_) { /* Ignore malformed backend events. */ }
    };
    const handleAdkChunk = (chunk) => {
      lineBuffer += chunk;
      const lines = lineBuffer.split("\n");
      lineBuffer = lines.pop();
      lines.forEach((line) => { const trimmed = line.trim(); if (trimmed.startsWith("data: ")) handleAdkData(trimmed.slice(6)); });
    };
    const reloadSessionSnapshot = async () => {
      const newerRequestIsActive = () => {
        const active = activeSessionRequest();
        return active && active !== request;
      };
      if (newerRequestIsActive()) return;
      // ADK may close the managed SSE stream before its session database has
      // received the final events. Do not let such an incomplete snapshot
      // erase the optimistic user message and already-streamed agent reply.
      const restored = await sessionRuntime.loadSession(request.sessionId, request.owner, { render: false });
      if (newerRequestIsActive()) return;
      const events = restored?.events || [];
      const userEventIndex = events.findIndex((event) => event?.author === "user"
        && (event.content?.parts || []).some((part) => String(part.text || "").includes(backendMessage)));
      const hasPersistedReply = userEventIndex >= 0 && events.slice(userEventIndex + 1)
        .some((event) => event?.author !== "user" && (event.content?.parts || []).length);

      if (hasPersistedReply) {
        await sessionRuntime.loadSession(request.sessionId, request.owner);
      } else if (!userMessage.isConnected
        && sessionRequestKey(request.sessionId, request.owner) === sessionRequestKey()) {
        chatArea.prepend(userMessage);
      }
    };

    try {
      const startResponse = await fetch("/api/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: request.controller.signal,
        body: JSON.stringify({ app_name: appName, user_id: request.backendUserId, session_id: request.sessionId, new_message: { role: "user", parts: [{ text: backendMessage }] } }),
      });
      if (!startResponse.ok) throw new Error(`HTTP ${startResponse.status}`);
      request.runId = (await startResponse.json()).run_id;
      const eventsResponse = await fetch(managedRunEventsUrl(request), { headers: { Accept: "text/event-stream" }, signal: request.controller.signal });
      if (!eventsResponse.ok) throw new Error(`HTTP ${eventsResponse.status}`);
      updateAgentRunningStatus("connected");
      const reader = eventsResponse.body.getReader();
      const decoder = new TextDecoder();
      let eventBuffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        eventBuffer += decoder.decode(value, { stream: true });
        const lines = eventBuffer.split("\n");
        eventBuffer = lines.pop();
        for (const line of lines) {
          if (!line.trim().startsWith("data: ")) continue;
          let event;
          try {
            event = JSON.parse(line.trim().slice(6));
          } catch (_) {
            continue; // Ignore malformed SSE messages.
          }
          if (event.type === "event") { request.lastSequence = event.sequence || request.lastSequence; handleAdkChunk(event.data || ""); }
          else if (event.type === "snapshot_required") await reloadSessionSnapshot();
          else if (event.type === "terminal") {
            request.lastSequence = event.latest_sequence || request.lastSequence;
            terminalStatus = event.status;
            if (event.status === "failed") throw new Error(event.error || "Agent run failed");
            if (event.status === "completed") {
              releaseSessionRequest(request);
              stepExecutionFeed.finishLiveTurn();
              revealPlanApproval();
            }
          }
        }
      }
      if (lineBuffer.trim().startsWith("data: ")) handleAdkData(lineBuffer.trim().slice(6));
    } catch (error) {
      if (error?.name !== "AbortError") addMessage("agent", `Backend error: ${error}`, undefined, liveTurn);
    } finally {
      timelineContainer.finishAgentDuration?.();
      // A cancelled browser subscription can finish before the managed run
      // does. Keep the composer locked until cancellation polling observes a
      // terminal run, otherwise a new send would clear the cancellation flag.
      if (request.stopStatus !== "waiting") releaseSessionRequest(request);
      stepExecutionFeed.finishLiveTurn();
      revealPlanApproval();
      // These are independent reconciliation tasks. They keep the durable
      // session, roadmap, agent graph, and files current, but none is required
      // before the user can act on a completed plan.
      await Promise.allSettled([
        agentGraph._poll(request.sessionId),
        planGraph._poll(request.sessionId),
        refreshSessionFiles(request.sessionId, request.owner),
        reloadSessionSnapshot(),
      ]);
      agentGraph.stopPolling();
      planGraph.stopPolling();
      // A session snapshot replaces the chat DOM. Restore the stop indicator
      // from the request state so cancellation feedback is never lost during
      // the final refresh.
      renderStopStatus(request);
      // Do not depend on session DB timing for the prompt.  The live ADK
      // response is authoritative; the persisted snapshot remains a fallback
      // for page refreshes and reconnects.
      revealPlanApproval();
    }
  }

  function phaseForTool(name = "") {
    const tool = String(name).toLowerCase();
    if (tool.includes("search") || tool.includes("retrieve") || tool.includes("lookup")) return "searching";
    if (tool.includes("plan") || tool.includes("graph") || tool.includes("decompos")) return "planning";
    if (tool.includes("run_") || tool.includes("execute") || tool.includes("submit") || tool.includes("resume")) return "executing";
    if (tool.includes("calc") || tool.includes("simulate") || tool.includes("compute")) return "computing";
    return "working";
  }

  return { send, stop };
}
