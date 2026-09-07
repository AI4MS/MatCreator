function sessionTabTooltip(title) {
  return `${title || "Chat"}\nDouble-click to edit session name`;
}

export function createSessionSummaryController({
  state,
  summaryElement,
  tabElement,
  createTextReveal,
  rerenderSessionList,
  fetchImpl = globalThis.fetch,
}) {
  let initialized = false;

  function stopReveal() {
    summaryElement?._textReveal?.cancel();
    if (summaryElement) summaryElement._textReveal = null;
  }

  function reveal(text) {
    if (!summaryElement) return;
    const controller = createTextReveal(summaryElement);
    summaryElement._textReveal = controller;
    controller.append(text);
    controller.finish();
  }

  function render(summary, { animate = false } = {}) {
    if (!summaryElement) return;
    const defaultTitle = summaryElement.dataset.defaultTitle || "Chat";
    stopReveal();
    summaryElement.classList.remove("session-summary-placeholder");
    if (!summary) {
      summaryElement.textContent = defaultTitle;
      tabElement?.setAttribute("title", sessionTabTooltip(defaultTitle));
      return;
    }

    tabElement?.setAttribute("title", sessionTabTooltip(summary));
    if (animate) reveal(summary);
    else summaryElement.textContent = summary;
  }

  async function save(sessionId, summary) {
    try {
      const owner = state.activeSessionUserId || state.userId || "";
      const query = new URLSearchParams();
      if (owner) query.set("user_id", owner);
      const suffix = query.size ? `?${query}` : "";
      await fetchImpl(`/api/sessions/${encodeURIComponent(sessionId)}/summary${suffix}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ summary }),
      });
    } catch (_) {
      // A title is optional metadata; the active conversation remains usable.
    }
  }

  async function generate(sessionId) {
    try {
      const owner = state.activeSessionUserId || state.userId || "";
      const query = new URLSearchParams();
      if (owner) query.set("user_id", owner);
      const suffix = query.size ? `?${query}` : "";
      const response = await fetchImpl(
        `/api/sessions/${encodeURIComponent(sessionId)}/summarize${suffix}`,
        { method: "POST" },
      );
      if (!response.ok) return null;
      const data = await response.json();
      if (!data.summary) return null;

      state.sessionSummaries[sessionId] = data.summary;
      state.summaryGeneratedFor.add(sessionId);
      if (sessionId === state.sessionId) render(data.summary, { animate: true });
      rerenderSessionList?.();
      return data.summary;
    } catch (_) {
      return null;
    }
  }

  function startEditing() {
    if (!summaryElement || !tabElement || tabElement.querySelector("input")) return;
    const defaultTitle = summaryElement.dataset.defaultTitle || "Chat";
    const isPlaceholder = summaryElement.classList.contains("session-summary-placeholder");
    const original = isPlaceholder || summaryElement.textContent === defaultTitle
      ? ""
      : summaryElement.textContent;
    const sessionId = state.sessionId;
    const input = document.createElement("input");
    input.type = "text";
    input.value = original;
    input.className = "session-summary-input";
    input.maxLength = 60;
    input.placeholder = "Enter session name…";
    const labelWidth = Math.ceil(summaryElement.getBoundingClientRect().width);
    input.style.width = `${Math.max(44, labelWidth)}px`;
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("dblclick", (event) => event.stopPropagation());
    summaryElement.style.display = "none";
    tabElement.insertBefore(input, summaryElement);
    input.focus();
    input.select();

    let finished = false;
    const finish = async (shouldSave) => {
      if (finished) return;
      finished = true;
      const nextSummary = input.value.trim();
      input.remove();
      summaryElement.style.display = "";

      if (!shouldSave) {
        render(original || state.sessionSummaries[sessionId] || "");
        return;
      }
      if (nextSummary === original) return;

      if (nextSummary) {
        state.sessionSummaries[sessionId] = nextSummary;
        state.summaryGeneratedFor.add(sessionId);
      } else {
        delete state.sessionSummaries[sessionId];
        state.summaryGeneratedFor.delete(sessionId);
      }
      if (state.sessionId === sessionId) render(nextSummary);
      rerenderSessionList?.();
      await save(sessionId, nextSummary);
    };

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        void finish(false);
      }
    });
    input.addEventListener("blur", () => void finish(true));
  }

  const handleDoubleClick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    startEditing();
  };

  function init() {
    if (initialized) return;
    initialized = true;
    tabElement?.addEventListener("dblclick", handleDoubleClick);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    tabElement?.removeEventListener("dblclick", handleDoubleClick);
    stopReveal();
  }

  return { init, destroy, render, save, generate, startEditing };
}
