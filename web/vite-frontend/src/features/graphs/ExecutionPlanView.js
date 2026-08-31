import { Network, DataSet } from "vis-network/standalone";
import { installNetworkWheelZoom } from "./networkWheelZoom.js";
import { httpClient } from "../../shared/api/http.js";
import { applyGraphUpdate } from "./graphUpdates.js";

function clamp(number, min, max) {
  return Math.max(min, Math.min(max, number));
}

const PLAN_NODE_STATUS_COLORS = {
  pending:   { bg: "#374151", border: "#6B7280", font: "#9CA3AF" },
  running:   { bg: "#FBBF24", border: "#F59E0B", font: "#1a1a1a" },
  success:   { bg: "#10B981", border: "#059669", font: "#043F2E" },
  failed:    { bg: "#EF4444", border: "#DC2626", font: "#fff" },
  blocked:   { bg: "#1F2937", border: "#374151", font: "#4B5563" },
};

const PLAN_GRAPH_DEFAULT_LAYOUT = {
  direction: "LR",
  sortMethod: "directed",
  nodeSpacing: 125,
  levelSeparation: 200,
  blockShifting: true,
  edgeMinimization: true,
};

const PLAN_GRAPH_MIN_SCALE = 0.15;
const PLAN_GRAPH_MAX_SCALE = 4;

export class ExecutionPlanView {
  constructor(containerId, options = {}) {
    this._container = document.getElementById(containerId);
    this._toggleButton = options.toggleButton || null;
    this._thumbnailElement = options.thumbnailElement || null;
    this._planNodes = new DataSet([]);
    this._planEdges = new DataSet([]);
    this._network = null;
    this._removeWheelZoom = null;
    this._pollInterval = null;
    this._eventStream = null;
    this._didInitialFit = false;
    this._structureKey = null;
    this._subgraphs = [];
    this._currentIndex = 0;
    this._hierarchicalMode = true;
    this._latestGraphData = null;
    this._renderLayoutKey = null;
    this._nodeLayoutKey = null;
    this._nodeVisualKeys = new Map();
    this._graphSnapshot = null;
    this._appearanceRedrawQueued = false;
    this._handleAppearanceChange = () => {
      if (this._appearanceRedrawQueued) return;
      this._appearanceRedrawQueued = true;
      queueMicrotask(() => {
        this._appearanceRedrawQueued = false;
        this._network?.redraw();
      });
    };
    this._init();
  }

