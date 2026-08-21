"""Source-contract tests for concurrent session requests in the Vite frontend."""

from __future__ import annotations

from pathlib import Path


MAIN_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "main.js"
MESSAGE_STREAM_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "chat" / "messageStream.js"
RUNTIME_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "session" / "runtime.js"
SESSION_LIST_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "session" / "sessionList.js"
EVALUATION_CONTROLLER_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "evaluation" / "EvaluationController.js"
REMOTE_JOBS_CONTROLLER_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "remoteJobs" / "RemoteJobsController.js"
SESSIONS_CSS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "styles" / "sessions.css"
INDEX_HTML = Path(__file__).parents[1] / "web" / "vite-frontend" / "index.html"


def _main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def _message_stream_js() -> str:
    return MESSAGE_STREAM_JS.read_text(encoding="utf-8")


def _runtime_js() -> str:
    return RUNTIME_JS.read_text(encoding="utf-8")


def _evaluation_controller_js() -> str:
    return EVALUATION_CONTROLLER_JS.read_text(encoding="utf-8")


def _remote_jobs_controller_js() -> str:
    return REMOTE_JOBS_CONTROLLER_JS.read_text(encoding="utf-8")


def test_plan_approval_uses_live_validation_and_consumes_stale_prompt() -> None:
    content = _message_stream_js()

    assert "sessionRuntime.suppressPlanApproval(state.sessionId, backendMessage);" in content
    assert 'querySelectorAll(".plan-approval-message")' in content
    assert "let validatedPlanThisTurn = false;" in content
    assert 'response.name === "validate_graph"' in content
    assert '!validatedPlanThisTurn || executionApprovedThisTurn' in content
    assert "sessionRuntime.restorePlanApproval(request.sessionId);" in content
    assert "addPlanApprovalActions(latestTimeline);" in content

    runtime = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "session" / "runtime.js").read_text(encoding="utf-8")
    assert "const suppressedPlanApprovalTurns = new Map();" in runtime
    assert "latestUserText === suppressedTurn.userText" in runtime
    assert "function latestTurnPendingPlan(events)" in runtime
    assert 'response?.name === "confirm_plan_and_start_execution"' in runtime
    assert "pendingPlan = null;" in runtime


def test_frontend_tracks_requests_per_session() -> None:
    main = _main_js()
    streams = _message_stream_js()
    runtime = _runtime_js()

    assert "activeRequests: new Map()" in main
    assert "state.activeRequests.set(request.key, request);" in streams
    assert "state.activeRequests.set(key, request);" in runtime
    assert "state.activeRequests.get(sessionRequestKey())" in main
    assert "if (activeSessionRequest()) return;" in main


def test_frontend_has_no_browser_global_send_lock() -> None:
    content = _main_js()

    assert "state.isSending" not in content
    assert "state.sendController" not in content
    assert "setSendingState" not in content


def test_session_creation_does_not_start_knowledge_review() -> None:
    content = _main_js()
    create_session = content[
        content.index("async function createSession("):
        content.index("function renderKnowledgeReviewStatus(")
    ]

    assert "resp.status === 409 ? await fetch(url) : null" in create_session
    assert "if (!existingResp?.ok)" in create_session
    assert "startKnowledgeReview" not in create_session


def test_sse_request_uses_captured_session_context() -> None:
    content = _message_stream_js()
    main = _main_js()

    assert "session_id: request.sessionId" in content
    assert "user_id: request.backendUserId" in content
    assert 'fetch("/api/runs"' in content
    assert "`/api/runs/${request.runId}/events`" in main
    assert "signal: request.controller.signal" in content
    assert "releaseSessionRequest(request);" in content
    assert "sessionRuntime.loadSession(request.sessionId, request.owner" in content


