import {
  applyAssistantMessagePart,
  completeAssistantMessage,
  createAssistantMessage,
} from "./timeline.js";
import { createMessageRenderScheduler, messageRenderInterval } from "./messageRenderScheduler.js";

/**
 * Coordinates a single composer request from optimistic UI through SSE completion.
 *
 * Grouping collaborators here makes the controller's responsibilities
 * (session state, composer UI including uploads, and execution UI) visible at
 * its API.
 */
export function createMessageStreamController({
  session: {
    state,
    activeRequest: activeSessionRequest,
    requestKey: sessionRequestKey,
    backendUserId: activeSessionBackendUserId,
    canWrite: canWriteActiveSession,
    create: createSession,
    releaseRequest: releaseSessionRequest,
    runtime: sessionRuntime,
    refreshFiles: refreshSessionFiles,
    generateSummary: generateSessionSummary,
  },
  composer: {
    appName,
    chatArea,
    textInput,
    showLoginModal,
    addMessage,
    addAgentTimelineMessage,
    addPlanApprovalActions,
    renderTimeline,
    messageWithUploadNames,
    messageWithUploadContext,
    clearCurrentUploads,
    autoResizeTextInput,
  },
  execution: {
    stepExecutionFeed,
    agentGraph,
    planGraph,
    updateSendButtonState,
    updateAgentRunningStatus,
    attachAgentRunningIndicator,
    managedRunEventsUrl,
    shouldRefreshPlanGraphForTool,
    showPlanGraph,
  },
}) {

  function renderStopStatus(request) {
    if (!request.stopStatus || sessionRequestKey(request.sessionId, request.owner) !== sessionRequestKey()) return;
    const content = request.stopStatus === "stopped" ? "✓ Execution stopped." : "Still executing…";
    if (!request.stopStatusMessage?.isConnected) {
      request.stopStatusMessage = addMessage("agent", content, undefined, sessionRuntime.getLiveHost());
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
      addMessage("agent", `Admin view is read-only for ${state.activeSessionUserId}'s session.`, undefined, sessionRuntime.getLiveHost());
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
    const liveHost = sessionRuntime.beginLiveOutput();
    const userMessage = addMessage("user", messageWithUploadNames(message, uploads), undefined, liveHost);
    const startedAt = Date.now();
    textInput.value = "";
    clearCurrentUploads();
    autoResizeTextInput();
    if (!state.sessionReady) await createSession();
    if (!state.sessionReady) {
      addMessage("agent", "Failed to create session — the backend may still be loading. Please try again in a moment.", undefined, liveHost);
      stepExecutionFeed.finishLiveTurn();
      return;
    }

    const assistantMessage = createAssistantMessage({
      id: `live:${state.sessionId}:${startedAt}`,
      startedAt,
    });
    const shownPlotPaths = new Set();
    const messageView = addAgentTimelineMessage(assistantMessage, shownPlotPaths, undefined, liveHost, {
      startedAt,
      live: true,
    });
    // The agent shell is visible immediately while the first SSE event is
    // pending, so the user sees progress in the conversational flow rather
    // than an isolated indicator above the composer.
    attachAgentRunningIndicator(messageView);

    agentGraph.reset();
    planGraph.reset();
    stepExecutionFeed.startLiveTurn(userMessage, startedAt, messageView.stepFeedLiveHost);
    agentGraph.startPolling(state.sessionId);
    planGraph.startPolling(state.sessionId);
    const owner = state.activeSessionUserId || state.userId;
    const request = {
      key: sessionRequestKey(state.sessionId, owner), sessionId: state.sessionId, owner,
      backendUserId: activeSessionBackendUserId(), controller: new AbortController(), lastSequence: 0, runId: null,
      message: assistantMessage, messageView, userMessage, backendMessage,
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
    const renderScheduler = createMessageRenderScheduler({
      intervalMs: () => messageRenderInterval(assistantMessage.items
        .filter((item) => item.type === "text" || item.type === "reasoning")
        .reduce((total, item) => total + String(item.text || "").length, 0)),
      render: () => {
        if (messageView.element.isConnected) renderTimeline(messageView, assistantMessage, shownPlotPaths);
      },
    });
    let presentationFinishPromise = null;
    const finishLivePresentation = () => {
      if (presentationFinishPromise) return presentationFinishPromise;
      completeAssistantMessage(assistantMessage);
      messageView.finishDuration(assistantMessage.endedAt);
      const activityFinish = messageView.finishLiveActivity();
      // Status teardown is derived from the canonical lifecycle and happens
      // before the final Markdown task is scheduled.
      updateSendButtonState();
      presentationFinishPromise = Promise.all([activityFinish, renderScheduler.finish()]);
      return presentationFinishPromise;
    };
    const revealPlanApproval = () => {
      if (terminalStatus !== "completed" || !validatedPlanThisTurn || executionApprovedThisTurn
        || sessionRequestKey(request.sessionId, request.owner) !== sessionRequestKey()) return;
      if (!sessionRuntime.canRevealPlanApproval(request.sessionId, backendMessage)) return;
      // The terminal event is the safe handoff boundary: the backend no longer
      // owns this session, so approval can start a new run immediately. Do not
      // make the user wait for graph, file, and persisted-session refreshes.
      sessionRuntime.restorePlanApproval(request.sessionId);
      const latestTimeline = messageView.element.isConnected
        ? messageView
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
      if (data === "[DONE]") {
        // The model has finished emitting text, even though the managed run
        // can remain active briefly while the session is persisted. Commit
        // the final scheduled text frame now, then finish the *visible* turn.
        // The request stays locked until its terminal event, but presentation
        // no longer depends on that slower durable-write lifecycle.
        void finishLivePresentation();
        return;
      }
      try {
        for (const part of JSON.parse(data)?.content?.parts || []) {
          const normalized = applyAssistantMessagePart(assistantMessage, part);
          if (!normalized) continue;
          if (part.thought) {
            updateAgentRunningStatus("thinking");
          } else if (normalized.type === "function_call") {
            const name = normalized.name;
            updateAgentRunningStatus(phaseForTool(name));
          }
          else if (normalized.type === "function_response") {
            const response = normalized;
            updateAgentRunningStatus(phaseForTool(response.name));
            if (shouldRefreshPlanGraphForTool(response.name)) planGraph.refresh(request.sessionId);
            if ((response.name === "validate_graph" || response.name === "validate_plan")
              && response.response?.status === "ok") {
              validatedPlanThisTurn = true;
              updateAgentRunningStatus("finalizing_plan");
            }
            if ((response.name === "confirm_plan_and_start_execution" || response.name === "resume_execution")
              && response.response?.status === "ok") executionApprovedThisTurn = true;
          } else if (normalized.type === "text") {
            updateAgentRunningStatus(validatedPlanThisTurn && !executionApprovedThisTurn
              ? "finalizing_plan"
              : "thinking");
            if (!summaryTriggered && !state.summaryGeneratedFor.has(request.sessionId) && !state.sessionSummaries[request.sessionId]) {
              summaryTriggered = true;
              generateSessionSummary(request.sessionId, request.owner);
            }
          }
          renderScheduler.request();
        }
      } catch (_) { /* Ignore malformed backend events. */ }
    };
    const handleAdkChunk = (chunk) => {
      lineBuffer += chunk;
      const lines = lineBuffer.split("\n");
      lineBuffer = lines.pop();
      lines.forEach((line) => { const trimmed = line.trim(); if (trimmed.startsWith("data: ")) handleAdkData(trimmed.slice(6)); });
    };
    const reloadSessionSnapshot = async ({ handoff = false } = {}) => {
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
      // Always identify the most recent matching user event. `findIndex`
      // matched an older identical prompt (notably "yes"), then saw that old
      // prompt's reply and incorrectly replaced the active live turn.
      const userEventIndex = events.findLastIndex((event) => event?.author === "user"
        && (event.content?.parts || []).some((part) => String(part.text || "").includes(backendMessage)));
      const hasPersistedReply = userEventIndex >= 0 && events.slice(userEventIndex + 1)
        .some((event) => event?.author !== "user" && (event.content?.parts || []).length);

      if (handoff && hasPersistedReply) {
        await sessionRuntime.loadSession(request.sessionId, request.owner);
        return true;
      }
      if (handoff && !userMessage.isConnected
        && sessionRequestKey(request.sessionId, request.owner) === sessionRequestKey()) {
        sessionRuntime.getLiveHost().prepend(userMessage);
      }
      return false;
    };
    const reconcileDurableTurn = async () => {
      // A terminal notification may slightly precede the database commit.
      // Retry the durable handoff in the background, without ever mounting a
      // second response or disturbing the completed live presentation.
      for (let attempt = 0; attempt < 4; attempt += 1) {
        if (await reloadSessionSnapshot({ handoff: true })) return true;
        if (activeSessionRequest() && activeSessionRequest() !== request) return false;
        if (attempt < 3) await new Promise((resolve) => window.setTimeout(resolve, 200 * (attempt + 1)));
      }
      return false;
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
          // Snapshot recovery keeps the live presentation authoritative. Only
          // the final handoff below is allowed to mount durable history.
          else if (event.type === "snapshot_required") await reloadSessionSnapshot();
          else if (event.type === "terminal") {
            request.lastSequence = event.latest_sequence || request.lastSequence;
            terminalStatus = event.status;
            if (event.status === "failed") throw new Error(event.error || "Agent run failed");
            if (event.status === "completed") {
              void finishLivePresentation();
              releaseSessionRequest(request);
              stepExecutionFeed.finishLiveTurn();
              revealPlanApproval();
            }
          }
        }
      }
      if (lineBuffer.trim().startsWith("data: ")) handleAdkData(lineBuffer.trim().slice(6));
    } catch (error) {
      if (error?.name !== "AbortError") addMessage("agent", `Backend error: ${error}`, undefined, liveHost);
    } finally {
      // Every exit converges on the same idempotent lifecycle transition and
      // one guaranteed final affected-message render.
      const presentationFinished = finishLivePresentation();
      // A cancelled browser subscription can finish before the managed run
      // does. Keep the composer locked until cancellation polling observes a
      // terminal run, otherwise a new send would clear the cancellation flag.
      if (request.stopStatus !== "waiting") releaseSessionRequest(request);
      stepExecutionFeed.finishLiveTurn();
      revealPlanApproval();
      // The final affected-message pass is the handoff boundary. Durable
      // history cannot mount a competing representation before it completes.
      const reconcileAfterTransition = async () => {
        await presentationFinished;
        return reconcileDurableTurn();
      };
      // These are independent reconciliation tasks. They keep the durable
      // session, roadmap, agent graph, and files current, but none is required
      // before the user can act on a completed plan.
      await Promise.allSettled([
        agentGraph._poll(request.sessionId),
        planGraph._poll(request.sessionId),
        refreshSessionFiles(request.sessionId, request.owner),
        reconcileAfterTransition(),
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
