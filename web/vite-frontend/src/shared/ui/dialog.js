const OPEN_DIALOGS = [];

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "area[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable]:not([contenteditable='false'])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isVisible(element) {
  if (!(element instanceof HTMLElement)) return false;
  if (element.hidden || element.closest("[hidden], [aria-hidden='true']")) return false;
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}

function focusElement(element) {
  if (!element?.focus) return;
  try {
    element.focus({ preventScroll: true });
  } catch (_) {
    element.focus();
  }
}

function removeFromStack(controller) {
  const index = OPEN_DIALOGS.lastIndexOf(controller);
  if (index >= 0) OPEN_DIALOGS.splice(index, 1);
}

/**
 * Adds accessible dialog behavior to an existing overlay.
 *
 * The overlay's hidden class remains the source of truth for visibility, so
 * adopting this controller does not impose layout or animation styles.
 */
export function createDialogController({
  element,
  hiddenClass = "hidden",
  initialFocus = null,
  label = "",
  labelledBy = "",
  onClose = null,
} = {}) {
  if (!(element instanceof HTMLElement)) {
    throw new TypeError("createDialogController requires a dialog element");
  }

  element.setAttribute("role", "dialog");
  element.setAttribute("aria-modal", "true");
  if (labelledBy) {
    element.setAttribute("aria-labelledby", labelledBy);
    element.removeAttribute("aria-label");
  } else if (label) {
    element.setAttribute("aria-label", label);
    element.removeAttribute("aria-labelledby");
  }
  element.setAttribute("aria-hidden", String(element.classList.contains(hiddenClass)));

  const restoreTabIndex = element.hasAttribute("tabindex") ? element.getAttribute("tabindex") : null;
  if (!element.hasAttribute("tabindex")) element.setAttribute("tabindex", "-1");

  let previouslyFocused = null;
  let focusRequest = 0;
  let controller;

  function isOpen() {
    return !element.classList.contains(hiddenClass);
  }

  function focusableElements() {
    return [...element.querySelectorAll(FOCUSABLE_SELECTOR)].filter(isVisible);
  }

  function resolveInitialFocus() {
    const candidate = typeof initialFocus === "function"
      ? initialFocus()
      : typeof initialFocus === "string"
        ? element.querySelector(initialFocus)
        : initialFocus;
    if (candidate && element.contains(candidate) && isVisible(candidate)) return candidate;
    return focusableElements()[0] || element;
  }

  function handleKeydown(event) {
    if (OPEN_DIALOGS.at(-1) !== controller || !isOpen()) return;

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      controller.close("escape");
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = focusableElements();
    if (!focusable.length) {
      event.preventDefault();
      focusElement(element);
      return;
    }

    const first = focusable[0];
    const last = focusable.at(-1);
    const activeElement = document.activeElement;
    const focusIsInside = element.contains(activeElement);
    if (event.shiftKey && (!focusIsInside || activeElement === first)) {
      event.preventDefault();
      focusElement(last);
    } else if (!event.shiftKey && (!focusIsInside || activeElement === last)) {
      event.preventDefault();
      focusElement(first);
    }
  }

  function open() {
    if (isOpen()) return false;

    previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.classList.remove(hiddenClass);
    element.setAttribute("aria-hidden", "false");
    removeFromStack(controller);
    OPEN_DIALOGS.push(controller);
    document.addEventListener("keydown", handleKeydown, true);

    const request = ++focusRequest;
    queueMicrotask(() => {
      if (request === focusRequest && isOpen() && OPEN_DIALOGS.at(-1) === controller) {
        focusElement(resolveInitialFocus());
      }
    });
    return true;
  }

  function close(reason = "programmatic") {
    if (!isOpen()) return false;

    focusRequest += 1;
    document.removeEventListener("keydown", handleKeydown, true);
    removeFromStack(controller);

    const restoreTarget = previouslyFocused;
    previouslyFocused = null;
    element.classList.add(hiddenClass);
    element.setAttribute("aria-hidden", "true");
    onClose?.({ reason });
    if (restoreTarget?.isConnected) focusElement(restoreTarget);
    return true;
  }

  function destroy() {
    if (isOpen()) close("destroy");
    document.removeEventListener("keydown", handleKeydown, true);
    removeFromStack(controller);
    focusRequest += 1;
    if (restoreTabIndex === null) element.removeAttribute("tabindex");
    else element.setAttribute("tabindex", restoreTabIndex);
  }

  controller = { close, destroy, element, isOpen, open };
  return controller;
}
