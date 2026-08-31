function normalizedOwner(session, fallbackOwner = "") {
  const owner = session?.userId ?? session?.user_id ?? fallbackOwner;
  return owner === undefined || owner === null ? "" : String(owner);
}

/**
 * Validate a persisted session selection against the list the backend says is
 * currently accessible. Local mode can expose historical UUID-owned sessions,
 * so it must honor the stored owner instead of coercing every tuple to `user`.
 */
export function validateStoredSessionSelection({
  sessions,
  sessionId,
  storedOwner,
  deploymentMode,
  isAdmin,
  currentUserId,
}) {
  if (!sessionId || !Array.isArray(sessions)) return null;

  const selectedId = String(sessionId);
  const currentOwner = String(currentUserId || "");
  const requestedOwner = String(storedOwner || "");
  const matchingSessions = sessions.filter((session) => String(session?.id || "") === selectedId);
  if (!matchingSessions.length) return null;

  let owner = currentOwner;
  if (deploymentMode === "server") {
    if (isAdmin) {
      if (!requestedOwner) return null;
      owner = requestedOwner;
    } else if (requestedOwner && requestedOwner !== currentOwner) {
      return null;
    }
  } else if (requestedOwner) {
    owner = requestedOwner;
  } else {
    // Older local selections did not persist an owner. Restore only when the
    // session id identifies one unambiguous owner in the accessible list.
    const owners = new Set(matchingSessions.map((session) => normalizedOwner(session, currentOwner)));
    if (owners.size !== 1) return null;
    [owner] = owners;
  }

  const found = matchingSessions.some((session) => normalizedOwner(session, currentOwner) === owner);
  return found ? { sessionId: selectedId, owner } : null;
}
