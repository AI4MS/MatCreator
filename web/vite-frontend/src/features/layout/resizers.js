const MOBILE_LAYOUT_QUERY = window.matchMedia("(max-width: 900px)");

const PANEL_HEIGHT_DEFAULTS = {};
const PANEL_HEIGHT_BOUNDS = {};

const COL_WIDTH_DEFAULTS = {
  "graph-column": 360,
  "side-panel": 320,
};

const COL_WIDTH_BOUNDS = {
  "graph-column": { min: 240, max: 600 },
  "side-panel": { min: 240, max: 520 },
};

function clamp(number, min, max) {
  return Math.max(min, Math.min(max, number));
}

export function createLayoutController({ getUserId, onLayoutChanged, elements }) {
  const {
    graphResizer,
    graphColumn,
    sidePanel,
    fileExplorerCol,
    colResizerGraph,
    colResizerSide,
    colResizerFiles,
  } = elements;

  const isMobileLayout = () => MOBILE_LAYOUT_QUERY.matches;
  const panelStorageKey = (targetId) => `mat_panel_height_${getUserId() || "anon"}_${targetId}`;
  const colStorageKey = (colId) => `mat_col_width_${getUserId() || "anon"}_${colId}`;
  const getTargetHeight = (targetEl) => Math.round(targetEl.getBoundingClientRect().height);
  const getColWidth = (colEl) => Math.round(colEl.getBoundingClientRect().width);

  function applyTargetHeight(targetEl, heightPx) {
    if (!targetEl) return;
    const bounds = PANEL_HEIGHT_BOUNDS[targetEl.id];
    if (!bounds) return;
    targetEl.style.height = `${clamp(heightPx, bounds.min, bounds.max)}px`;
  }

  function persistTargetHeight(targetEl) {
    if (!targetEl) return;
    localStorage.setItem(panelStorageKey(targetEl.id), String(getTargetHeight(targetEl)));
  }

  function applyStoredPanelHeights() {
    for (const [targetId, fallback] of Object.entries(PANEL_HEIGHT_DEFAULTS)) {
      const element = document.getElementById(targetId);
      if (!element) continue;
      if (isMobileLayout()) {
        element.style.removeProperty("height");
        continue;
      }

      const raw = localStorage.getItem(panelStorageKey(targetId));
      const parsed = raw ? Number(raw) : fallback;
      applyTargetHeight(element, Number.isFinite(parsed) ? parsed : fallback);
    }
  }

  function syncPanelResizerVisibility() {
    graphResizer?.classList.add("hidden");
  }

  function initPanelResizer(handleEl, targetEl) {
    if (!handleEl || !targetEl) return;
    const keyStep = 16;
    const commit = () => {
      persistTargetHeight(targetEl);
      onLayoutChanged();
    };
    const resizeBy = (delta) => {
      applyTargetHeight(targetEl, getTargetHeight(targetEl) + delta);
      onLayoutChanged();
    };

    handleEl.addEventListener("pointerdown", (event) => {
      if (isMobileLayout() || handleEl.classList.contains("hidden")) return;
      event.preventDefault();
      const startY = event.clientY;
      const startHeight = getTargetHeight(targetEl);
      handleEl.classList.add("resizing");
      handleEl.setPointerCapture(event.pointerId);

      const onMove = (moveEvent) => {
        applyTargetHeight(targetEl, startHeight + moveEvent.clientY - startY);
        onLayoutChanged();
      };
      const onUp = () => {
        handleEl.classList.remove("resizing");
        handleEl.removeEventListener("pointermove", onMove);
        handleEl.removeEventListener("pointerup", onUp);
        handleEl.removeEventListener("pointercancel", onUp);
        commit();
      };
      handleEl.addEventListener("pointermove", onMove);
      handleEl.addEventListener("pointerup", onUp);
      handleEl.addEventListener("pointercancel", onUp);
    });

    handleEl.addEventListener("keydown", (event) => {
      if (isMobileLayout() || handleEl.classList.contains("hidden")) return;
      if (event.key === "ArrowUp") {
        event.preventDefault();
        resizeBy(-keyStep);
        commit();
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        resizeBy(keyStep);
        commit();
      }
    });
  }

  function applyColWidth(colEl, widthPx) {
    const bounds = COL_WIDTH_BOUNDS[colEl.id];
    if (!bounds) return;
    colEl.style.width = `${clamp(widthPx, bounds.min, bounds.max)}px`;
  }

  function persistColWidth(colEl) {
    localStorage.setItem(colStorageKey(colEl.id), String(getColWidth(colEl)));
  }

  function applyStoredColWidths() {
    for (const colId of ["graph-column", "side-panel"]) {
      const element = document.getElementById(colId);
      if (!element) continue;
      if (isMobileLayout()) {
        element.style.removeProperty("width");
        continue;
      }
      const raw = localStorage.getItem(colStorageKey(colId));
      const width = raw ? Number(raw) : COL_WIDTH_DEFAULTS[colId];
      applyColWidth(element, Number.isFinite(width) ? width : COL_WIDTH_DEFAULTS[colId]);
    }
  }

  function syncColResizerVisibility() {
    const mobile = isMobileLayout();
    colResizerGraph?.classList.toggle("hidden", mobile);
    colResizerSide?.classList.toggle("hidden", mobile);
    colResizerFiles?.classList.add("hidden");
  }

  function initColResizer(handleEl, targetEl, direction = 1) {
    if (!handleEl || !targetEl) return;
    const keyStep = 16;
    const commit = () => {
      persistColWidth(targetEl);
      onLayoutChanged();
    };

    handleEl.addEventListener("pointerdown", (event) => {
      if (isMobileLayout() || handleEl.classList.contains("hidden")) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = getColWidth(targetEl);
      handleEl.classList.add("resizing");
      handleEl.setPointerCapture(event.pointerId);
      const onMove = (moveEvent) => {
        applyColWidth(targetEl, startWidth + direction * (moveEvent.clientX - startX));
        onLayoutChanged();
      };
      const onUp = () => {
        handleEl.classList.remove("resizing");
        handleEl.removeEventListener("pointermove", onMove);
        handleEl.removeEventListener("pointerup", onUp);
        handleEl.removeEventListener("pointercancel", onUp);
        commit();
      };
      handleEl.addEventListener("pointermove", onMove);
      handleEl.addEventListener("pointerup", onUp);
      handleEl.addEventListener("pointercancel", onUp);
    });

    handleEl.addEventListener("keydown", (event) => {
      if (isMobileLayout() || handleEl.classList.contains("hidden")) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        applyColWidth(targetEl, getColWidth(targetEl) - direction * keyStep);
        commit();
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        applyColWidth(targetEl, getColWidth(targetEl) + direction * keyStep);
        commit();
      }
    });
  }

  function init() {
    refresh();
    initColResizer(colResizerGraph, graphColumn, 1);
    initColResizer(colResizerSide, sidePanel, -1);

    MOBILE_LAYOUT_QUERY.addEventListener("change", () => {
      applyStoredPanelHeights();
      syncPanelResizerVisibility();
      applyStoredColWidths();
      fileExplorerCol?.classList.add("is-open");
      syncColResizerVisibility();
      onLayoutChanged();
    });
  }

  function refresh() {
    applyStoredPanelHeights();
    syncPanelResizerVisibility();
    applyStoredColWidths();
    fileExplorerCol?.classList.add("is-open");
    syncColResizerVisibility();
  }

  return { init, refresh, syncPanelResizerVisibility };
}