const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);

/** Attach the client-side lifecycle that outlives a managed backend run. */
export function initializeRequestLifecycle(request) {
  let resolveCleanup;
  const cleanupComplete = new Promise((resolve) => { resolveCleanup = resolve; });
  return Object.assign(request, {
    backendStatus: "starting",
    cleanupComplete,
    finishCleanup: resolveCleanup,
  });
}

export function requestHasActiveRun(request) {
  return Boolean(request && !TERMINAL_RUN_STATUSES.has(request.backendStatus));
}

export function markRequestTerminal(request, status) {
  if (request) request.backendStatus = status;
}

export function finishRequestCleanup(request) {
  request?.finishCleanup?.();
}
