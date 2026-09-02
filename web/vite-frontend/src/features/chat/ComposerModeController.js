const MODES = Object.freeze({
  flash: {
    label: "Flash",
    icon: '<svg viewBox="0 0 24 24"><path d="m13 2-9 12h7l-1 8 10-13h-7z"/></svg>',
  },
  normal: {
    label: "Standard",
    icon: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/></svg>',
  },
  bench: {
    label: "Bench",
    icon: '<svg viewBox="0 0 24 24"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 16h8"/></svg>',
  },
});

export function normalizeAgentMode(mode) {
  return Object.hasOwn(MODES, mode) ? mode : "normal";
}

export function createComposerModeController({
  state,
  storageKey,
  selector,
  trigger,
  menu,
  inputContainer,
  onModeChanged,
  storage = globalThis.localStorage,
  documentRef = document,
  windowRef = window,
}) {
  let initialized = false;
  let menuPinned = false;
  let focusTimer = null;
  const buttons = selector ? [...selector.querySelectorAll(".mode-btn")] : [];
  const label = trigger?.querySelector(".mode-trigger-label");
  const icon = trigger?.querySelector(".mode-trigger-icon");

  function setMenuOpen(open, { pinned = menuPinned, focusSelected = false } = {}) {
    menuPinned = open && pinned;
    selector?.classList.toggle("is-open", open);
    trigger?.setAttribute("aria-expanded", String(open));
    if (open && focusSelected) {
      const selected = buttons.find((button) => button.dataset.mode === state.agentMode) || buttons[0];
      selected?.focus();
    }
  }

  function render(mode) {
    const normalized = normalizeAgentMode(mode);
    const detail = MODES[normalized];
    if (label) label.textContent = detail.label;
    if (icon) icon.innerHTML = detail.icon;
    if (selector) selector.dataset.selectedMode = normalized;
    if (inputContainer) inputContainer.dataset.agentMode = normalized;
    buttons.forEach((button) => {
      const selected = button.dataset.mode === normalized;
      button.classList.toggle("mode-btn-active", selected);
      button.setAttribute("aria-checked", String(selected));
    });
  }

  function select(mode, { notify = true, focusTrigger = true } = {}) {
    const normalized = normalizeAgentMode(mode);
    state.agentMode = normalized;
    try { storage?.setItem(storageKey, normalized); } catch (_) { /* preference is non-critical */ }
    render(normalized);
    if (notify) void onModeChanged?.(normalized);
    setMenuOpen(false, { pinned: false });
    if (focusTrigger) trigger?.focus();
    return normalized;
  }

  const handleTriggerClick = () => {
    const open = !selector?.classList.contains("is-open");
    setMenuOpen(open, { pinned: open });
  };
  const handleSelectorClick = (event) => {
    const button = event.target.closest(".mode-btn");
    if (button) select(button.dataset.mode);
  };
  const handleKeydown = (event) => {
    const currentIndex = buttons.indexOf(documentRef.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      setMenuOpen(false, { pinned: false });
      trigger?.focus();
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && documentRef.activeElement === trigger) {
      event.preventDefault();
      const open = !selector?.classList.contains("is-open");
      setMenuOpen(open, { pinned: open, focusSelected: open });
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      if (!buttons.length) return;
      event.preventDefault();
      const selectedIndex = Math.max(0, buttons.findIndex(
        (button) => button.dataset.mode === state.agentMode,
      ));
      const baseIndex = currentIndex < 0 ? selectedIndex : currentIndex;
      const nextIndex = event.key === "Home" ? 0
        : event.key === "End" ? buttons.length - 1
          : (baseIndex + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      setMenuOpen(true, { pinned: true });
      buttons[nextIndex].focus();
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && currentIndex >= 0) {
      event.preventDefault();
      select(buttons[currentIndex].dataset.mode);
    }
  };
  const handleDocumentPointerDown = (event) => {
    if (!selector?.contains(event.target)) setMenuOpen(false, { pinned: false });
  };
  const handleFocusOut = () => {
    windowRef.clearTimeout(focusTimer);
    focusTimer = windowRef.setTimeout(() => {
      if (!selector?.contains(documentRef.activeElement)) setMenuOpen(false, { pinned: false });
    });
  };

  function init() {
    if (initialized || !selector || !trigger || !menu) return;
    initialized = true;
    state.agentMode = normalizeAgentMode(state.agentMode);
    render(state.agentMode);
    trigger.addEventListener("click", handleTriggerClick);
    selector.addEventListener("click", handleSelectorClick);
    selector.addEventListener("keydown", handleKeydown);
    documentRef.addEventListener("pointerdown", handleDocumentPointerDown);
    selector.addEventListener("focusout", handleFocusOut);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    windowRef.clearTimeout(focusTimer);
    trigger.removeEventListener("click", handleTriggerClick);
    selector.removeEventListener("click", handleSelectorClick);
    selector.removeEventListener("keydown", handleKeydown);
    documentRef.removeEventListener("pointerdown", handleDocumentPointerDown);
    selector.removeEventListener("focusout", handleFocusOut);
  }

  return { init, destroy, select, render, setMenuOpen };
}
