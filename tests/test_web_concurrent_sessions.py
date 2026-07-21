"""Source-contract tests for concurrent session requests in the Vite frontend."""

from __future__ import annotations

from pathlib import Path


MAIN_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "main.js"
MESSAGE_STREAM_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "chat" / "messageStream.js"
RUNTIME_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "session" / "runtime.js"
SESSION_LIST_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "features" / "session" / "sessionList.js"
SESSIONS_CSS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "styles" / "sessions.css"
INDEX_HTML = Path(__file__).parents[1] / "web" / "vite-frontend" / "index.html"


def _main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def _message_stream_js() -> str:
    return MESSAGE_STREAM_JS.read_text(encoding="utf-8")


def _runtime_js() -> str:
    return RUNTIME_JS.read_text(encoding="utf-8")


def test_plan_approval_uses_live_validation_and_consumes_stale_prompt() -> None:
    content = _message_stream_js()

    assert "sessionRuntime.suppressPlanApproval(state.sessionId, backendMessage);" in content
    assert 'querySelectorAll(".plan-approval-message")' in content
    assert "let validatedPlanThisTurn = false;" in content
    assert 'response.name === "validate_graph"' in content
    assert "validatedPlanThisTurn && !executionApprovedThisTurn" in content
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
        "await agentGraph._poll(request.sessionId);"
    )


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
    assert "pollCancellationConfirmed(request.sessionId, request.owner)" in stop_message


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
    content = _main_js()

    assert "startRemoteJobsPolling(sessionId, owner)" in content
    assert "remoteJobsUrl(sessionId, owner)" in content
    assert "sessionId !== state.sessionId || owner !== state.activeSessionUserId" in content
    assert "remote-jobs/${encodeURIComponent(job.job_id)}/${action}" in content


def test_remote_jobs_are_collapsed_and_keep_lifecycle_status_visible() -> None:
    content = _main_js()
    index = INDEX_HTML.read_text(encoding="utf-8")
    styles = SESSIONS_CSS.read_text(encoding="utf-8")

    assert 'id="remote-jobs-toggle"' in index
    assert 'aria-expanded="false"' in index
    assert 'id="remote-job-list"' in index and 'remote-job-list hidden' in index
    assert "remoteJobsExpanded: false" in content
    assert "remoteJobsPane?.classList.toggle(\"is-expanded\", state.remoteJobsExpanded);" in content
    assert "function remoteJobLifecycle(status)" in content
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
        main.index("function remoteJobsUrl(")
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
        content.index("function remoteJobsUrl(")
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
    switch_session = content[content.index("async function switchSession("):content.index("function remoteJobsUrl(")]
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
    content = _main_js()
    index = INDEX_HTML.read_text(encoding="utf-8")
    styles = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "styles" / "evaluation.css").read_text(encoding="utf-8")

    question_sets_start = index.index('<details class="panel-block evaluation-disclosure evaluation-question-sets"')
    generated_questions_start = index.index('<details class="panel-block evaluation-disclosure evaluation-generated-questions"')

    assert '<section class="evaluation-runs-pane" aria-label="Evaluation runs">' in index
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
