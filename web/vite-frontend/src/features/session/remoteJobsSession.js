/**
 * Attach the Remote Jobs view to an active session.
 *
 * The identity guard prevents a delayed session-creation response from
 * replacing polling for a session the user selected in the meantime.
 */
export async function activateRemoteJobsSession({
  state,
  controller,
  sessionId,
  owner,
} = {}) {
  if (!sessionId || !owner) return false;
  if (sessionId !== state?.sessionId || owner !== state?.activeSessionUserId) return false;

  controller.startPolling(sessionId, owner);
  await controller.load(sessionId, owner);
  return sessionId === state.sessionId && owner === state.activeSessionUserId;
}