def test_completed_request_releases_composer_before_refreshes() -> None:
    content = _message_stream_js()
    send_message = content[content.index("async function send(message)"):]
    finally_block = send_message[send_message.index("} finally {"):]

    assert finally_block.index("releaseSessionRequest(request);") < finally_block.index(
        "await Promise.allSettled(["
    )
    assert 'terminalStatus = event.status;' in send_message
    assert 'updateAgentRunningStatus("finalizing_plan");' in send_message
    assert 'finalizing_plan: ["Plan validated — preparing it for review…", "thinking"]' in _main_js()
    assert send_message.index("releaseSessionRequest(request);") < send_message.index("revealPlanApproval();")
    assert finally_block.index("revealPlanApproval();") < finally_block.index("await Promise.allSettled([")


def test_roadmap_auto_opens_at_completed_plan_handoff() -> None:
    main = _main_js()
    streams = _message_stream_js()
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "ExecutionPlanView.js").read_text(encoding="utf-8")

    assert "showPlanGraph," in main
    assert "let roadmapOpenedForPlan = false;" in streams
    assert "showPlanGraph();" in streams
    assert streams.index('terminalStatus = event.status;') < streams.index("revealPlanApproval();", streams.index('terminalStatus = event.status;'))
    assert "onNewGraph: () => showPlanGraph()" not in main
    assert "autoOpenOnNewGraph" not in streams
    assert "_autoOpenOnNewGraph" not in graph


def test_roadmap_fits_after_the_popup_has_a_stable_layout() -> None:
    main = _main_js()
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "ExecutionPlanView.js").read_text(encoding="utf-8")

    show_roadmap = main[main.index("function showPlanGraph()") : main.index("function hidePlanGraph()")]
    assert "requestAnimationFrame(() => requestAnimationFrame(() =>" in show_roadmap
    assert "planGraph.fitToView({ animate: false });" in show_roadmap
    assert "fitToView({ animate = true } = {})" in graph


def test_running_session_switch_discovers_and_reconnects_managed_run() -> None:
    main = _main_js()
    runtime = _runtime_js()

    assert "discoverManagedRun(sessionId, owner)" in main
    assert "startManagedRunReconnect(activeRun, sessionId, owner)" in main
    assert 'fetch(`/api/runs/active?${query}`)' in runtime
    assert "after=${request.lastSequence}" in main


def test_managed_run_reconnect_retries_and_refreshes_persisted_state() -> None:
    runtime = _runtime_js()

    assert "const MANAGED_RUN_RETRY_INITIAL_DELAY_MS = 500;" in runtime
    assert "function scheduleManagedRunRefresh(request" in runtime
    assert "await loadSession(request.sessionId, request.owner);" in runtime
    assert "async function managedRunStillActive(request)" in runtime
    assert 'fetch(`/api/runs/${encodeURIComponent(request.runId)}`)' in runtime
    assert "async function waitForManagedRunRetry(request)" in runtime
    assert "while (shouldRetry && isCurrentManagedRunRequest(request)" in runtime
    assert "request.lastSequence = event.sequence || request.lastSequence;" in runtime
    assert "scheduleManagedRunRefresh(request);" in runtime
    assert "if (event.type === \"terminal\")" in runtime
    assert "if (!await managedRunStillActive(request) || !await waitForManagedRunRetry(request))" in runtime
    assert "if (isCurrentManagedRunRequest(request)) {\n        releaseSessionRequest(request);" in runtime


def test_stop_request_identifies_the_active_session_owner() -> None:
    content = _message_stream_js()
    stop_message = content[
        content.index("function stop()") : content.index("async function send(message)")
    ]

    assert "new URLSearchParams({ user_id: request.owner || state.userId })" in stop_message
    assert "cancel?${query}" in stop_message
    assert "pollCancellationConfirmed(request)" in stop_message


def test_stop_feedback_uses_managed_run_status_and_survives_session_refresh() -> None:
    content = _message_stream_js()

    assert 'content = request.stopStatus === "stopped" ? "✓ Execution stopped." : "Still executing…"' in content
    assert 'fetch(`/api/runs/${encodeURIComponent(request.runId)}`)' in content
    assert '["completed", "failed", "cancelled"].includes(run.status)' in content
    assert "request.stopStatus = \"stopped\";\n        releaseSessionRequest(request);" in content
    assert "reloadSessionSnapshot()," in content
    assert "await Promise.allSettled([" in content
    assert "renderStopStatus(request);" in content
    assert "cancellation_requested" not in content

    finally_block = content[content.index("} finally {"):]
    assert 'if (request.stopStatus !== "waiting") releaseSessionRequest(request);' in finally_block


