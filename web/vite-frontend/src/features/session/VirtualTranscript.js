import {
  Virtualizer,
  elementScroll,
  measureElement,
  observeElementOffset,
  observeElementRect,
} from "@tanstack/virtual-core";

export class VirtualTranscript {
  constructor({ chatArea, renderRow, estimateRow, onNeedRange, overscan = 6 }) {
    this.chatArea = chatArea;
    this.renderRow = renderRow;
    this.estimateRow = estimateRow;
    this.onNeedRange = onNeedRange;
    this.overscan = overscan;
    this.rows = [];
    this.rowElements = new Map();
    this.renderFrame = null;
    this.rendering = false;
    this.renderAgain = false;
    this.lastTotalSize = null;
    this.mode = "FOLLOW_OUTPUT";
    this.allowGapRequests = true;
    this.logicalAnchorRestores = 0;

    this.canvas = document.createElement("div");
    this.canvas.className = "virtual-transcript-canvas";
    this.liveHost = document.createElement("div");
    this.liveHost.className = "virtual-transcript-live";
    // Chat rendering helpers also serve non-virtual surfaces. Mark this
    // scroll container so they never compete with the viewport controller.
    chatArea.dataset.transcriptViewport = "virtual";
    chatArea.replaceChildren(this.canvas, this.liveHost);

    this.virtualizer = new Virtualizer({
      count: 0,
      getScrollElement: () => this.chatArea,
      estimateSize: (index) => this.estimateRow(this.rows[index]),
      getItemKey: (index) => this.rows[index]?.id || index,
      observeElementRect,
      observeElementOffset,
      measureElement,
      scrollToFn: elementScroll,
      overscan,
      anchorTo: "end",
      followOnAppend: false,
      useAnimationFrameWithResizeObserver: false,
      onChange: (instance) => this.handleVirtualizerChange(instance),
    });
    this.virtualizer.shouldAdjustScrollPositionOnItemSizeChange = (item, _delta, instance) => {
      const offset = instance.getScrollOffset() + instance.scrollAdjustments;
      // A full delta is correct only when the entire row is above the reading
      // position. For a first-measured row spanning the fold, renderNow keeps
      // the same proportional logical point after all measurements complete.
      return item.start + item.size <= offset;
    };
    this.virtualizer._willUpdate();
    this.cleanupVirtualizer = this.virtualizer._didMount();
    this.bindViewportState();
  }

