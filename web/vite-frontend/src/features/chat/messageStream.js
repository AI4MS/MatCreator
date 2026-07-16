import {
  compactRepeatedPrefixSnapshots,
  mergeReplayedText,
  upsertTimelineEvent,
  upsertTimelineText,
  upsertTimelineThought,
} from "./timeline.js";

/** Coordinates a single composer request from optimistic UI through SSE completion. */
export function createMessageStreamController(deps) {
  const {
    state, appName, chatArea, textInput, activeSessionRequest, sessionRequestKey, activeSessionBackendUserId,
    canWriteActiveSession, showLoginModal, createSession, addMessage, addAgentTimelineMessage,
    renderTimeline, messageWithUploadNames, messageWithUploadContext, clearCurrentUploads,
    autoResizeTextInput, stepExecutionFeed, agentGraph, planGraph, updateSendButtonState,
    releaseSessionRequest, managedRunEventsUrl, shouldRefreshPlanGraphForTool,
    generateSessionSummary, refreshSessionFiles, sessionRuntime,
  } = deps;

  function pollCancellationConfirmed(sessionId, attempts = 0) {
    if (attempts >= 20) {
      addMessage("agent", "⚠️ Stop requested but execution may still be running in the background.");
      return;
    }
    setTimeout(async () => {
      try {
        const response = await fetch(`/api/sessions/${sessionId}/cancel`);
        const result = await response.json();
        if (!result.cancellation_requested) {
          addMessage("agent", "✓ Execution stopped.");
          return;
        }
      } catch (_) { /* Ignore transient network errors. */ }
      pollCancellationConfirmed(sessionId, attempts + 1);
    }, 2000);
  }

  function stop() {
    const request = activeSessionRequest();
    if (!request) return;
    fetch(`/api/sessions/${state.sessionId}/cancel`, { method: "POST" }).catch(() => {});
    request.controller.abort();
    pollCancellationConfirmed(state.sessionId);
  }

  async function send(message) {
    if (!message.trim() || activeSessionRequest()) return;
    if (!state.userId) { showLoginModal(); return; }
    if (!canWriteActiveSession()) {
      addMessage("agent", `Admin view is read-only for ${state.activeSessionUserId}'s session.`);
      return;
    }
    try { await fetch(`/api/sessions/${state.sessionId}/cancel`, { method: "DELETE" }); } catch (_) {}

    const uploads = state.currentUploads.slice();
    const userMessage = addMessage("user", messageWithUploadNames(message, uploads));
    const startedAt = Date.now();
    const backendMessage = messageWithUploadContext(message, uploads);
    textInput.value = "";
    clearCurrentUploads();
    autoResizeTextInput();
    if (!state.sessionReady) await createSession();
    if (!state.sessionReady) {
      addMessage("agent", "Failed to create session — the backend may still be loading. Please try again in a moment.");
      stepExecutionFeed.finishLiveTurn();
      return;
    }

    const previousPlanGraphKey = planGraph.currentGraphKey();
    agentGraph.reset();
    planGraph.reset();
    const liveTurn = stepExecutionFeed.startLiveTurn(userMessage, startedAt);
    agentGraph.startPolling(state.sessionId);
    planGraph.startPolling(state.sessionId, { autoOpenOnNewGraph: true, autoOpenBaselineKey: previousPlanGraphKey });
    const owner = state.activeSessionUserId || state.userId;
    const request = {
      key: sessionRequestKey(state.sessionId, owner), sessionId: state.sessionId, owner,
      backendUserId: activeSessionBackendUserId(), controller: new AbortController(), lastSequence: 0, runId: null,
    };
    state.activeRequests.set(request.key, request);
    updateSendButtonState();

    const timeline = [];
    const shownPlotPaths = new Set();
    let timelineContainer = null;
    let accumulatedText = "";
    let lineBuffer = "";
    let summaryTriggered = false;
    const renderPendingTimeline = () => {
      if (timeline.length && !timelineContainer) timelineContainer = addAgentTimelineMessage(timeline, shownPlotPaths, undefined, liveTurn);
      else if (timelineContainer) renderTimeline(timelineContainer, timeline, shownPlotPaths);
    };
    const handleAdkData = (data) => {
      if (data === "[DONE]") return;
      try {
        for (const part of JSON.parse(data)?.content?.parts || []) {
          if (part.thought) upsertTimelineThought(timeline, part.text || "");
          else if (part.functionCall) upsertTimelineEvent(timeline, { type: "function_call", id: part.functionCall.id, name: part.functionCall.name || "Unknown", args: part.functionCall.args || {} });
          else if (part.functionResponse) {
            const response = part.functionResponse;
            upsertTimelineEvent(timeline, { type: "function_response", id: response.id, name: response.name || "Unknown", response: response.response || {} });
            if (shouldRefreshPlanGraphForTool(response.name)) planGraph.refresh(request.sessionId);
          } else if (part.text) {
            accumulatedText = mergeReplayedText(accumulatedText, part.text);
            upsertTimelineText(timeline, compactRepeatedPrefixSnapshots(accumulatedText));
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
      // ADK may close the managed SSE stream before its session database has
      // received the final events. Do not let such an incomplete snapshot
      // erase the optimistic user message and already-streamed agent reply.
      const restored = await sessionRuntime.loadSession(request.sessionId, request.owner, { render: false });
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
            if (event.status === "failed") throw new Error(event.error || "Agent run failed");
          }
        }
      }
      if (lineBuffer.trim().startsWith("data: ")) handleAdkData(lineBuffer.trim().slice(6));
    } catch (error) {
      addMessage("agent", error?.name === "AbortError" ? "Stopping execution…" : `Backend error: ${error}`, undefined, liveTurn);
    } finally {
      releaseSessionRequest(request);
      await agentGraph._poll(request.sessionId);
      agentGraph.stopPolling();
      await planGraph._poll(request.sessionId);
      planGraph.stopPolling();
      await refreshSessionFiles(request.sessionId, request.owner);
      stepExecutionFeed.finishLiveTurn();
      await reloadSessionSnapshot();
    }
  }

  return { send, stop };
}