def test_stop_and_plan_refreshes_preserve_open_node_dialogs() -> None:
    streams = _message_stream_js()
    runtime = _runtime_js()
    disclosures = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "ui" / "disclosureState.js").read_text(encoding="utf-8")
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "AgentGraphView.js").read_text(encoding="utf-8")

    assert "sessionRuntime.markSessionRendered(state.sessionId, owner);" in streams
    assert "if (preserveDisclosures) stepExecutionFeed.captureDisclosureState();" in runtime
    assert "openState.set(details.dataset.disclosureKey, details.open);" in disclosures
    assert "captureDisclosureState()" in graph
    assert 'defaultOpen: node.status === "running"' in graph
    assert "details.open = isRunning && (userChoice === undefined ? true : userChoice);" in graph


def test_bottom_attachment_and_node_toggle_use_one_scroll_policy() -> None:
    rendering = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "chat" / "rendering.js").read_text(encoding="utf-8")
    disclosures = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "ui" / "disclosureState.js").read_text(encoding="utf-8")
    main = _main_js()
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "AgentGraphView.js").read_text(encoding="utf-8")

    assert "const BOTTOM_ATTACH_THRESHOLD = 80;" in rendering
    assert "currentScrollTop < lastScrollTop - 0.5" in rendering
    assert "if (preserveUserPosition && userDetached) return;" in rendering
    assert "if (event.deltaY < 0) detachBottomFollow();" in rendering
    assert "else if (event.deltaY > 0 && isChatNearBottom()) enterBottomFollow();" in rendering
    assert "viewportModeVersion !== transaction.viewportModeVersion" in rendering
    assert "position.viewportModeVersion !== viewportModeVersion" in rendering
    assert "|| !userDetached) return;" in rendering
    assert "if (!userScrollActive && isChatNearBottom()) bottomPinned = true;" not in rendering
    assert "if (detachBottom && !followBottom) detachBottomFollow();" in rendering
    assert "if (followBottom) return { followBottom: true, userScrollIntent, viewportModeVersion };" in rendering
    assert "if (snapshot.followBottom)" in rendering
    assert "captureScrollPosition?.(details," in disclosures
    assert 'block.dataset.readingAnchor = `${key}:block:${index}`;' in disclosures
    assert "absolute: true" not in disclosures
    assert "const readingPosition = wasBottomPinned ? null : captureScrollPosition();" in rendering
    assert "restoreScrollPosition(transaction.readingPosition);" in rendering
    assert "absolute = false" not in rendering
    assert "function updatePreservingReadingPosition(update)" in rendering
    assert "updatePreservingReadingPosition(() => {" in main
    assert "this._updatePreservingReadingPosition(() => {" in graph
    assert "const shouldStick = isChatBottomPinned();" not in main
    assert "const shouldStick = this._isChatBottomPinned();" not in graph


def test_all_chat_disclosures_share_the_reading_position_controller() -> None:
    main = _main_js()
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "AgentGraphView.js").read_text(encoding="utf-8")
    runtime = _runtime_js()

    render_timeline = main[main.index("function renderTimeline("):main.index("function addAgentTimelineMessage(")]
    create_activity = main[main.index("function createAgentActivity("):main.index("function renderTimeline(")]
    assert 'activity.className = "agent-activity";' in create_activity
    assert "wireTimelineDetails(activity," in create_activity
    assert "createTimelineReasoning(" in create_activity
    assert "createActivityAction(" in create_activity
    assert "updatePreservingReadingPosition(() => {" in render_timeline

    render_card = graph[graph.index("_createCard(node)"):graph.index("// ---------------------------------------------------------------------------\n// Execution Plan Graph")]
    assert 'this._disclosures.wire(details, `step:${node.id}:card`' in render_card
    assert 'this._wireNested(node.id, "input"' in render_card
    assert 'activity.className = "step-feed-activity-list agent-activity-action-list";' in render_card
    assert "const activityItems = this._activityStream(node);" in render_card
    assert "this._wireNested(node.id, key, this._renderStepConversationEvent(event, {" in render_card
    assert 'collapsed: node.status !== "running"' in render_card
    assert "this._wireNested(node.id, key, this._renderStepToolCall(toolCall))" in render_card

    assert "beginScrollTransaction();" in runtime
    assert "endScrollTransaction();" in runtime
    assert "endScrollTransaction({ revealBottom: awaitingPlanApproval });" not in runtime


