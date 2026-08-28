/**
 * Keeps native <details> state and viewport behavior consistent across
 * frequently re-rendered views.
 */
export function createDisclosureController({
  captureScrollPosition,
  restoreScrollPosition,
} = {}) {
  const openState = new Map();

  function wire(details, key, { defaultOpen = false, onToggle } = {}) {
    if (!details || !key) return details;

    details.dataset.disclosureKey = key;
    details.querySelectorAll("p, pre, blockquote, li, table, h1, h2, h3, h4, h5, h6").forEach((block, index) => {
      block.dataset.readingAnchor = `${key}:block:${index}`;
    });
    details.open = openState.has(key) ? openState.get(key) : Boolean(defaultOpen);

    let pendingViewport = null;
    let interacted = false;
    const captureViewport = () => {
      interacted = true;
      // Use this disclosure as the reading anchor. At the bottom the renderer
      // returns an explicit follow-bottom snapshot; elsewhere the Node stays
      // at the same viewport offset while its body expands below it.
      pendingViewport = captureScrollPosition?.(details, {
        force: true,
        detachBottom: true,
      });
    };
    const isSummaryEvent = (event) => {
      const target = event.target;
      const summary = target?.closest?.("summary");
      const control = target?.closest?.("button, a, input, select, textarea");
      return summary?.parentElement === details && !control;
    };
    details.addEventListener("click", (event) => {
      if (isSummaryEvent(event)) captureViewport();
    });
    details.addEventListener("keydown", (event) => {
      if (!isSummaryEvent(event)) return;
      if (event.key === "Enter" || event.key === " ") captureViewport();
    });

    details.addEventListener("toggle", (event) => {
      if (event.target !== details) return;
      const viewport = pendingViewport;
      pendingViewport = null;
      // Assigning `open` while rebuilding the view also emits `toggle`.
      // Only a toggle preceded by interaction is a durable user choice.
      if (!interacted) return;
      interacted = false;
      openState.set(key, details.open);
      onToggle?.(details.open);
      if (viewport) restoreScrollPosition?.(viewport);
    });
    return details;
  }

  function prune(liveKeys) {
    for (const key of openState.keys()) {
      if (!liveKeys.has(key)) openState.delete(key);
    }
  }

  function prunePrefix(prefix, liveKeys) {
    for (const key of openState.keys()) {
      if (key.startsWith(prefix) && !liveKeys.has(key)) openState.delete(key);
    }
  }

  function deletePrefix(prefix) {
    for (const key of openState.keys()) {
      if (key.startsWith(prefix)) openState.delete(key);
    }
  }

  function capture(root) {
    root?.querySelectorAll?.("details[data-disclosure-key]").forEach((details) => {
      openState.set(details.dataset.disclosureKey, details.open);
    });
  }

  return { capture, clear: () => openState.clear(), deletePrefix, prune, prunePrefix, state: openState, wire };
}
