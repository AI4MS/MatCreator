export function createSessionId({
  now = Date.now(),
  randomId = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2),
} = {}) {
  return `session-${now}-${randomId}`;
}

export function createSessionRequestKey(sessionId, owner) {
  return `${owner || "user"}:${sessionId || ""}`;
}

export function managedRunEventsUrl(request) {
  const query = new URLSearchParams({ after: String(request.lastSequence ?? 0) });
  return `/api/runs/${encodeURIComponent(request.runId)}/events?${query}`;
}

export function shouldRefreshPlanGraphForTool(toolName) {
  return toolName === "validate_graph" || toolName === "validate_plan";
}

export function workspaceFileUrl(path, sessionId = "") {
  const query = new URLSearchParams({ path: String(path || "") });
  if (sessionId) query.set("session_id", sessionId);
  return `/api/workspace/files?${query}`;
}