def test_image_and_all_timeline_updates_preserve_node_disclosures() -> None:
    main = _main_js()
    rendering = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "chat" / "rendering.js").read_text(encoding="utf-8")
    render_timeline = main[main.index("function renderTimeline("):main.index("function addAgentTimelineMessage(")]
    create_image = main[main.index("function createTimelineImage("):main.index("function isExecutorLauncherTool(")]

    assert "disclosures.capture(chatArea);" in render_timeline
    assert "container.innerHTML = \"\";" in render_timeline
    assert render_timeline.index("disclosures.capture(chatArea);") < render_timeline.index('container.innerHTML = "";')
    assert "pendingRestoreSnapshot.userScrollIntent !== snapshot.userScrollIntent" in rendering
    assert "[data-reading-anchor]" in rendering
    assert 'anchorKeyType: anchorEl?.dataset.readingAnchor ? "reading"' in rendering
    assert "function protectAsyncContentLayout(root)" in rendering
    assert 'img:not([data-layout-protected])' in rendering
    assert "protectAsyncContentLayout(div);" in render_timeline
    assert create_image.count("updatePreservingReadingPosition(() => {") == 2
    assert "prepareAsyncReadingPositionUpdate" not in create_image


def test_plain_text_blocks_keep_history_order_and_stable_identity() -> None:
    timeline = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "chat" / "timeline.js").read_text(encoding="utf-8")
    streams = _message_stream_js()
    runtime = _runtime_js()
    main = _main_js()

    text_upsert = timeline[timeline.index("export function upsertTimelineText"):timeline.index("function titleizeToolName")]
    assert 'const last = timeline[timeline.length - 1];' in text_upsert
    assert 'if (last?.type === "text")' in text_upsert
    assert 'timelineId: nextTimelineItemId(timeline, "text")' in text_upsert
    assert "timeline.splice" not in text_upsert
    assert 'upsertTimelineText(timeline, part.text);' in streams
    assert 'upsertTimelineText(timeline, part.text);' in runtime
    assert 'let accumulatedText = "";' not in streams
    assert 'let accumulatedText = "";' not in runtime
    assert '${item.timelineId || "text:legacy"}:content' in main


def test_agent_graph_stops_animation_and_uses_one_update_transport() -> None:
    graph = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "graphs" / "AgentGraphView.js").read_text(encoding="utf-8")
    start_polling = graph[graph.index("  startPolling(sessionId)"):graph.index("  async _poll(sessionId)")]

    assert "this.stopPolling();" in start_polling
    assert "if (this._eventStream !== eventStream) return;" in start_polling
    assert "eventStream.close();" in start_polling
    assert start_polling.index("eventStream.close();") < start_polling.index("setInterval(")
    assert "this._hasRunningNodes = false;" in start_polling
    assert "cancelAnimationFrame(this._animationFrame);" in start_polling


def test_orbital_indicator_skips_duplicate_state_renders() -> None:
    indicator = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "components" / "OrbitalAgentIndicator.js").read_text(encoding="utf-8")

    assert "let renderedState;" in indicator
    assert "if (destroyed || nextState === renderedState) return;" in indicator
    assert "renderedState = nextState;" in indicator


