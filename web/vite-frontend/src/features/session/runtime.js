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
  renderSessionBanner,
  renderSessionFilesTree,
  refreshSessionFiles,
  generateSessionSummary,
  workdirDisplay,
}) {
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

  function eventToTimelineParts(event, responsesById, pairedResponseIds = new Set()) {
    const timeline = [];
    let accumulatedText = "";
    for (const part of event.content?.parts || []) {
      if (part.thought) {
        timeline.push({ type: "thought", text: part.text || "" });
      } else if (part.functionCall || part.function_call) {
        const call = part.functionCall || part.function_call;
        const matchedResponse = responsesById[call.id];
        timeline.push({ type: "function_call", id: call.id, name: call.name || "Unknown", args: call.args || {} });
        if (matchedResponse) {
          if (matchedResponse.id) pairedResponseIds.add(matchedResponse.id);
          timeline.push({ type: "function_response", id: matchedResponse.id, name: matchedResponse.name || "Unknown", response: matchedResponse.response || {} });
        }
      } else if (getFunctionResponse(part)) {
        const response = getFunctionResponse(part);
        if (response.id && pairedResponseIds.has(response.id)) continue;
        if (!timeline.some((item) => item.type === "function_response" && item.id === response.id)) {
          timeline.push({ type: "function_response", id: response.id, name: response.name || "Unknown", response: response.response || {} });
        }
      } else if (part.text) {
        accumulatedText += part.text;
        const previous = timeline.at(-1);
        if (previous?.type === "text") previous.text = accumulatedText;
        else timeline.push({ type: "text", text: accumulatedText });
      }
    }
    return timeline;
  }

  function attachStepNodes(timeline, pendingStepNodes) {
    for (const item of timeline) {
      if (item.type !== "function_call" || !isExecutorLauncherTool(item.name)) continue;
      const nextStep = pendingStepNodes.shift();
      if (nextStep) item.stepNodes = [nextStep];
    }
    return timeline;
  }

  function renderSessionTimeline(events, stepNodes) {
    chatArea.innerHTML = "";
    stepExecutionFeed.reset();
    stepExecutionFeed.setHierarchy(stepNodes || []);
    const sortedEvents = (events || []).map((event, index) => ({ event, timestamp: eventTimestamp(event, index), index }))
      .sort((left, right) => left.timestamp - right.timestamp || left.index - right.index).map(({ event }) => event);
    const pendingStepNodes = (stepNodes || []).filter((node) => stepExecutionFeed.isRootStep(node)).slice()
      .sort((left, right) => stepNodeTimestamp(left) - stepNodeTimestamp(right));
    const responsesById = collectFunctionResponsesById(events || []);
    const pairedResponseIds = new Set();
    let shownPlotPaths = new Set();
    let messageIndex = 0;

    for (const event of sortedEvents) {
      if (event.author === "user") {
        const text = displayMessageFromStoredUserText((event.content?.parts || []).map((part) => part.text || "").join(""));
        if (text) addMessage("user", text, messageIndex++);
        shownPlotPaths = new Set();
        continue;
      }
      const timeline = attachStepNodes(eventToTimelineParts(event, responsesById, pairedResponseIds), pendingStepNodes);
      if (timeline.length) addAgentTimelineMessage(timeline, shownPlotPaths, messageIndex++);
    }
    pendingStepNodes.forEach((node) => stepExecutionFeed.appendStatic(node));
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
      if (render) {
        renderSessionBanner(summary);
        renderSessionTimeline(events, graphNodes);
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

  function startManagedRunReconnect(activeRun, sessionId, owner = state.activeSessionUserId || state.userId) {
    if (!activeRun?.run_id) return;
    const key = sessionRequestKey(sessionId, owner);
    if (state.activeRequests.get(key)) return;
    const request = { key, sessionId, owner, backendUserId: owner, controller: new AbortController(), lastSequence: activeRun.latest_sequence || 0, runId: activeRun.run_id };
    state.activeRequests.set(key, request);
    updateSendButtonState();
    void streamManagedRunEvents(request);
  }

  async function streamManagedRunEvents(request) {
    try {
      const response = await fetch(managedRunEventsUrl(request), { headers: { Accept: "text/event-stream" }, signal: request.controller.signal });
      if (!response.ok) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
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
            if (event.type === "event") request.lastSequence = event.sequence || request.lastSequence;
            if (event.type === "snapshot_required") await loadSession(request.sessionId, request.owner);
          } catch (_) { /* Ignore malformed SSE events. */ }
        }
      }
    } catch (_) {
      // Reconnect is best-effort; a normal send path surfaces hard errors.
    } finally {
      releaseSessionRequest(request);
      await loadSession(request.sessionId, request.owner);
    }
  }

  return {
    discoverManagedRun,
    loadSession,
    renderSessionTimeline,
    startManagedRunReconnect,
    updateSessionWorkdirDisplay,
  };
}