  bindViewportState() {
    let interactionDirection = null;
    let pointerScrolling = false;
    let previousOffset = this.chatArea.scrollTop;
    this.chatArea.addEventListener("wheel", (event) => {
      interactionDirection = event.deltaY < 0 ? "backward" : event.deltaY > 0 ? "forward" : null;
      if (interactionDirection === "backward") this.detach();
      if (interactionDirection === "forward" && this.distanceFromEnd() <= 2) this.followOutput();
    }, { passive: true });
    this.chatArea.addEventListener("keydown", (event) => {
      if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
        interactionDirection = "backward";
        this.detach();
      } else if (event.key === "End") {
        interactionDirection = null;
        this.followOutput();
      } else if (["ArrowDown", "PageDown", " "].includes(event.key)) {
        interactionDirection = "forward";
      }
    }, { passive: true });
    this.chatArea.addEventListener("pointerdown", (event) => {
      if (event.target === this.chatArea) pointerScrolling = true;
    }, { passive: true });
    this.chatArea.addEventListener("scroll", () => {
      const offset = this.chatArea.scrollTop;
      if (pointerScrolling) interactionDirection = offset < previousOffset ? "backward" : offset > previousOffset ? "forward" : interactionDirection;
      previousOffset = offset;
      if (interactionDirection === "backward") this.detach();
      else if (interactionDirection === "forward" && this.distanceFromEnd() <= 2) this.mode = "FOLLOW_OUTPUT";
      if (!pointerScrolling) interactionDirection = null;
    }, { passive: true });
    const endPointerScroll = () => {
      if (pointerScrolling && interactionDirection === "forward" && this.distanceFromEnd() <= 2) this.mode = "FOLLOW_OUTPUT";
      pointerScrolling = false;
      interactionDirection = null;
    };
    window.addEventListener("pointerup", endPointerScroll, { passive: true });
    window.addEventListener("pointercancel", endPointerScroll, { passive: true });
    this.liveResizeObserver = new ResizeObserver(() => {
      if (this.mode === "FOLLOW_OUTPUT") this.scrollToEnd();
    });
    this.liveResizeObserver.observe(this.liveHost);
  }

  distanceFromEnd() {
    return Math.max(0, this.chatArea.scrollHeight - this.chatArea.clientHeight - this.chatArea.scrollTop);
  }

  detach() {
    this.mode = "DETACHED";
  }

  followOutput() {
    this.mode = "FOLLOW_OUTPUT";
    this.scrollToEnd();
  }

  currentOffset() { return this.virtualizer.scrollOffset || 0; }

  restoreOffset(offset) {
    this.mode = "DETACHED";
    this.virtualizer.scrollToOffset(Math.max(0, offset), { align: "start" });
  }

  scrollToEnd() {
    this.chatArea.scrollTop = Math.max(0, this.chatArea.scrollHeight - this.chatArea.clientHeight);
  }

  captureLogicalAnchor() {
    const offset = this.virtualizer.scrollOffset || 0;
    const item = this.virtualizer.getVirtualItemForOffset(offset);
    const row = item ? this.rows[item.index] : null;
    if (!item || !row || !item.size) return null;
    const fraction = Math.max(0, Math.min(0.999999, (offset - item.start) / item.size));
    return {
      rowId: row.id,
      pixelOffset: offset - item.start,
      eventPosition: row.startIndex + row.span * fraction,
    };
  }

  restoreLogicalAnchor(anchor, { force = false } = {}) {
    if (!anchor || (!force && this.rows.some((row) => row.id === anchor.rowId))) return;
    const index = this.rows.findIndex((row) => (
      anchor.eventPosition >= row.startIndex && anchor.eventPosition < row.endIndex
    ));
    if (index < 0) return;
    const row = this.rows[index];
    const item = this.virtualizer.getMeasurements()[index];
    if (!item) return;
    const fraction = row.span > 0
      ? Math.max(0, Math.min(0.999999, (anchor.eventPosition - row.startIndex) / row.span))
      : 0;
    this.logicalAnchorRestores += 1;
    this.virtualizer.scrollToOffset(item.start + item.size * fraction, { align: "start" });
  }

  setRows(rows, { follow = false } = {}) {
    const logicalAnchor = this.mode === "DETACHED" ? this.captureLogicalAnchor() : null;
    this.rows = rows;
    this.virtualizer.setOptions({
      ...this.virtualizer.options,
      count: rows.length,
      estimateSize: (index) => this.estimateRow(this.rows[index]),
      getItemKey: (index) => this.rows[index]?.id || index,
    });
    this.virtualizer._willUpdate();
    this.restoreLogicalAnchor(logicalAnchor);
    if (follow || this.mode === "FOLLOW_OUTPUT") this.allowGapRequests = false;
    this.renderNow();
    if (follow || this.mode === "FOLLOW_OUTPUT") {
      this.scrollToEnd();
      this.allowGapRequests = true;
    }
  }

  scheduleRender() {
    if (this.renderFrame !== null) return;
    this.renderFrame = requestAnimationFrame(() => {
      this.renderFrame = null;
      this.renderNow();
    });
  }

  handleVirtualizerChange(instance) {
    const totalSize = instance.getTotalSize();
    const geometryChanged = this.lastTotalSize !== null
      && Math.abs(totalSize - this.lastTotalSize) > 0.5;
    this.lastTotalSize = totalSize;
    if (!geometryChanged) {
      this.scheduleRender();
      return;
    }
    // ResizeObserver runs before paint. Commit changed transforms now so the
    // browser never paints the estimate and then corrects it one frame later.
    if (this.rendering) {
      this.renderAgain = true;
      return;
    }
    this.renderNow();
  }

  renderNow() {
    if (this.rendering) {
      this.renderAgain = true;
      return;
    }
    this.rendering = true;
    this.lastTotalSize = this.virtualizer.getTotalSize();
    const measurementAnchor = this.mode === "DETACHED" ? this.captureLogicalAnchor() : null;
    const restoreAfterMeasurement = measurementAnchor
      && !this.virtualizer.itemSizeCache.has(measurementAnchor.rowId);
    let passes = 0;
    try {
      do {
        this.renderAgain = false;
        this.renderPass();
        passes += 1;
      } while (this.renderAgain && passes < 2);
      if (restoreAfterMeasurement) {
        this.canvas.style.height = `${this.virtualizer.getTotalSize()}px`;
        this.restoreLogicalAnchor(measurementAnchor, { force: true });
      }
    } finally {
      this.rendering = false;
    }
    if (this.renderAgain) this.scheduleRender();
  }

  renderPass() {
    const virtualItems = this.virtualizer.getVirtualItems();
    this.canvas.style.height = `${this.virtualizer.getTotalSize()}px`;
    const mounted = new Set();
    virtualItems.forEach((virtualItem) => {
      const row = this.rows[virtualItem.index];
      if (!row) return;
      mounted.add(row.id);
      let entry = this.rowElements.get(row.id);
      if (!entry) {
        const element = document.createElement("div");
        element.className = `virtual-transcript-row is-${row.type}`;
        entry = { element, revision: null };
        this.rowElements.set(row.id, entry);
        this.canvas.appendChild(element);
      }
      const { element } = entry;
      element.dataset.index = String(virtualItem.index);
      element.dataset.transcriptRowId = row.id;
      element.style.transform = `translateY(${virtualItem.start}px)`;
      if (row.type === "gap") {
        element.style.height = `${virtualItem.size}px`;
        if (entry.revision !== row.revision) element.replaceChildren();
        if (this.allowGapRequests) this.requestGap(row, virtualItem);
      } else {
        element.style.height = "";
        if (entry.revision !== row.revision) {
          element.replaceChildren();
          this.renderRow(row, element);
          entry.revision = row.revision;
        }
        this.virtualizer.measureElement(element);
      }
    });
    [...this.rowElements.entries()].forEach(([key, entry]) => {
      if (mounted.has(key)) return;
      entry.element.remove();
      this.rowElements.delete(key);
    });
  }

  requestGap(row, virtualItem) {
    if (!this.onNeedRange || !virtualItem.size) return;
    const viewportCenter = (this.virtualizer.scrollOffset || 0) + (this.virtualizer.scrollRect?.height || 0) / 2;
    const fraction = Math.max(0, Math.min(0.999, (viewportCenter - virtualItem.start) / virtualItem.size));
    const targetIndex = row.startIndex + Math.floor(row.span * fraction);
    this.onNeedRange({ targetIndex, startIndex: row.startIndex, endIndex: row.endIndex });
  }

  clearLive() {
    this.liveHost.replaceChildren();
  }

  metrics() {
    const range = this.virtualizer.range;
    return {
      mountedRows: this.rowElements.size,
      logicalRows: this.rows.length,
      virtualRange: range ? [range.startIndex, range.endIndex] : null,
      measuredRows: this.virtualizer.itemSizeCache.size,
      logicalAnchorRestores: this.logicalAnchorRestores,
      estimatedHeight: this.virtualizer.getTotalSize(),
      viewportMode: this.mode,
    };
  }

  reset() {
    this.rows = [];
    this.rowElements.forEach((entry) => entry.element.remove());
    this.rowElements.clear();
    this.canvas.replaceChildren();
    this.canvas.style.height = "0px";
    this.liveHost.replaceChildren();
    if (!this.canvas.isConnected || !this.liveHost.isConnected) {
      this.chatArea.replaceChildren(this.canvas, this.liveHost);
    }
    this.virtualizer.setOptions({ ...this.virtualizer.options, count: 0 });
    this.virtualizer._willUpdate();
  }

  destroy() {
    if (this.renderFrame !== null) cancelAnimationFrame(this.renderFrame);
    this.liveResizeObserver.disconnect();
    this.cleanupVirtualizer?.();
    delete this.chatArea.dataset.transcriptViewport;
  }
}