def test_step_cancellation_identifies_the_active_session_owner() -> None:
    content = _main_js()
    request_step_cancellation = content[
        content.index("async function requestStepCancellation(") : content.index("function shouldRefreshPlanGraphForTool(")
    ]

    assert "user_id: state.activeSessionUserId || state.userId," in request_step_cancellation
    assert "cancel-step/${stepNumber}?${query}" in request_step_cancellation


def test_server_mode_cancellation_uses_the_worker_workspace() -> None:
    content = (Path(__file__).parents[1] / "web" / "main.py").read_text(encoding="utf-8")

    assert "def _cancellation_workspace_root(session_id: str, user_id: str = \"\")" in content
    assert "return _user_workspace_root(owner_id)" in content
    assert "workspace_root=cancellation_root" in content
    assert "workspace_root=_cancellation_workspace_root(session_id, user_id)" in content


def test_remote_job_polling_is_scoped_to_the_active_session() -> None:
    main = _main_js()
    content = _remote_jobs_controller_js()

    assert "remoteJobsController.startPolling(sessionId, owner)" in main
    assert "pollTimer = windowRef.setInterval(() => void load(sessionId, owner), pollIntervalMs);" in content
    assert "sessionId !== state.sessionId || owner !== state.activeSessionUserId" in content
    assert "remote-jobs/${encodeURIComponent(job.job_id)}/${action}" in content


def test_remote_jobs_are_collapsed_and_keep_lifecycle_status_visible() -> None:
    content = _remote_jobs_controller_js()
    index = INDEX_HTML.read_text(encoding="utf-8")
    styles = SESSIONS_CSS.read_text(encoding="utf-8")

    assert 'id="remote-jobs-toggle"' in index
    assert 'aria-expanded="false"' in index
    assert 'id="remote-job-list"' in index and 'remote-job-list hidden' in index
    assert "let expanded = false;" in content
    assert 'pane?.classList.toggle("is-expanded", expanded);' in content
    assert "export function remoteJobLifecycle(status)" in content
    assert 'succeeded: "Completed"' in content
    assert 'collected: "Completed"' in content
    assert '["Provider status", providerStatus]' in content
    assert '["Sandbox", job.external_id || "—"]' in content
    assert ".remote-jobs-pane:not(.is-expanded) .remote-jobs-toggle" in styles
    assert "font-size: 0;" not in styles


def test_remote_job_controls_do_not_cancel_the_linked_step_executor() -> None:
    content = (Path(__file__).parents[1] / "web" / "main.py").read_text(encoding="utf-8")
    controls = content[content.index("async def pause_session_remote_job("):content.index("@app.post(\"/api/sessions/{session_id}/remote-jobs/{job_id}/refresh\")")]

    assert "record_user_control" in controls
    assert "request_step_cancellation" not in controls


def test_session_switch_parallelizes_independent_requests() -> None:
    main = _main_js()
    runtime = _runtime_js()
    switch_session = main[
        main.index("async function switchSession("):
        main.index("function showConfirmDialog(")
    ]
    load_session = runtime[
        runtime.index("async function loadSession("):
        runtime.index("async function discoverManagedRun(")
    ]

    assert "const [activeRun] = await Promise.all([" in switch_session
    assert "discoverManagedRun(sessionId, owner)" in switch_session
    assert "loadSession(sessionId, owner)" in switch_session
    assert "void loadSessions();" in switch_session
    assert "const [sessionData, graphNodes] = await Promise.all([" in load_session
    assert "void refreshSessionFiles(sessionId, owner);" in load_session
    assert "await refreshSessionFiles(sessionId, owner);" not in load_session


def test_session_switch_renders_cached_snapshot_immediately() -> None:
    content = _main_js()
    switch_session = content[
        content.index("async function switchSession("):
        content.index("function showConfirmDialog(")
    ]

    assert "sessionViewCache: new Map()" in content
    assert "const cachedView = state.sessionViewCache.get(viewKey);" in switch_session
    assert switch_session.index("renderSessionSnapshot(") < switch_session.index(
        "await Promise.all(["
    )
    assert "if (state.sessionViewCache.size > 10)" in _runtime_js()


