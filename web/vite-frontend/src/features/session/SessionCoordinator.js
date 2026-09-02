export function createSessionCoordinator({
  state,
  appName,
  sessionIdElement,
  createSessionId,
  sessionRequestKey,
  activeSessionRequest,
  requestHasActiveRun,
  activeSessionBackendUserId,
  updateSendButtonState,
  storeSessionSelection,
  clearSessionSelection,
  loadSessions,
  getSessionRuntime,
  stepExecutionFeed,
  renderSessionFilesTree,
  clearCurrentUploads,
  remoteJobsController,
  agentGraph,
  planGraph,
  hidePlanGraph,
  clearDisclosures,
  renderSessionBanner,
  showConfirmDialog,
  fetchImpl = globalThis.fetch,
  documentRef = globalThis.document,
  urlApi = URL,
}) {
  let switchRevision = 0;
  const createOperations = new Map();
  let filesRequestController = null;
  let modeRequestController = null;

  function isCurrentSession(sessionId, owner) {
    return sessionRequestKey(sessionId, owner) === sessionRequestKey();
  }

  function displayStatus(session, owner) {
    if (requestHasActiveRun(state.activeRequests.get(sessionRequestKey(session.id, owner)))) {
      return "running";
    }
    if (session.id === state.sessionId && owner === state.activeSessionUserId) {
      const statuses = state.remoteJobs.map((job) => job.status);
      if (statuses.includes("running") || statuses.includes("queued")) return "running";
    }
    const status = String(session.status || session.phase || "").toLowerCase();
    return ["running", "idle"].includes(status) ? status : "idle";
  }

  function renderSnapshot(snapshot) {
    if (!snapshot) return;
    const runtime = getSessionRuntime();
    if (runtime.restoreSessionSnapshot(snapshot)) {
      renderSessionFilesTree(snapshot.files || []);
      return;
    }
    renderSessionBanner(snapshot.summary || "");
    runtime.renderSessionTimeline(
      snapshot.events || [],
      snapshot.graphNodes || [],
      Boolean(snapshot.awaitingPlanApproval),
    );
    renderSessionFilesTree(snapshot.files || []);
    runtime.updateSessionWorkdirDisplay(snapshot.sessionData || {});
  }

  function resetViews({ resetSummary = false } = {}) {
    getSessionRuntime().resetTranscript();
    stepExecutionFeed.reset();
    if (resetSummary) {
      state.sessionSummaries = {};
      state.summaryGeneratedFor = new Set();
      clearDisclosures();
      renderSessionBanner("");
    }
    renderSessionFilesTree([]);
    clearCurrentUploads();
    remoteJobsController.reset();
    agentGraph.reset();
    planGraph.reset();
    hidePlanGraph();
  }

  async function switchSession(
    sessionId,
    owner = state.userId,
    { knownRunning = false, knownRun = null } = {},
  ) {
    const viewKey = sessionRequestKey(sessionId, owner);
    if (state.sessionReady && viewKey === sessionRequestKey()) return false;
    const revision = ++switchRevision;
    state.sessionId = sessionId;
    state.activeSessionUserId = owner;
    state.sessionReady = true;
    updateSendButtonState();
    storeSessionSelection(sessionId, owner);
    if (sessionIdElement) sessionIdElement.textContent = sessionId;
    const cachedView = state.sessionViewCache.get(viewKey);
    if (cachedView) renderSnapshot(cachedView);
    else renderSessionFilesTree([]);
    clearCurrentUploads();
    remoteJobsController.reset();
    agentGraph.reset();
    planGraph.reset();
    hidePlanGraph();

    const runtime = getSessionRuntime();
    if (knownRun) runtime.startManagedRunReconnect(knownRun, sessionId, owner);
    else if (knownRunning) runtime.beginManagedRunDiscovery(sessionId, owner);
    remoteJobsController.startPolling(sessionId, owner);
    const reconnect = knownRun ? Promise.resolve() : runtime.discoverManagedRun(sessionId, owner)
      .then((activeRun) => {
        if (revision !== switchRevision || !isCurrentSession(sessionId, owner)) return;
        if (activeRun) runtime.startManagedRunReconnect(activeRun, sessionId, owner);
        else runtime.discardManagedRunDiscovery(sessionId, owner);
      });
    await Promise.all([
      reconnect,
      runtime.loadSession(sessionId, owner),
      remoteJobsController.load(sessionId, owner),
    ]);
    if (revision !== switchRevision || !isCurrentSession(sessionId, owner)) return false;
    void loadSessions();
    agentGraph.startPolling(sessionId);
    planGraph.startPolling(sessionId);
    return true;
  }

  async function refreshFiles(
    sessionId = state.sessionId,
    owner = state.activeSessionUserId || state.userId,
  ) {
    if (!sessionId || !state.sessionReady) return [];
    filesRequestController?.abort();
    const controller = new AbortController();
    filesRequestController = controller;
    try {
      const response = await fetchImpl(`/api/sessions/${encodeURIComponent(sessionId)}/files`, {
        signal: controller.signal,
      });
      if (!response.ok) return [];
      const data = await response.json();
      if (!controller.signal.aborted && isCurrentSession(sessionId, owner)) {
        renderSessionFilesTree(data.files || []);
      }
      return data.files || [];
    } catch (error) {
      return [];
    } finally {
      if (filesRequestController === controller) filesRequestController = null;
    }
  }

  async function createSession() {
    const sessionId = state.sessionId;
    const owner = state.userId;
    const creationKey = sessionRequestKey(sessionId, owner);
    const activeCreation = createOperations.get(creationKey);
    if (activeCreation) return activeCreation.promise;
    const controller = new AbortController();
    const operation = { controller, promise: null };
    createOperations.set(creationKey, operation);
    state.activeSessionUserId = owner;
    const backendUserId = activeSessionBackendUserId();
    const url = `/apps/${appName}/users/${encodeURIComponent(backendUserId)}`
      + `/sessions/${encodeURIComponent(sessionId)}`;
    const sessionWorkdir = state.customWorkdir || String(state.defaultWorkdir || "").trim();

    operation.promise = Promise.resolve().then(async () => {
      try {
        const response = await fetchImpl(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...(state.agentMode !== "normal" ? { agent_mode: state.agentMode } : {}),
            ...(state.agentMode === "bench" ? { benchmark_mode: true } : {}),
            ...(sessionWorkdir ? { custom_workdir: sessionWorkdir } : {}),
          }),
          signal: controller.signal,
        });
        const existingResponse = response.status === 409
          ? await fetchImpl(url, { signal: controller.signal })
          : null;
        if (!response.ok && !existingResponse?.ok) {
          if (response.status !== 409) {
            console.error(`Failed to create session: HTTP ${response.status}`, await response.text());
          }
          return false;
        }
        if (state.sessionId !== sessionId || state.userId !== owner) return false;
        state.sessionReady = true;
        storeSessionSelection(sessionId, state.activeSessionUserId);
        await loadSessions();
        return true;
      } catch (error) {
        if (error.name !== "AbortError") console.error("Failed to create session:", error);
        return false;
      } finally {
        if (createOperations.get(creationKey) === operation) {
          createOperations.delete(creationKey);
        }
      }
    });
    return operation.promise;
  }

  async function createNew(customWorkdir = "") {
    switchRevision += 1;
    filesRequestController?.abort();
    state.customWorkdir = customWorkdir;
    state.sessionId = createSessionId();
    state.activeSessionUserId = state.userId;
    state.sessionReady = false;
    updateSendButtonState();
    clearSessionSelection();
    if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
    resetViews({ resetSummary: true });
    return createSession();
  }

  async function deleteSession(sessionId) {
    if (activeSessionRequest()) return false;
    const confirmed = await showConfirmDialog(`Delete session ${sessionId}? This cannot be undone.`);
    if (!confirmed) return false;
    try {
      const response = await fetchImpl(
        `/api/sessions/${encodeURIComponent(sessionId)}`,
        { method: "DELETE" },
      );
      if (!response.ok) return false;
      if (sessionId === state.sessionId) {
        switchRevision += 1;
        state.sessionId = createSessionId();
        state.activeSessionUserId = state.userId;
        state.sessionReady = false;
        updateSendButtonState();
        clearSessionSelection();
        if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
        resetViews();
      }
      await loadSessions();
      return true;
    } catch (_) {
      return false;
    }
  }

  async function downloadSessionLog(sessionId, owner = state.userId) {
    if (!sessionId) return false;
    const query = new URLSearchParams();
    if (owner || state.userId) query.set("user_id", owner || state.userId);
    try {
      const response = await fetchImpl(
        `/api/sessions/${encodeURIComponent(sessionId)}/session-log`
          + `${query.size ? `?${query}` : ""}`,
      );
      if (!response.ok) {
        const message = await response.text().catch(() => "");
        throw new Error(message || `HTTP ${response.status}`);
      }
      const objectUrl = urlApi.createObjectURL(await response.blob());
      let link;
      try {
        link = documentRef.createElement("a");
        link.href = objectUrl;
        link.download = `matcreator-session-log-${sessionId}.json`;
        documentRef.body.appendChild(link);
        link.click();
      } finally {
        link?.remove();
        urlApi.revokeObjectURL(objectUrl);
      }
      return true;
    } catch (error) {
      console.warn("Failed to download session log", error);
      return false;
    }
  }

  async function patchAgentMode(mode) {
    if (!state.sessionReady || !state.sessionId) return false;
    modeRequestController?.abort();
    const controller = new AbortController();
    modeRequestController = controller;
    const sessionId = state.sessionId;
    const url = `/apps/${appName}/users/${encodeURIComponent(activeSessionBackendUserId())}`
      + `/sessions/${encodeURIComponent(sessionId)}`;
    try {
      const response = await fetchImpl(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          state_delta: { agent_mode: mode, benchmark_mode: mode === "bench" },
        }),
        signal: controller.signal,
      });
      if (!response.ok) console.error(`Failed to patch agent mode: HTTP ${response.status}`);
      return response.ok;
    } catch (error) {
      if (error.name !== "AbortError") console.error("Failed to patch agent mode:", error);
      return false;
    } finally {
      if (modeRequestController === controller) modeRequestController = null;
    }
  }

  function destroy() {
    switchRevision += 1;
    filesRequestController?.abort();
    modeRequestController?.abort();
    createOperations.forEach((operation) => operation.controller.abort());
    createOperations.clear();
    filesRequestController = null;
    modeRequestController = null;
  }

  return {
    displayStatus,
    switchSession,
    refreshFiles,
    createSession,
    createNew,
    deleteSession,
    downloadSessionLog,
    patchAgentMode,
    renderSnapshot,
    destroy,
  };
}
