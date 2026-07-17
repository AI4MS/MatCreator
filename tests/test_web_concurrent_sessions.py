"""Source-contract tests for concurrent session requests in the Vite frontend."""

from __future__ import annotations

from pathlib import Path


MAIN_JS = Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "main.js"
INDEX_HTML = Path(__file__).parents[1] / "web" / "vite-frontend" / "index.html"


def _main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def test_frontend_tracks_requests_per_session() -> None:
    content = _main_js()

    assert "activeRequests: new Map()" in content
    assert "state.activeRequests.set(request.key, request);" in content
    assert "state.activeRequests.get(sessionRequestKey())" in content
    assert "if (activeSessionRequest()) return;" in content


def test_frontend_has_no_browser_global_send_lock() -> None:
    content = _main_js()

    assert "state.isSending" not in content
    assert "state.sendController" not in content
    assert "setSendingState" not in content


def test_existing_session_conflict_does_not_block_run_submission() -> None:
    content = _main_js()
    create_session = content[
        content.index("async function createSession("):
        content.index("function renderKnowledgeReviewStatus(")
    ]

    assert "resp.status === 409 ? await fetch(url) : null" in create_session
    assert "if (!existingResp?.ok)" in create_session
    assert "if (resp.status !== 409) await startKnowledgeReview(sessionId);" in create_session


def test_sse_request_uses_captured_session_context() -> None:
    content = _main_js()

    assert "session_id: request.sessionId" in content
    assert "user_id: request.backendUserId" in content
    assert 'fetch("/api/runs"' in content
    assert "/events`" in content
    assert "signal: request.controller.signal" in content
    assert "releaseSessionRequest(request);" in content
    assert "await loadSession(request.sessionId, request.owner);" in content


def test_completed_request_releases_composer_before_refreshes() -> None:
    content = _main_js()
    send_message = content[content.index("async function sendMessage(message)"):]
    finally_block = send_message[send_message.index("} finally {"):]

    assert finally_block.index("releaseSessionRequest(request);") < finally_block.index(
        "await agentGraph._poll(request.sessionId);"
    )


def test_running_session_switch_discovers_and_reconnects_managed_run() -> None:
    content = _main_js()

    assert "discoverManagedRun(sessionId, owner)" in content
    assert "startManagedRunReconnect(activeRun, sessionId, owner)" in content
    assert 'fetch(`/api/runs/active?${query}`)' in content
    assert "after=${request.lastSequence}" in content


def test_stop_request_identifies_the_active_session_owner() -> None:
    content = _main_js()
    stop_message = content[
        content.index("function stopCurrentMessage()"):content.index("function pollCancellationConfirmed(")
    ]

    assert "new URLSearchParams({ user_id: request.owner || state.userId })" in stop_message
    assert "cancel?${query}" in stop_message


def test_remote_job_polling_is_scoped_to_the_active_session() -> None:
    content = _main_js()

    assert "startRemoteJobsPolling(sessionId, owner)" in content
    assert "remoteJobsUrl(sessionId, owner)" in content
    assert "sessionId !== state.sessionId || owner !== state.activeSessionUserId" in content
    assert "remote-jobs/${encodeURIComponent(job.job_id)}/${action}" in content


def test_remote_jobs_are_collapsed_and_keep_lifecycle_status_visible() -> None:
    content = _main_js()
    index = INDEX_HTML.read_text(encoding="utf-8")
    styles = (Path(__file__).parents[1] / "web" / "vite-frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'id="remote-jobs-toggle"' in index
    assert 'aria-expanded="false"' in index
    assert 'id="remote-job-list"' in index and 'remote-job-list hidden' in index
    assert "remoteJobsExpanded: false" in content
    assert "remoteJobsPane?.classList.toggle(\"is-expanded\", state.remoteJobsExpanded);" in content
    assert "function remoteJobLifecycle(status)" in content
    assert 'succeeded: "Completed"' in content
    assert 'collected: "Completed"' in content
    assert "Sandbox: ${providerStatus}" in content
    assert ".remote-jobs-pane:not(.is-expanded) .remote-jobs-toggle" in styles
    assert "font-size: 0;" not in styles


def test_remote_job_controls_do_not_cancel_the_linked_step_executor() -> None:
    content = (Path(__file__).parents[1] / "web" / "main.py").read_text(encoding="utf-8")
    controls = content[content.index("async def pause_session_remote_job("):content.index("@app.post(\"/api/sessions/{session_id}/remote-jobs/{job_id}/refresh\")")]

    assert "record_user_control" in controls
    assert "request_step_cancellation" not in controls


def test_session_switch_parallelizes_independent_requests() -> None:
    content = _main_js()
    switch_session = content[
        content.index("async function switchSession("):
        content.index("async function discoverManagedRun(")
    ]
    load_session = content[
        content.index("async function loadSession("):
        content.index("// ---------------------------------------------------------------------------\n// Streaming deduplication helpers")
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
        content.index("async function discoverManagedRun(")
    ]

    assert "sessionViewCache: new Map()" in content
    assert "const cachedView = state.sessionViewCache.get(viewKey);" in switch_session
    assert switch_session.index("renderSessionSnapshot(") < switch_session.index(
        "await Promise.all(["
    )
    assert "if (state.sessionViewCache.size > 10)" in content


def test_stale_session_loads_cannot_replace_active_view() -> None:
    content = _main_js()

    assert "const viewKey = sessionRequestKey(sessionId, owner);" in content
    assert "const requestAtStart = activeSessionRequest();" in content
    assert "if (!viewIsCurrent()) return;" in content


def test_new_session_ids_are_not_limited_to_one_second_resolution() -> None:
    content = _main_js()

    assert "globalThis.crypto?.randomUUID?.()" in content
    assert "return `session-${Date.now()}-${randomPart}`;" in content
    assert "Math.floor(Date.now() / 1000)" not in content


def test_session_list_supports_status_indicators_and_filtering() -> None:
    content = _main_js()
    index = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="session-status-filter"' in index
    assert '<option value="running">Running</option>' in index
    assert '<option value="idle">Idle</option>' in index
    assert "sessionDisplayStatus(session, owner)" in content
    assert "session-status-indicator status-${status}" in content
    assert "state.sessionStatusFilter" in content