def test_stale_session_loads_cannot_replace_active_view() -> None:
    content = _runtime_js()

    assert "const viewKey = sessionRequestKey(sessionId, owner);" in content
    assert "const requestAtStart = activeSessionRequest();" in content
    assert "if (!isCurrentView()) return;" in content


def test_new_session_ids_are_not_limited_to_one_second_resolution() -> None:
    content = _main_js()

    assert "globalThis.crypto?.randomUUID?.()" in content
    assert "return `session-${Date.now()}-${randomPart}`;" in content
    assert "Math.floor(Date.now() / 1000)" not in content


def test_session_list_supports_status_indicators_and_filtering() -> None:
    content = _main_js()
    session_list = SESSION_LIST_JS.read_text(encoding="utf-8")
    index = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="session-status-filter"' in index
    assert 'data-value="running">Running</li>' in index
    assert 'data-value="idle">Idle</li>' in index
    assert "sessionDisplayStatus(session, owner)" in content
    assert "session-status-indicator status-${status}" in session_list
    assert "state.sessionStatusFilter" in session_list


def test_active_session_transitions_recompute_composer_state() -> None:
    content = _main_js()
    switch_session = content[content.index("async function switchSession("):content.index("function showConfirmDialog(")]
    new_session = content[content.index("async function _doNewSession("):]
    apply_session = content[content.index("function _applySession("):content.index("async function applyLogin(")]
    delete_session = content[content.index("async function deleteSession("):content.index("async function downloadSessionLog(")]

    assert switch_session.index("updateSendButtonState();") < switch_session.index("await Promise.all([")
    assert "updateSendButtonState();" in new_session
    assert "updateSendButtonState();" in apply_session
    assert "updateSendButtonState();" in delete_session


def test_startup_restores_only_an_accessible_session_owner_tuple() -> None:
    main = _main_js()
    session_list = SESSION_LIST_JS.read_text(encoding="utf-8")

    assert 'const SESSION_OWNER_KEY = "mat_sessionOwnerId";' in main
    assert "storeSessionSelection(sessionId, owner);" in main
    assert "const sessions = await loadSessions();" in main
    assert "validatedStoredSession(sessions, storedSessionId, storedSessionOwner)" in main
    assert "state.deploymentMode === \"server\" && state.isAdmin" in main
    assert "storedOwner !== state.userId" in main
    assert "await switchSession(storedSession.sessionId, storedSession.owner);" in main
    assert "clearStoredSessionSelection();" in main
    assert "return Array.isArray(sessions) ? sessions : [];" in session_list


def test_evaluation_sidebar_prioritizes_runs_and_collapses_configuration() -> None:
    content = _evaluation_controller_js()
    index = INDEX_HTML.read_text(encoding="utf-8")
    styles = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "styles" / "evaluation.css").read_text(encoding="utf-8")

    question_sets_start = index.index('<details class="panel-block evaluation-disclosure evaluation-question-sets"')
    generated_questions_start = index.index('<details class="panel-block evaluation-disclosure evaluation-generated-questions"')

    assert '<details class="panel-block evaluation-disclosure evaluation-runs-pane" aria-label="Evaluation runs">' in index
    assert 'class="evaluation-runs-list-body"' in index
    assert 'class="evaluation-start-area"' in index
    assert 'id="evaluation-campaign-list"' in index
    assert 'id="evaluation-create-start"' in index
    assert " open" not in index[question_sets_start:index.index(">", question_sets_start)]
    assert " open" not in index[generated_questions_start:index.index(">", generated_questions_start)]
    assert '<summary class="block-header">Question sets</summary>' in index[question_sets_start:generated_questions_start]
    assert '<summary class="block-header">Generated questions</summary>' in index[generated_questions_start:]
    assert ".evaluation-runs-list-body" in styles
    assert "overflow-y: auto;" in styles
    assert ".evaluation-start-area" in styles
    assert "margin-top: auto;" in styles
    assert 'button.classList.toggle("is-active", isActive);' in content
