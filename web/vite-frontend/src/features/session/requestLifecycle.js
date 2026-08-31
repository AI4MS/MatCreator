const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);

/** Attach the client-side lifecycle that outlives a managed backend run. */
export function initializeRequestLifecycle(request) {
  let resolveCleanup;
  const cleanupComplete = new Promise((resolve) => { resolveCleanup = resolve; });
  request.cleanupDone = false;
  return Object.assign(request, {
    backendStatus: "starting",
    cleanupComplete,
    finishCleanup: () => {
      request.cleanupDone = true;
      resolveCleanup();
    },
  });
}

export function requestHasActiveRun(request) {
  return Boolean(request && !TERMINAL_RUN_STATUSES.has(request.backendStatus));
}

export function requestRetainsVisibleTurn(request) {
  if (!request || request.cleanupDone) return false;
  return Boolean(request.liveTurnClaimed || request.messageView || request.userMessage || request.message);
}

/** Whether request-owned UI should still present an in-progress agent turn. */
export function requestPresentsLiveTurn(request) {
  return requestRetainsVisibleTurn(request) && request.message?.lifecycle !== "completed";
}

/**
 * Resolve the request that owns a conversation independently of transient
 * owner-key normalization. Session IDs are unique in the client; the fallback
 * is accepted only when exactly one retained request matches that session.
 */
export function findConversationRequest(activeRequests, { key = "", sessionId = "", owner = "" } = {}) {
  const exact = activeRequests?.get?.(key);
  if (exact && requestRetainsVisibleTurn(exact)) return exact;
  const candidates = [...(activeRequests?.values?.() || [])].filter((request) => (
    requestRetainsVisibleTurn(request)
    && request.sessionId === sessionId
    && (!owner || !request.owner || request.owner === owner)
  ));
  if (candidates.length === 1) return candidates[0];
  const sessionCandidates = [...(activeRequests?.values?.() || [])].filter((request) => (
    requestRetainsVisibleTurn(request) && request.sessionId === sessionId
  ));
  return sessionCandidates.length === 1 ? sessionCandidates[0] : null;
}

export function markRequestTerminal(request, status) {
  if (request) request.backendStatus = status;
}

export function finishRequestCleanup(request) {
  request?.finishCleanup?.();
}
