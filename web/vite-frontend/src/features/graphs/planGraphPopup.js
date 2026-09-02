export function createPlanGraphPopupController({
  planGraph,
  popup,
  toggleButton,
  closeButton,
  zoomInButton,
  zoomOutButton,
  fitButton,
  previousButton,
  nextButton,
  windowRef = window,
}) {
  let initialized = false;
  let layoutFrame = null;
  let fitFrame = null;

  function cancelPendingFit() {
    if (layoutFrame !== null) windowRef.cancelAnimationFrame(layoutFrame);
    if (fitFrame !== null) windowRef.cancelAnimationFrame(fitFrame);
    layoutFrame = null;
    fitFrame = null;
  }

  function renderToggle(open) {
    toggleButton?.classList.toggle("is-open", open);
    toggleButton?.setAttribute("aria-pressed", String(open));
    const label = open ? "Close roadmap" : "Open roadmap";
    toggleButton?.setAttribute("title", label);
    toggleButton?.setAttribute("aria-label", label);
  }

  function show() {
    popup?.classList.remove("hidden");
    renderToggle(true);
    cancelPendingFit();
    layoutFrame = windowRef.requestAnimationFrame(() => {
      layoutFrame = null;
      fitFrame = windowRef.requestAnimationFrame(() => {
        fitFrame = null;
        if (popup?.classList.contains("hidden")) return;
        planGraph.notifyLayoutChanged();
        planGraph.fitToView({ animate: false });
      });
    });
  }

  function hide() {
    cancelPendingFit();
    popup?.classList.add("hidden");
    renderToggle(false);
  }

  function toggle() {
    if (popup?.classList.contains("hidden")) show();
    else hide();
  }

  const zoomIn = () => planGraph.zoomIn();
  const zoomOut = () => planGraph.zoomOut();
  const fit = () => planGraph.fitToView();
  const previous = () => planGraph.goPrev();
  const next = () => planGraph.goNext();

  function init() {
    if (initialized) return;
    initialized = true;
    toggleButton?.addEventListener("click", toggle);
    closeButton?.addEventListener("click", hide);
    zoomInButton?.addEventListener("click", zoomIn);
    zoomOutButton?.addEventListener("click", zoomOut);
    fitButton?.addEventListener("click", fit);
    previousButton?.addEventListener("click", previous);
    nextButton?.addEventListener("click", next);
    renderToggle(!popup?.classList.contains("hidden"));
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    cancelPendingFit();
    toggleButton?.removeEventListener("click", toggle);
    closeButton?.removeEventListener("click", hide);
    zoomInButton?.removeEventListener("click", zoomIn);
    zoomOutButton?.removeEventListener("click", zoomOut);
    fitButton?.removeEventListener("click", fit);
    previousButton?.removeEventListener("click", previous);
    nextButton?.removeEventListener("click", next);
  }

  return { init, destroy, show, hide, toggle };
}