  _init() {
    if (!this._container) return;
    const options = {
      layout: {
        hierarchical: { ...PLAN_GRAPH_DEFAULT_LAYOUT },
      },
      physics: { enabled: false },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        color: { color: "#4B5563", highlight: "#9CA3AF" },
        width: 1.5,
        smooth: { type: "cubicBezier", forceDirection: "horizontal" },
      },
      nodes: {
        shape: "box",
        borderWidth: 2,
        borderWidthSelected: 3,
        font: { size: 14, face: "Manrope, sans-serif" },
        margin: { top: 8, bottom: 8, left: 12, right: 12 },
      },
      interaction: {
        hover: true,
        tooltipDelay: 200,
        dragNodes: true,
        dragView: true,
        // Use the shared distance-based handler instead of vis-network's
        // fixed step per WheelEvent.
        zoomView: false,
      },
    };
    this._network = new Network(
      this._container,
      { nodes: this._planNodes, edges: this._planEdges },
      options
    );
    this._network.on("beforeDrawing", (ctx) => this._drawCanvasGrid(ctx));
    this._removeWheelZoom = installNetworkWheelZoom(this._container, this._network, {
      minScale: PLAN_GRAPH_MIN_SCALE,
      maxScale: PLAN_GRAPH_MAX_SCALE,
    });
    window.addEventListener("matcreator-theme-change", this._handleAppearanceChange);
    window.addEventListener("matcreator-skin-change", this._handleAppearanceChange);
  }

  _computeLevels(nodeIds, rawEdges) {
    const inDeg = Object.fromEntries(nodeIds.map((id) => [id, 0]));
    const adj   = Object.fromEntries(nodeIds.map((id) => [id, []]));
    rawEdges.forEach((e) => {
      const from = Array.isArray(e) ? e[0] : e.from;
      const to   = Array.isArray(e) ? e[1] : e.to;
      if (adj[from]) adj[from].push(to);
      if (to in inDeg) inDeg[to]++;
    });
    const levels = {};
    const queue = nodeIds.filter((id) => inDeg[id] === 0);
    queue.forEach((id) => { levels[id] = 0; });
    while (queue.length) {
      const curr = queue.shift();
      (adj[curr] || []).forEach((nxt) => {
        levels[nxt] = Math.max(levels[nxt] ?? 0, (levels[curr] ?? 0) + 1);
        if (--inDeg[nxt] === 0) queue.push(nxt);
      });
    }
    nodeIds.forEach((id) => { if (!(id in levels)) levels[id] = 0; });
    return levels;
  }

  _breakLongToken(token, maxChars) {
    const chunks = [];
    for (let i = 0; i < token.length; i += maxChars) {
      chunks.push(token.slice(i, i + maxChars));
    }
    return chunks;
  }

  _wrapLabel(text, maxChars = 24, maxLines = 5) {
    const source = String(text || "").trim();
    if (!source) return "";
    const words = source
      .replace(/[_/.-]+/g, (match) => `${match} `)
      .split(/\s+/)
      .filter(Boolean)
      .flatMap((word) => word.length > maxChars ? this._breakLongToken(word, maxChars) : [word]);
    const lines = [];
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (next.length <= maxChars) {
        current = next;
        continue;
      }
      if (current) lines.push(current);
      current = word;
      if (lines.length >= maxLines) break;
    }
    if (current && lines.length < maxLines) lines.push(current);
    if (lines.length === maxLines && words.join(" ").length > lines.join(" ").length) {
      lines[maxLines - 1] = `${lines[maxLines - 1].replace(/\s*.{0,2}$/, "")}...`;
    }
    return lines.join("\n");
  }

  _labelMetrics(nodeEntries) {
    const labels = nodeEntries.map(([id, node]) => this._wrapLabel(node.label || id));
    const lineCounts = labels.map((label) => Math.max(1, label.split("\n").length));
    const longestLines = labels.map((label) => Math.max(...label.split("\n").map((line) => line.length), 1));
    const maxLines = Math.max(...lineCounts, 1);
    const maxLineChars = Math.max(...longestLines, 1);
    const estimatedWidth = Math.min(260, Math.max(120, maxLineChars * 8 + 32));
    const estimatedHeight = Math.max(42, maxLines * 18 + 22);
    return {
      labels,
      maxLines,
      maxLineChars,
      estimatedWidth,
      estimatedHeight,
      nodeSpacing: Math.round(clamp(estimatedHeight + 36, 95, 175)),
      levelSeparation: Math.round(clamp(estimatedWidth + 68, 180, 300)),
      gridGapX: Math.round(clamp(estimatedWidth + 58, 180, 300)),
      gridGapY: Math.round(clamp(estimatedHeight + 24, 70, 125)),
    };
  }

  _drawCanvasGrid(ctx) {
    if (!this._network || !this._container) return;
    const width = this._container.clientWidth || 0;
    const height = this._container.clientHeight || 0;
    if (width <= 0 || height <= 0) return;

    const topLeft = this._network.DOMtoCanvas({ x: 0, y: 0 });
    const bottomRight = this._network.DOMtoCanvas({ x: width, y: height });
    const scale = this._network.getScale() || 1;
    const spacing = 72;
    const startX = Math.floor(topLeft.x / spacing) * spacing;
    const endX = Math.ceil(bottomRight.x / spacing) * spacing;
    const startY = Math.floor(topLeft.y / spacing) * spacing;
    const endY = Math.ceil(bottomRight.y / spacing) * spacing;
    const isLight = document.body.dataset.theme === "light";

    ctx.save();
    ctx.strokeStyle = isLight ? "rgba(19, 32, 51, 0.12)" : "rgba(148, 163, 184, 0.16)";
    ctx.lineWidth = 1 / scale;
    ctx.setLineDash([6 / scale, 7 / scale]);
    for (let x = startX; x <= endX; x += spacing) {
      ctx.beginPath();
      ctx.moveTo(x, startY);
      ctx.lineTo(x, endY);
      ctx.stroke();
    }
    for (let y = startY; y <= endY; y += spacing) {
      ctx.beginPath();
      ctx.moveTo(startX, y);
      ctx.lineTo(endX, y);
      ctx.stroke();
    }
    ctx.restore();
  }

  _visNode(nodeId, node, level) {
    const status = node.status || "pending";
    const isRunning = status === "running";
    const colors = PLAN_NODE_STATUS_COLORS[status] || PLAN_NODE_STATUS_COLORS.pending;
    return {
      id: nodeId,
      label: this._wrapLabel(node.label || nodeId),
      title: node.action || nodeId,
      level,
      widthConstraint: { minimum: 110, maximum: 280 },
      color: {
        background: colors.bg,
        border: colors.border,
        highlight: { background: colors.border, border: colors.border },
      },
      font: { color: colors.font, size: 14, face: "Manrope, sans-serif" },
      shapeProperties: isRunning ? { borderDashes: [4, 3] } : {},
      borderWidth: isRunning ? 2.5 : 2,
    };
  }

  _extractConnectedSubgraphs(graphData) {
    const nodes = graphData.nodes || {};
    const rawEdges = graphData.edges || [];
    const nodeIds = Object.keys(nodes);
    if (nodeIds.length === 0) return [graphData];

    // No edges at all → treat as one graph
    if (rawEdges.length === 0) return [graphData];

    const adj = {};
    nodeIds.forEach((id) => { adj[id] = []; });
    rawEdges.forEach((e) => {
      const from = Array.isArray(e) ? e[0] : e.from;
      const to = Array.isArray(e) ? e[1] : e.to;
      if (adj[from]) adj[from].push(to);
      if (adj[to]) adj[to].push(from);
    });

    const visited = new Set();
    const components = [];
    for (const id of nodeIds) {
      if (visited.has(id)) continue;
      const compIds = new Set();
      const queue = [id];
      visited.add(id);
      while (queue.length) {
        const cur = queue.shift();
        compIds.add(cur);
        for (const nb of (adj[cur] || [])) {
          if (!visited.has(nb)) {
            visited.add(nb);
            queue.push(nb);
          }
        }
      }
      const compNodes = {};
      const compEdges = [];
      for (const cid of compIds) {
        if (nodes[cid]) compNodes[cid] = nodes[cid];
      }
      rawEdges.forEach((e) => {
        const from = Array.isArray(e) ? e[0] : e.from;
        const to = Array.isArray(e) ? e[1] : e.to;
        if (compIds.has(from) || compIds.has(to)) {
          compEdges.push(e);
        }
      });
      components.push({ nodes: compNodes, edges: compEdges });
    }

    // Merge all isolated (single-node) components into one group
    const multi = components.filter((c) => Object.keys(c.nodes).length > 1);
    const singles = components.filter((c) => Object.keys(c.nodes).length === 1);
    if (singles.length > 0) {
      const mergedNodes = {};
      singles.forEach((c) => Object.assign(mergedNodes, c.nodes));
      multi.push({ nodes: mergedNodes, edges: [] });
    }

    return multi.length > 0 ? multi : [graphData];
  }

  _isHistoricalSubgraph(subgraph) {
    const terminalStatuses = new Set(["success", "failed", "blocked", "cancelled"]);
    const nodes = Object.values(subgraph?.nodes || {});
    return nodes.length > 0 && nodes.every((node) => terminalStatuses.has(node.status));
  }

  _subgraphPriority(subgraph) {
    const nodes = Object.values(subgraph?.nodes || {});
    const statuses = new Set(nodes.map((node) => node.status || "pending"));
    const activityScore = statuses.has("running")
      ? 1000
      : statuses.has("pending")
        ? 800
        : statuses.has("waiting")
          ? 600
          : 0;
    const connectedScore = Math.min((subgraph?.edges || []).length, 20) * 10;
    return activityScore + connectedScore + Math.min(nodes.length, 20);
  }

  _primarySubgraph() {
    return this._subgraphs[0] || this._latestGraphData;
  }

  update(incomingGraphData) {
    if (!incomingGraphData || typeof incomingGraphData.nodes !== "object") return;
    const patch = applyGraphUpdate(this._graphSnapshot, incomingGraphData);
    const graphData = patch.graph;
    this._graphSnapshot = graphData;
    if (patch.isDelta && !patch.layoutChanged && this._subgraphs.length) {
      this._latestGraphData = graphData;
      for (const id of patch.changedNodeIds) {
        for (const subgraph of this._subgraphs) {
          if (subgraph.nodes[id]) subgraph.nodes[id] = graphData.nodes[id];
        }
      }
      this._nodeVisualKeys = new Map(this._nodeVisualKeys);
      for (const id of patch.changedNodeIds) {
        const node = graphData.nodes[id];
        if (node) this._nodeVisualKeys.set(id, JSON.stringify([node.status, node.label, node.action]));
        else this._nodeVisualKeys.delete(id);
      }
      this._updateThumbnailStatuses(this._primarySubgraph(), patch.changedNodeIds);
      this._updateCurrentNodeVisuals(patch.changedNodeIds);
      this._updateNavUI();
      return;
    }
    const nodeEntries = Object.entries(graphData.nodes);
    if (nodeEntries.length === 0) return;
    this._latestGraphData = graphData;

    const rawEdges = graphData.edges || [];
    const nodeIds = nodeEntries.map(([id]) => id);

    // Detect structural changes
    const structureKey = JSON.stringify({ ids: [...nodeIds].sort(), edges: rawEdges });
    const structureChanged = structureKey !== this._structureKey;
    const nodeLayoutKey = JSON.stringify(nodeEntries.map(([id, node]) => [id, node.label || id]));
    const nodeLayoutChanged = nodeLayoutKey !== this._nodeLayoutKey;
    const nextVisualKeys = new Map(nodeEntries.map(([id, node]) => [
      id,
      JSON.stringify([node.status, node.label, node.action]),
    ]));
    const changedNodeIds = new Set(nodeIds.filter((id) => this._nodeVisualKeys.get(id) !== nextVisualKeys.get(id)));
    this._nodeLayoutKey = nodeLayoutKey;
    this._nodeVisualKeys = nextVisualKeys;
    if (structureChanged) {
      this._structureKey = structureKey;
      this._subgraphs = this._extractConnectedSubgraphs(graphData)
        .sort((a, b) => this._subgraphPriority(b) - this._subgraphPriority(a));
      this._currentIndex = 0;
    } else {
      // The graph structure can stay the same while execution status changes.
      // Refresh the node objects so _visNode() recomputes colors and styling.
      this._subgraphs.forEach((subgraph) => {
        Object.keys(subgraph.nodes).forEach((id) => {
          if (graphData.nodes[id]) subgraph.nodes[id] = graphData.nodes[id];
        });
      });
    }

    if (this._subgraphs.length === 0) return;
    // The compact preview represents the current/primary roadmap only. Older
    // disconnected nodes remain navigable in the full popup without turning
    // the thumbnail into a collection of unrelated fragments.
    if (!structureChanged && !nodeLayoutChanged) {
      this._updateThumbnailStatuses(this._primarySubgraph(), changedNodeIds);
      this._updateCurrentNodeVisuals(changedNodeIds);
      this._updateNavUI();
      return;
    }
    this._renderThumbnail(this._primarySubgraph());
    this._renderCurrentSubgraph(structureChanged || nodeLayoutChanged);
    this._updateNavUI();
  }

  _updateCurrentNodeVisuals(changedNodeIds) {
    const subgraph = this._subgraphs[this._currentIndex];
    if (!subgraph || !changedNodeIds.size) return;
    const updates = [];
    for (const id of changedNodeIds) {
      const node = subgraph.nodes[id];
      if (!node || !this._planNodes.get(id)) continue;
      const current = this._planNodes.get(id);
      updates.push(this._visNode(id, node, current.level ?? 0));
    }
    if (updates.length) this._planNodes.update(updates);
  }

  _updateThumbnailStatuses(graphData, changedNodeIds) {
    if (!this._thumbnailElement || !changedNodeIds.size) return;
    for (const dot of this._thumbnailElement.querySelectorAll("[data-plan-node-id]")) {
      const id = dot.dataset.planNodeId;
      if (!changedNodeIds.has(id)) continue;
      const status = graphData?.nodes?.[id]?.status || "pending";
      dot.style.background = (PLAN_NODE_STATUS_COLORS[status] || PLAN_NODE_STATUS_COLORS.pending).bg;
    }
  }

  _syncPlanData(visNodes, visEdges, replaceAll = false) {
    if (replaceAll) {
      this._planNodes.clear();
      this._planEdges.clear();
      this._planNodes.add(visNodes);
      this._planEdges.add(visEdges);
      return;
    }

    const nextNodeIds = new Set(visNodes.map((node) => node.id));
    const nextEdgeIds = new Set(visEdges.map((edge) => edge.id));
    const staleNodeIds = this._planNodes.getIds().filter((id) => !nextNodeIds.has(id));
    const staleEdgeIds = this._planEdges.getIds().filter((id) => !nextEdgeIds.has(id));
    if (staleNodeIds.length) this._planNodes.remove(staleNodeIds);
    if (staleEdgeIds.length) this._planEdges.remove(staleEdgeIds);
    this._planNodes.update(visNodes);
    this._planEdges.update(visEdges);
  }

  _renderCurrentSubgraph(structureChanged = false) {
    const sub = this._subgraphs[this._currentIndex];
    if (!sub) return;

    const nodeEntries = Object.entries(sub.nodes);
    const rawEdges = sub.edges || [];
    const nodeIds = nodeEntries.map(([id]) => id);
    const levels = this._computeLevels(nodeIds, rawEdges);
    const maxLevel = Math.max(...Object.values(levels), 0);
    const noHierarchy = maxLevel === 0 && nodeIds.length > 1;
    const metrics = this._labelMetrics(nodeEntries);

    let visNodes;
    let layoutKey;
    if (noHierarchy) {
      layoutKey = JSON.stringify({
        mode: "grid",
        gapX: metrics.gridGapX,
        gapY: metrics.gridGapY,
        count: nodeIds.length,
      });
      if (this._renderLayoutKey !== layoutKey) {
        this._network.setOptions({ layout: { hierarchical: false, randomSeed: 42 } });
      }
      this._hierarchicalMode = false;
      const cols = Math.ceil(Math.sqrt(nodeIds.length));
      const gapX = metrics.gridGapX;
      const gapY = metrics.gridGapY;
      visNodes = nodeEntries.map(([id, n], i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        return {
          ...this._visNode(id, n, 0),
          x: col * gapX,
          y: row * gapY,
          fixed: { x: true, y: true },
        };
      });
    } else {
      layoutKey = JSON.stringify({
        mode: "hierarchical",
        nodeSpacing: metrics.nodeSpacing,
        levelSeparation: metrics.levelSeparation,
      });
      if (this._renderLayoutKey !== layoutKey) {
        this._network.setOptions({
          layout: {
            hierarchical: {
              ...PLAN_GRAPH_DEFAULT_LAYOUT,
              nodeSpacing: metrics.nodeSpacing,
              levelSeparation: metrics.levelSeparation,
            },
          },
        });
      }
      this._hierarchicalMode = true;
      visNodes = nodeEntries.map(([id, n]) => this._visNode(id, n, levels[id] ?? 0));
    }

    const visEdges = rawEdges.map((e) => {
      const from = Array.isArray(e) ? e[0] : e.from;
      const to = Array.isArray(e) ? e[1] : e.to;
      return {
        id: `e__${from}__${to}`,
        from,
        to,
        physics: false,
        hidden: false,
        smooth: { type: "cubicBezier", forceDirection: "horizontal" },
      };
    });

    let savedCamera = null;
    let savedPositions = null;
    if (this._didInitialFit) {
      try {
        savedCamera = {
          position: this._network.getViewPosition(),
          scale: this._network.getScale(),
        };
        savedPositions = this._network.getPositions(nodeIds);
      } catch (_) {}
    }

    const replaceAll = !this._didInitialFit || structureChanged || this._renderLayoutKey !== layoutKey;
    this._syncPlanData(visNodes, visEdges, replaceAll);
    this._renderLayoutKey = layoutKey;

    if (!this._didInitialFit) {
      this._network.fit({ animation: { duration: 300, easingFunction: "easeInOutQuad" } });
      this._didInitialFit = true;
    } else {
      if (savedCamera) {
        this._network.moveTo({
          position: savedCamera.position,
          scale: savedCamera.scale,
          animation: false,
        });
      }
      if (savedPositions) {
        requestAnimationFrame(() => {
          Object.entries(savedPositions).forEach(([id, pos]) => {
            if (nodeIds.includes(id) && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
              this._network.moveNode(id, pos.x, pos.y);
            }
          });
        });
      }
    }
  }

  _updateNavUI() {
    const counter = document.getElementById("plan-graph-counter");
    const prevBtn = document.getElementById("plan-graph-prev");
    const nextBtn = document.getElementById("plan-graph-next");
    if (this._subgraphs.length <= 1) {
      if (counter) counter.textContent = "";
      if (prevBtn) prevBtn.style.display = "none";
      if (nextBtn) nextBtn.style.display = "none";
    } else {
      const current = this._subgraphs[this._currentIndex];
      const sectionLabel = this._isHistoricalSubgraph(current) ? "Roadmap history" : "Roadmap";
      if (counter) counter.textContent = `${sectionLabel} ${this._currentIndex + 1} / ${this._subgraphs.length}`;
      if (prevBtn) { prevBtn.style.display = ""; prevBtn.disabled = this._currentIndex === 0; }
      if (nextBtn) { nextBtn.style.display = ""; nextBtn.disabled = this._currentIndex >= this._subgraphs.length - 1; }
    }
  }

  goPrev() {
    if (this._currentIndex > 0) {
      this._currentIndex--;
      this._didInitialFit = false;
      this._renderCurrentSubgraph();
      this._updateNavUI();
    }
  }

  goNext() {
    if (this._currentIndex < this._subgraphs.length - 1) {
      this._currentIndex++;
      this._didInitialFit = false;
      this._renderCurrentSubgraph();
      this._updateNavUI();
    }
  }

  startPolling(sessionId) {
    this.stopPolling();
    this._currentSessionId = sessionId;
    void this._poll(sessionId);
    const eventStream = new EventSource(`/api/execution-graph/${encodeURIComponent(sessionId)}/events`);
    this._eventStream = eventStream;
    eventStream.onmessage = (event) => {
      try {
        if (sessionId !== this._currentSessionId) return;
        this.update(JSON.parse(event.data));
      } catch (_) {
        // Ignore a malformed snapshot; EventSource will deliver the next one.
      }
    };
    // Use polling only when connected to a deployment that predates the SSE
    // endpoint. Keeping the normal update path event-driven avoids stale
    // roadmap node statuses while a step is running.
    eventStream.onerror = () => {
      if (this._eventStream !== eventStream) return;
      eventStream.close();
      this._eventStream = null;
      if (!this._pollInterval) this._pollInterval = setInterval(() => this._poll(sessionId), 2000);
    };
  }

  refresh(sessionId) {
    this._currentSessionId = sessionId;
    return this._poll(sessionId);
  }

  stopPolling() {
    this._eventStream?.close();
    this._eventStream = null;
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
  }

  async _poll(sessionId) {
    try {
      const data = await httpClient.getJson(`/api/execution-graph/${encodeURIComponent(sessionId)}`);
      if (data === null) return;
      if (sessionId !== this._currentSessionId) return;
      this.update(data);
    } catch (_) {}
  }

  reset() {
    this._currentSessionId = null;
    this._hierarchicalMode = true;
    this._network?.setOptions({
      layout: {
        hierarchical: { ...PLAN_GRAPH_DEFAULT_LAYOUT },
      },
    });
    this._planNodes.clear();
    this._planEdges.clear();
    this._didInitialFit = false;
    this._structureKey = null;
    this._renderLayoutKey = null;
    this._nodeLayoutKey = null;
    this._nodeVisualKeys.clear();
    this._graphSnapshot = null;
    this._subgraphs = [];
    this._currentIndex = 0;
    this._latestGraphData = null;
    this._renderThumbnail(null);
    this._updateNavUI();
    this.stopPolling();
  }

  _renderThumbnail(graphData) {
    const button = this._toggleButton;
    const thumb = this._thumbnailElement;
    if (!button || !thumb) return;
    thumb.innerHTML = "";
    const nodes = graphData?.nodes && typeof graphData.nodes === "object"
      ? Object.entries(graphData.nodes)
      : [];
    if (!nodes.length) {
      button.classList.add("hidden");
      button.setAttribute("aria-pressed", "false");
      return;
    }

    button.classList.remove("hidden");
    const edges = graphData.edges || [];
    const nodeIds = nodes.map(([id]) => id);
    const levels = this._computeLevels(nodeIds, edges);
    const maxLevel = Math.max(...Object.values(levels), 0);
    const columns = maxLevel > 0 ? maxLevel + 1 : Math.ceil(Math.sqrt(nodes.length));
    const buckets = Array.from({ length: columns }, () => []);
    nodes.forEach(([id, node], index) => {
      const col = maxLevel > 0 ? levels[id] ?? 0 : index % columns;
      buckets[Math.min(col, columns - 1)].push([id, node, index]);
    });
    const maxRows = Math.max(...buckets.map((bucket) => bucket.length), 1);
    const positions = new Map();

    nodes.slice(0, 36).forEach(([id], index) => {
      const col = maxLevel > 0 ? levels[id] ?? 0 : index % columns;
      const row = maxLevel > 0
        ? buckets[Math.min(col, columns - 1)].findIndex(([bucketId]) => bucketId === id)
        : Math.floor(index / columns);
      positions.set(id, {
        x: 8 + ((Math.min(col, columns - 1) + 0.5) / columns) * 84,
        y: 10 + ((Math.max(row, 0) + 0.5) / maxRows) * 80,
      });
    });

    const svgNamespace = "http://www.w3.org/2000/svg";
    const connections = document.createElementNS(svgNamespace, "svg");
    connections.classList.add("plan-graph-thumbnail-connections");
    connections.setAttribute("viewBox", "0 0 100 100");
    connections.setAttribute("preserveAspectRatio", "none");
    edges.forEach((edge) => {
      const from = Array.isArray(edge) ? edge[0] : edge.from;
      const to = Array.isArray(edge) ? edge[1] : edge.to;
      const start = positions.get(from);
      const end = positions.get(to);
      if (!start || !end) return;
      const line = document.createElementNS(svgNamespace, "line");
      line.setAttribute("x1", start.x);
      line.setAttribute("y1", start.y);
      line.setAttribute("x2", end.x);
      line.setAttribute("y2", end.y);
      connections.appendChild(line);
    });
    thumb.appendChild(connections);

    nodes.slice(0, 36).forEach(([id, node]) => {
      const position = positions.get(id);
      const colors = PLAN_NODE_STATUS_COLORS[node.status || "pending"] || PLAN_NODE_STATUS_COLORS.pending;
      const dot = document.createElement("span");
      dot.className = "plan-graph-thumbnail-node";
      dot.dataset.planNodeId = id;
      dot.style.left = `${position.x}%`;
      dot.style.top = `${position.y}%`;
      dot.style.background = colors.bg;
      thumb.appendChild(dot);
    });
  }

  notifyLayoutChanged() {
    this._network?.redraw();
    if (!this._didInitialFit) {
      this._network?.fit({ animation: false });
    }
  }

  zoomIn() {
    if (!this._network) return;
    const scale = clamp(this._network.getScale() * 1.3, PLAN_GRAPH_MIN_SCALE, PLAN_GRAPH_MAX_SCALE);
    this._network.moveTo({ scale, animation: { duration: 200, easingFunction: "easeInOutQuad" } });
  }

  zoomOut() {
    if (!this._network) return;
    const scale = clamp(this._network.getScale() / 1.3, PLAN_GRAPH_MIN_SCALE, PLAN_GRAPH_MAX_SCALE);
    this._network.moveTo({ scale, animation: { duration: 200, easingFunction: "easeInOutQuad" } });
  }

  fitToView({ animate = true } = {}) {
    if (!this._network) return;
    this._network.fit({
      animation: animate ? { duration: 300, easingFunction: "easeInOutQuad" } : false,
    });
  }

  destroy() {
    window.removeEventListener("matcreator-theme-change", this._handleAppearanceChange);
    window.removeEventListener("matcreator-skin-change", this._handleAppearanceChange);
    this.stopPolling();
    this._removeWheelZoom?.();
    this._removeWheelZoom = null;
    this._network?.destroy();
    this._network = null;
  }
}
