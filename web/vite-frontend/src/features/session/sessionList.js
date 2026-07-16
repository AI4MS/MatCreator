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
}) {
  let lastSessions = [];

  async function loadSessions() {
    if (!state.userId) return;
    try {
      const response = state.isAdmin
        ? await fetch(`/api/admin/sessions?user_id=${encodeURIComponent(state.userId)}`)
        : await fetch(`/api/users/${encodeURIComponent(state.userId)}/sessions`);
      if (!response.ok) return;
      render(await response.json());
    } catch (_) {
      // The API may be unavailable while the frontend is starting.
    }
  }

  function sessionDisplayStatus(session, owner) {
    if (activeSessionRequest(sessionRequestKey(session.id, owner))) return "running";
    const status = String(session.status || session.phase || "").toLowerCase();
    return ["running", "idle"].includes(status) ? status : "idle";
  }

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

    const content = document.createElement("div");
    content.className = "session-item-content";
    const label = state.isAdmin ? `${owner} / ${session.id}` : session.id;
    const summary = session.summary || state.sessionSummaries[session.id];
    const idLine = document.createElement("div");
    idLine.className = "session-item-id";
    idLine.textContent = label;
    const statusIndicator = document.createElement("span");
    statusIndicator.className = `session-status-indicator status-${status}`;
    statusIndicator.title = status;
    idLine.prepend(statusIndicator);

    if (summary) {
      item.classList.add("has-summary");
      const summaryLine = document.createElement("div");
      summaryLine.className = "session-item-summary";
      summaryLine.textContent = summary;
      content.append(summaryLine, idLine);
    } else {
      content.append(idLine);
    }
    item.append(content, createLogButton(session.id, owner), createDeleteButton(session.id));
    item.title = summary ? `${summary}\n${label}` : label;
    item.addEventListener("click", () => switchSession(session.id, owner));
    sessionListEl.appendChild(item);
  }

  function createLogButton(sessionId, owner) {
    const button = document.createElement("button");
    button.className = "session-item-log";
    button.textContent = "LOG JSON";
    button.title = "Download full session log";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      downloadSessionLog(sessionId, owner);
    });
    return button;
  }

  function createDeleteButton(sessionId) {
    const button = document.createElement("button");
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
