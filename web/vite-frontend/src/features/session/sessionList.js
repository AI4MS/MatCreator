import { httpClient } from "../../shared/api/http.js";

/**
 * Owns session sidebar rendering and filtering.
 *
 * Keeping this concern outside the application bootstrap makes it possible to
 * change the sidebar independently of session loading and chat streaming.
 */
export function createSessionListController({
  state,
  sessionListEl,
  refreshButton,
  filterElement,
  activeSessionRequest,
  sessionRequestKey,
  switchSession,
  deleteSession,
  downloadSessionLog,
  sessionDisplayStatus: getSessionDisplayStatus,
  showDraft,
  showSessionDetails,
}) {
  let lastSessions = [];

  async function loadSessions({ retries = 0, retryDelayMs = 250 } = {}) {
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      if (!state.userId) return null;
      try {
        const sessions = state.isAdmin
          ? await httpClient.getJson("/api/admin/sessions", { query: { user_id: state.userId } })
          : await httpClient.getJson(`/api/users/${encodeURIComponent(state.userId)}/sessions`);
        if (sessions === null) return null;
        render(sessions);
        return Array.isArray(sessions) ? sessions : [];
      } catch (_) {
        // Vite can become available a moment before the API proxy. Retry only
        // during initial bootstrap; user-triggered refreshes remain immediate.
        if (attempt === retries) return null;
        await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs * (attempt + 1)));
      }
    }
    return null;
  }

  function defaultSessionDisplayStatus(session, owner) {
    if (activeSessionRequest(sessionRequestKey(session.id, owner))) return "running";
    const status = String(session.status || session.phase || "").toLowerCase();
    return ["running", "idle"].includes(status) ? status : "idle";
  }

  const sessionDisplayStatus = getSessionDisplayStatus || defaultSessionDisplayStatus;

  function render(sessions) {
    lastSessions = Array.isArray(sessions) ? sessions : [];
    sessionListEl.innerHTML = "";
    if (!lastSessions.length) {
      sessionListEl.innerHTML = '<li class="empty">No sessions yet</li>';
      return;
    }

    lastSessions
      .slice()
      .filter((session) => state.sessionStatusFilter === "all"
        || sessionDisplayStatus(session, session.userId || state.userId) === state.sessionStatusFilter)
      .sort((a, b) => (b.lastUpdateTime || 0) - (a.lastUpdateTime || 0))
      .forEach((session) => renderSession(session));
  }

  function renderSession(session) {
    const owner = session.userId || state.userId;
    const isActive = session.id === state.sessionId && owner === state.activeSessionUserId;
    const status = sessionDisplayStatus(session, owner);
    const item = document.createElement("li");
    item.className = `session-item${isActive ? " active" : ""}`;
    item.dataset.owner = owner;

    const content = document.createElement("button");
    content.type = "button";
    content.className = "session-item-content";
    const rawSummary = session.summary || state.sessionSummaries[session.id] || "";
    const summary = typeof rawSummary === "string" ? rawSummary.trim() : "";
    const nameLine = document.createElement("span");
    nameLine.className = "session-item-name";
    const statusIndicator = document.createElement("span");
    statusIndicator.className = `session-status-indicator status-${status}`;
    statusIndicator.title = status === "running" ? "Agent is running" : "Session is idle";
    statusIndicator.setAttribute("aria-label", statusIndicator.title);
    const nameText = document.createElement("span");
    nameText.className = "session-item-name-text";
    nameText.textContent = summary || "Unnamed session";
    nameLine.append(statusIndicator, nameText);
    content.append(nameLine);
    const buttons = [createLogButton(session.id, owner)];
    if (showDraft) buttons.push(createDraftButton(session.id, owner, status));
    buttons.push(createDeleteButton(session.id));
    const actions = document.createElement("span");
    actions.className = "session-item-actions";
    actions.append(...buttons);
    item.append(content, actions);
    item.title = `${summary || "Unnamed session"}\nRight-click for session details`;
    if (isActive) content.setAttribute("aria-current", "page");
    item.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button && button !== content) return;
      switchSession(session.id, owner, { knownRunning: status === "running" });
    });
    item.addEventListener("contextmenu", (event) => {
      if (event.target.closest("button") && !event.target.closest(".session-item-content")) return;
      event.preventDefault();
      showSessionDetails?.({ ...session, summary }, owner);
    });
    sessionListEl.appendChild(item);
  }

  function createLogButton(sessionId, owner) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item-log";
    button.textContent = "LOG JSON";
    button.title = "Download full session log";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      downloadSessionLog(sessionId, owner);
    });
    return button;
  }

  function createDraftButton(sessionId, owner, status) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item-draft";
    button.textContent = "GENERATE";
    button.title = "Generate a staged benchmark question from this session";
    button.disabled = status === "running";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      showDraft(sessionId, owner);
    });
    return button;
  }

  function createDeleteButton(sessionId) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item-delete";
    button.textContent = "×";
    button.title = "Delete session";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(sessionId);
    });
    return button;
  }

  function setFilter(value) {
    state.sessionStatusFilter = value || "all";
    render(lastSessions);
  }

  function configureFilter() {
    if (!filterElement) return;
    const trigger = filterElement.querySelector(".custom-select-trigger");
    const options = [...filterElement.querySelectorAll(".custom-select-options li")];
    if (!trigger || !options.length) return;

    const updateFilter = (value) => {
      const label = filterElement.querySelector(`[data-value="${value}"]`)?.textContent || "All";
      trigger.textContent = label;
      trigger.dataset.value = value;
      options.forEach((option) => option.setAttribute("aria-selected", String(option.dataset.value === value)));
      setFilter(value);
    };
    const close = () => {
      filterElement.classList.remove("is-open");
      filterElement.setAttribute("aria-expanded", "false");
    };
    const toggle = () => {
      filterElement.classList.toggle("is-open");
      filterElement.setAttribute("aria-expanded", String(filterElement.classList.contains("is-open")));
    };

    filterElement.addEventListener("click", (event) => { event.stopPropagation(); toggle(); });
    options.forEach((option) => option.addEventListener("click", (event) => {
      event.stopPropagation();
      updateFilter(option.dataset.value);
      close();
    }));
    document.addEventListener("click", (event) => {
      if (!filterElement.contains(event.target)) close();
    });
    filterElement.addEventListener("keydown", (event) => {
      const active = filterElement.querySelector('[aria-selected="true"]');
      let index = options.indexOf(active);
      if (event.key === "ArrowDown" || event.key === "ArrowRight") {
        event.preventDefault();
        updateFilter(options[(index + 1) % options.length].dataset.value);
      } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
        event.preventDefault();
        updateFilter(options[(index - 1 + options.length) % options.length].dataset.value);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      } else if (event.key === "Escape") {
        close();
      }
    });
  }

  refreshButton?.addEventListener("click", (event) => { event.stopPropagation(); loadSessions(); });
  configureFilter();

  return { loadSessions, render, rerender: () => render(lastSessions), setFilter };
}
