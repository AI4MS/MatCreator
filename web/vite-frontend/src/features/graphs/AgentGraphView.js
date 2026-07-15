import { Network, DataSet } from "vis-network/standalone";

const NODE_COLORS = {
  orchestrator: { bg: "#7C3AED", border: "#6D28D9", font: "#fff" },
  planning:     { bg: "#3B82F6", border: "#2563EB", font: "#fff" },
  execution:    { bg: "#10B981", border: "#059669", font: "#fff" },
  tester:       { bg: "#F59E0B", border: "#D97706", font: "#1a1a1a" },
  step:         { bg: "#374151", border: "#4B5563", font: "#e5e7eb" },
};

const STATUS_COLORS = {
  running:          { bg: "#FBBF24", border: "#F59E0B", font: "#1a1a1a" },
  success:          null,
  failed:           { bg: "#EF4444", border: "#DC2626", font: "#fff" },
  needs_replanning: { bg: "#F97316", border: "#EA580C", font: "#fff" },
  idle:             { bg: "#374151", border: "#4B5563", font: "#9CA3AF" },
};

export class AgentGraphView {
  constructor(containerId, dependencies) {
    this._stepExecutionFeed = dependencies.stepExecutionFeed;
    this._graphViewport = dependencies.graphViewport;
    this._requestStepCancellation = dependencies.requestStepCancellation;
    this._createArtifactListItem = dependencies.createArtifactListItem;
    this._createJsonBlock = dependencies.createJsonBlock;
    this._getStructurePaths = dependencies.getStructurePaths;
    this._createStructureViewButton = dependencies.createStructureViewButton;
    this._syncPanelResizerVisibility = dependencies.syncPanelResizerVisibility;
    this._container = document.getElementById(containerId);
    this._surfaceEl = document.getElementById("graph-surface");
    this._nodes = new DataSet([]);
    this._edges = new DataSet([]);
    this._network = null;
    this._pollInterval = null;
    this._didInitialFit = false;
    this._pendingFit = true;
    this._detailEl = document.getElementById("graph-detail");
    this._detailClose = document.getElementById("graph-detail-close");
    this._detailLabel = document.getElementById("detail-label");
    this._detailStatus = document.getElementById("detail-status");
    this._detailSummary = document.getElementById("detail-summary");
    this._detailArtifacts = document.getElementById("detail-artifacts");
    this._detailTiming = document.getElementById("detail-timing");
    this._detailInput = document.getElementById("detail-input");
    this._detailToolcalls = document.getElementById("detail-toolcalls");
    this._detailToolcallsCount = document.getElementById("detail-toolcalls-count");
    this._detailConversation = document.getElementById("detail-conversation");
    this._nodeData = {};
    this._activeDetailNodeId = null;
    this._init();
  }

  _captureOpenToolCallKeys() {
    const openKeys = new Set();
    this._detailToolcalls?.querySelectorAll("details[data-toolcall-key][open]")?.forEach((el) => {
      openKeys.add(el.getAttribute("data-toolcall-key"));
    });
    return openKeys;
  }

  _restoreOpenToolCallKeys(openKeys) {
    if (!openKeys?.size) return;
    this._detailToolcalls?.querySelectorAll("details[data-toolcall-key]")?.forEach((el) => {
      el.open = openKeys.has(el.getAttribute("data-toolcall-key"));
    });
  }

  _init() {
    const options = {
      layout: {
        hierarchical: {
          direction: "UD",
          sortMethod: "directed",
          nodeSpacing: 76,
          levelSeparation: 86,
          blockShifting: true,
          edgeMinimization: true,
        },
      },
      physics: { enabled: false },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.72 } },
        color: { color: "#4B5563", highlight: "#9CA3AF" },
        width: 2.4,
        smooth: { type: "cubicBezier", forceDirection: "vertical" },
      },
      nodes: {
        shape: "custom",
        borderWidth: 2,
        borderWidthSelected: 3,
      },
      interaction: {
        hover: true,
        tooltipDelay: 200,
        dragNodes: true,
        dragView: true,
        zoomView: true,
      },
    };

    this._network = new Network(
      this._container,
      { nodes: this._nodes, edges: this._edges },
      options
    );

    this._network.on("selectNode", (params) => {
      if (params.nodes.length) this._showDetail(params.nodes[0]);
    });
    this._network.on("deselectNode", () => this._hideDetail());
    this._detailClose?.addEventListener("click", () => {
      this._network.unselectAll();
      this._hideDetail();
    });
  }

  _nodeTooltip(raw) {
    const lines = [
      raw.label || raw.id,
      `Status: ${raw.status || "unknown"}`,
      `Type: ${raw.type || "step"}`,
    ];
    if (raw.summary) lines.push(`Summary: ${raw.summary}`);
    if (raw.start_time) {
      if (raw.end_time) {
        const secs = ((new Date(raw.end_time) - new Date(raw.start_time)) / 1000).toFixed(1);
        lines.push(`Duration: ${secs}s`);
      } else {
        lines.push("Duration: running");
      }
    }
    return lines.join("\n");
  }

  _nodeBadge(raw) {
    const stepNumber = raw.input && raw.input.step_number;
    if (raw.type === "step" && stepNumber !== undefined && stepNumber !== null) {
      return String(stepNumber).slice(0, 2);
    }
    const typeInitials = {
      orchestrator: "O",
      planning: "P",
      execution: "E",
      tester: "T",
    };
    if (typeInitials[raw.type]) return typeInitials[raw.type];
    return String(raw.label || raw.id || "?").trim().charAt(0).toUpperCase() || "?";
  }

  _nodeRadius(raw) {
    if (raw.type === "orchestrator") return 17;
    if (raw.type === "planning") return 15;
    return 13;
  }

  _nodeRenderer(raw, colors, badge, radius, isRunning) {
    return ({ ctx, x, y, state }) => {
      const selected = Boolean(state?.selected);
      const hover = Boolean(state?.hover);
      const drawRadius = radius + (selected ? 2 : hover ? 1 : 0);
      const borderWidth = isRunning ? 2.5 : selected ? 3 : 2;

      return {
        drawNode: () => {
          ctx.save();
          ctx.beginPath();
          ctx.arc(x, y, drawRadius, 0, Math.PI * 2);
          ctx.fillStyle = hover || selected ? colors.border : colors.bg;
          ctx.fill();
          ctx.lineWidth = borderWidth;
          ctx.strokeStyle = colors.border;
          if (isRunning) ctx.setLineDash([4, 3]);
          ctx.stroke();
          ctx.setLineDash([]);

          ctx.fillStyle = colors.font;
          ctx.font = `800 ${badge.length > 1 ? 10.5 : 12}px Manrope, system-ui, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          const metrics = ctx.measureText(badge);
          const opticalOffset = metrics.actualBoundingBoxLeft !== undefined
            ? (metrics.actualBoundingBoxLeft - metrics.actualBoundingBoxRight) / 2
            : 0;
          ctx.fillText(badge, x + opticalOffset, y);
          ctx.restore();
        },
        nodeDimensions: {
          width: (drawRadius + borderWidth) * 2,
          height: (drawRadius + borderWidth) * 2,
        },
      };
    };
  }

  _visNode(raw) {
    const typeColors = NODE_COLORS[raw.type] || NODE_COLORS.step;
    const statusOverride = STATUS_COLORS[raw.status];
    const colors = statusOverride || typeColors;
    const isRunning = raw.status === "running";
    const badge = this._nodeBadge(raw);
    const radius = this._nodeRadius(raw);
    return {
      id: raw.id,
      label: "",
      shape: "custom",
      color: {
        background: colors.bg,
        border: colors.border,
        highlight: { background: colors.border, border: colors.border },
      },
      ctxRenderer: this._nodeRenderer(raw, colors, badge, radius, isRunning),
      title: this._nodeTooltip(raw),
    };
  }

  _computeLevels(rawNodes, edges) {
    const nodeMap = Object.fromEntries(rawNodes.map(n => [n.id, n]));
    const children = {};
    const hasParent = new Set();
    edges.forEach(e => {
      (children[e.from] = children[e.from] || []).push(e.to);
      hasParent.add(e.to);
    });
    const roots = rawNodes.map(n => n.id).filter(id => !hasParent.has(id));
    const levels = {};

    // Recursively assign levels. Among siblings, sort by start_time and
    // increment the level each time a child starts after the previous group ends
    // (sequential). Children whose time windows overlap stay at the same level
    // (parallel).
    function assign(id, minLevel) {
      if (levels[id] !== undefined && levels[id] >= minLevel) return;
      levels[id] = minLevel;

      const currentType = nodeMap[id]?.type;
      if (currentType === "orchestrator") {
        (children[id] || []).forEach((kid) => assign(kid, 1));
        return;
      }
      if (currentType === "planning") {
        (children[id] || []).forEach((kid) => assign(kid, 2));
        return;
      }

      const kids = (children[id] || []).slice().sort((a, b) => {
        const ta = nodeMap[a]?.start_time ? new Date(nodeMap[a].start_time).getTime() : Infinity;
        const tb = nodeMap[b]?.start_time ? new Date(nodeMap[b].start_time).getTime() : Infinity;
        return ta - tb;
      });

      let nextLevel = minLevel + 1;
      let groupEndTime = null; // latest end_time seen in the current parallel group

      for (const kid of kids) {
        const kidStart = nodeMap[kid]?.start_time ? new Date(nodeMap[kid].start_time).getTime() : null;
        const kidEnd   = nodeMap[kid]?.end_time   ? new Date(nodeMap[kid].end_time).getTime()   : null;

        if (groupEndTime !== null && kidStart !== null && kidStart >= groupEndTime) {
          // This child starts after the previous group ended — sequential, new level
          nextLevel++;
          groupEndTime = kidEnd;
        } else {
          // Concurrent with previous group (or no timing info) — extend group window
          if (groupEndTime === null || (kidEnd !== null && kidEnd > groupEndTime)) {
            groupEndTime = kidEnd;
          }
        }

        assign(kid, nextLevel);
      }
    }

    roots.forEach(r => assign(r, 0));
    return levels;
  }

  _buildDisplayEdges(rawNodes, edges) {
    const nodeMap = Object.fromEntries(rawNodes.map((n) => [n.id, n]));
    const phaseTypes = new Set(["planning", "execution", "tester"]);
    const displayEdges = [];
    const phaseNodes = rawNodes
      .filter((n) => n.parent_id === "orchestrator" && phaseTypes.has(n.type))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });

    const planningNodes = phaseNodes.filter((n) => n.type === "planning");
    const childPhaseNodes = phaseNodes.filter((n) => n.type !== "planning");

    planningNodes.forEach((planning) => {
      displayEdges.push({
        id: `phase__orchestrator__${planning.id}`,
        from: "orchestrator",
        to: planning.id,
      });
    });

    childPhaseNodes.forEach((node) => {
      let parentPlanning = null;
      const nodeStart = node.start_time ? new Date(node.start_time).getTime() : Infinity;

      for (const planning of planningNodes) {
        const planningStart = planning.start_time ? new Date(planning.start_time).getTime() : -Infinity;
        if (planningStart <= nodeStart) {
          parentPlanning = planning;
        } else {
          break;
        }
      }

      displayEdges.push({
        id: `phase__${(parentPlanning || { id: "orchestrator" }).id}__${node.id}`,
        from: parentPlanning ? parentPlanning.id : "orchestrator",
        to: node.id,
      });
    });

    (edges || []).forEach((edge) => {
      const fromNode = nodeMap[edge.from];
      const toNode = nodeMap[edge.to];
      if (!fromNode || !toNode) return;

      const isTopLevelPhaseEdge =
        edge.from === "orchestrator" &&
        toNode.parent_id === "orchestrator" &&
        phaseTypes.has(toNode.type);

      if (isTopLevelPhaseEdge) return;

      displayEdges.push({
        id: edge.id || `${edge.from}__${edge.to}`,
        from: edge.from,
        to: edge.to,
      });
    });

    return displayEdges;
  }

  _resizeSurface() {
    if (!this._surfaceEl || !this._graphViewport) return;

    // Match the canvas to the visible viewport exactly; larger off-screen
    // surfaces make fit() center against hidden space instead of the panel.
    const targetWidth = Math.max(1, Math.round(this._graphViewport.clientWidth || 1));
    const targetHeight = Math.max(1, Math.round(this._graphViewport.clientHeight || 1));
    this._surfaceEl.style.width = `${targetWidth}px`;
    this._surfaceEl.style.height = `${targetHeight}px`;
  }

  _fitGraph() {
    if (!this._network || this._nodes.length === 0) return;
    requestAnimationFrame(() => {
      if (!this._network || this._nodes.length === 0) return;
      this._network.redraw();
      this._network.fit({ animation: { duration: 300, easingFunction: "easeInOutQuad" } });
      this._didInitialFit = true;
      this._pendingFit = false;
    });
  }

  update(graphData) {
    if (!graphData || typeof graphData.nodes !== "object") return;

    const prevNodeIds = new Set(this._nodes.getIds());
    const prevEdgeIds = new Set(this._edges.getIds());
    const rawNodes = Object.values(graphData.nodes);
    this._nodeData = graphData.nodes;
    this._stepExecutionFeed.update(graphData);
    const displayEdges = this._buildDisplayEdges(rawNodes, graphData.edges || []);
    const levels = this._computeLevels(rawNodes, displayEdges);
    this._resizeSurface(levels);
    const nextNodeIds = new Set(rawNodes.map((raw) => raw.id));
    const nextEdgeIds = new Set(displayEdges.map((e) => e.id || `${e.from}__${e.to}`));
    const topologyChanged =
      prevNodeIds.size !== nextNodeIds.size ||
      prevEdgeIds.size !== nextEdgeIds.size ||
      [...nextNodeIds].some((id) => !prevNodeIds.has(id)) ||
      [...nextEdgeIds].some((id) => !prevEdgeIds.has(id));

    this._nodes.getIds().forEach((nodeId) => {
      if (!nextNodeIds.has(nodeId)) this._nodes.remove(nodeId);
    });

    rawNodes.forEach((raw) => {
      const vis = this._visNode(raw);
      vis.level = levels[raw.id] ?? 0;
      if (this._nodes.get(raw.id)) {
        this._nodes.update(vis);
      } else {
        this._nodes.add(vis);
      }
    });

    this._edges.getIds().forEach((edgeId) => {
      if (!nextEdgeIds.has(edgeId)) this._edges.remove(edgeId);
    });

    const existingEdgeIds = new Set(this._edges.getIds());
    displayEdges.forEach((e) => {
      const edgeId = e.id || `${e.from}__${e.to}`;
      if (!existingEdgeIds.has(edgeId)) {
        this._edges.add({
          id: edgeId,
          from: e.from,
          to: e.to,
          hidden: false,
          physics: false,
          width: 2.4,
          smooth: { type: "cubicBezier", forceDirection: "vertical" },
        });
      }
    });

    if (rawNodes.length > 0 && (topologyChanged || !this._didInitialFit || this._pendingFit)) {
      this._fitGraph();
    }

    if (this._activeDetailNodeId) {
      if (this._nodeData[this._activeDetailNodeId]) {
        this._showDetail(this._activeDetailNodeId, { preserveScroll: true, scrollToStep: false });
      } else {
        this._hideDetail();
      }
    }
  }

  startPolling(sessionId) {
    this._currentSessionId = sessionId;
    this._poll(sessionId);
    this._pollInterval = setInterval(() => this._poll(sessionId), 2000);
  }

  stopPolling() {
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
  }

  async _poll(sessionId) {
    try {
      const resp = await fetch(`/api/agent-graph/${sessionId}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (sessionId !== this._currentSessionId) return;
      this.update(data);
    } catch (_) {
      // silently ignore network errors during polling
    }
  }

  reset() {
    this._currentSessionId = null;
    this._nodes.clear();
    this._edges.clear();
    this._nodeData = {};
    this._didInitialFit = false;
    this._pendingFit = true;
    this._resizeSurface([], { 0: 1 });
    this._hideDetail();
    this.stopPolling();
  }

  _showDetail(nodeId, options = {}) {
    const raw = this._nodeData[nodeId];
    if (!raw) return;
    this._activeDetailNodeId = nodeId;
    const preserveScroll = Boolean(options.preserveScroll);
    const prevScrollTop = preserveScroll ? this._detailEl.scrollTop : 0;
    const prevOpenToolCallKeys = preserveScroll ? this._captureOpenToolCallKeys() : new Set();
    this._detailLabel.textContent = raw.label;
    this._detailStatus.textContent = raw.status;
    this._detailStatus.className = `badge badge-${raw.status}`;
    this._detailSummary.textContent = raw.summary || "—";

    // Timing
    if (raw.start_time) {
      const start = new Date(raw.start_time);
      if (raw.end_time) {
        const end = new Date(raw.end_time);
        const secs = ((end - start) / 1000).toFixed(1);
        this._detailTiming.textContent = `${secs}s`;
      } else {
        this._detailTiming.textContent = "running…";
      }
    } else {
      this._detailTiming.textContent = "—";
    }

    // Stop-step button (only for running step nodes)
    const actionsRow = document.getElementById("detail-actions-row");
    const stopStepBtn = document.getElementById("detail-stop-step-btn");
    const stepNumber = raw.input && raw.input.step_number;
    if (raw.type === "step" && raw.status === "running" && stepNumber !== undefined && stepNumber !== null) {
      stopStepBtn.disabled = false;
      stopStepBtn.textContent = "Stop step";
      stopStepBtn.onclick = async () => {
        stopStepBtn.disabled = true;
        stopStepBtn.textContent = "Stopping…";
        await this._requestStepCancellation(stepNumber);
      };
      actionsRow.style.display = "";
    } else {
      actionsRow.style.display = "none";
    }
    this._detailArtifacts.innerHTML = "";
    const arts = raw.artifacts || [];
    if (arts.length) {
      arts.forEach((a) => {
        this._detailArtifacts.appendChild(this._createArtifactListItem(a));
      });
    } else {
      const li = document.createElement("li");
      li.textContent = "none";
      this._detailArtifacts.appendChild(li);
    }

    // Input parameters
    if (raw.input && Object.keys(raw.input).length) {
      this._detailInput.textContent = JSON.stringify(raw.input, null, 2);
      document.getElementById("detail-input-row").style.display = "";
    } else {
      document.getElementById("detail-input-row").style.display = "none";
    }

    // Tool calls
    const toolCalls = raw.tool_calls || [];
    this._detailToolcallsCount.textContent = toolCalls.length;
    this._detailToolcalls.innerHTML = "";
    if (toolCalls.length) {
      toolCalls.forEach((tc) => {
        const d = document.createElement("details");
        d.className = "timeline-function-call";
        d.setAttribute("data-toolcall-key", tc.id || `${tc.name}:${tc.start_time || ""}`);
        const dur = tc.start_time && tc.end_time
          ? ` (${((new Date(tc.end_time) - new Date(tc.start_time)) / 1000).toFixed(1)}s)`
          : "";
        const s = document.createElement("summary");
        s.textContent = `🔧 ${tc.name}${dur}`;
        d.appendChild(s);
        if (tc.args_summary) {
          d.appendChild(this._createJsonBlock(tc.args_summary));
        }
        if (tc.result_summary) {
          const pre = this._createJsonBlock(`→ ${tc.result_summary}`);
          pre.style.borderTop = "1px solid rgba(255,255,255,0.06)";
          d.appendChild(pre);
        }
        this._getStructurePaths(tc).forEach((path) => {
          d.appendChild(this._createStructureViewButton(path));
        });
        this._detailToolcalls.appendChild(d);
      });
      document.getElementById("detail-toolcalls-row").style.display = "";
    } else {
      document.getElementById("detail-toolcalls-row").style.display = "none";
    }

    // Conversation transcript is rendered live in the main chat step feed.
    const conversation = raw.type === "step" ? [] : (raw.conversation || []);
    this._detailConversation.innerHTML = "";
    if (conversation.length) {
      conversation.forEach((evt) => {
        const d = document.createElement("details");
        d.className = `timeline-${evt.type}`;
        const s = document.createElement("summary");
        const icon = evt.type === "thought" ? "💭" : evt.type === "text" ? "💬" : evt.type === "function_call" ? "🔧" : "↩";
        s.textContent = `${icon} [${evt.author}] ${evt.type}`;
        d.appendChild(s);
        d.appendChild(this._createJsonBlock(evt.content));
        this._detailConversation.appendChild(d);
      });
      document.getElementById("detail-conversation-row").style.display = "";
    } else {
      document.getElementById("detail-conversation-row").style.display = "none";
    }

    this._detailEl.classList.remove("hidden");
    if (raw.type === "step" && options.scrollToStep !== false) this._stepExecutionFeed.highlight(raw.id);
    this._syncPanelResizerVisibility();
    if (preserveScroll) {
      this._restoreOpenToolCallKeys(prevOpenToolCallKeys);
      this._detailEl.scrollTop = prevScrollTop;
    }
  }

  _hideDetail() {
    this._activeDetailNodeId = null;
    this._detailEl.classList.add("hidden");
    this._syncPanelResizerVisibility();
  }

  notifyLayoutChanged() {
    if (!this._network) return;
    this._network.redraw();
  }
}

// ---------------------------------------------------------------------------
// Step executor feed in the main chat window
// ---------------------------------------------------------------------------

export class StepExecutionFeed {
  constructor(dependencies) {
    this._chatArea = dependencies.chatArea;
    this._isSending = dependencies.isSending;
    this._isChatNearBottom = dependencies.isChatNearBottom;
    this._scrollToBottom = dependencies.scrollToBottom;
    this._createAgentAvatarEl = dependencies.createAgentAvatarEl;
    this._stepFeedTitle = dependencies.stepFeedTitle;
    this._formatStepDuration = dependencies.formatStepDuration;
    this._renderStepInput = dependencies.renderStepInput;
    this._renderStepConversationEvent = dependencies.renderStepConversationEvent;
    this._renderStepToolCall = dependencies.renderStepToolCall;
    this._requestStepCancellation = dependencies.requestStepCancellation;
    this._createArtifactListItem = dependencies.createArtifactListItem;
    this._cards = new Map();
    this._userOpen = new Map();
    this._nestedOpen = new Map();
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._liveToolHostEl = null;
    this._stepById = new Map();
    this._childNodes = new Map();
  }

  reset() {
    this._cards.clear();
    this._userOpen.clear();
    this._nestedOpen.clear();
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._liveToolHostEl = null;
    this._stepById = new Map();
    this._childNodes = new Map();
  }

  startLiveTurn(anchorEl, startedAt = Date.now()) {
    this._liveAnchorEl = anchorEl || null;
    this._liveStartedAt = startedAt;
    this._liveContainerEl = document.createElement("div");
    this._liveContainerEl.className = "step-feed-live-region";
    this._liveContainerEl.dataset.stepLiveRegion = "true";
    this._liveToolHostEl = null;

    if (anchorEl && anchorEl.parentNode === this._chatArea) {
      this._chatArea.insertBefore(this._liveContainerEl, anchorEl.nextSibling);
    } else {
      this._chatArea.appendChild(this._liveContainerEl);
    }

    return this._liveContainerEl;
  }

  attachLiveToolHost(hostEl) {
    if (!hostEl || this._liveToolHostEl === hostEl) return;
    this._liveToolHostEl = hostEl;
    for (const [nodeId, card] of this._cards.entries()) {
      const node = this._stepById.get(nodeId);
      if (node && !this.isRootStep(node)) continue;
      if (card.dataset.stepStartTime !== undefined) {
        hostEl.appendChild(card);
      }
    }
  }

  finishLiveTurn() {
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._liveToolHostEl = null;
  }

  update(graphData) {
    if (!graphData || typeof graphData.nodes !== "object") return;
    const liveContainer = this._activeLiveContainer();
    const steps = Object.values(graphData.nodes)
      .filter((node) => node.type === "step")
      .filter((node) => !liveContainer || this._isLiveStep(node))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });
    this.setHierarchy(steps);
    const rootSteps = steps.filter((node) => this.isRootStep(node));

    const seen = new Set(steps.map((node) => node.id));
    for (const nodeId of this._cards.keys()) {
      if (!seen.has(nodeId)) {
        this._cards.delete(nodeId);
        this._nestedOpen.delete(nodeId);
      }
    }

    const shouldStick = this._isChatNearBottom();
    rootSteps.forEach((node) => this._upsert(node));
    if (shouldStick) this._scrollToBottom();
  }

  setHierarchy(stepNodes) {
    const steps = Array.isArray(stepNodes) ? stepNodes : [];
    this._stepById = new Map(steps.map((node) => [node.id, node]));
    this._childNodes = new Map();

    steps.forEach((node) => {
      if (!this._stepById.has(node.parent_id)) return;
      const children = this._childNodes.get(node.parent_id) || [];
      children.push(node);
      this._childNodes.set(node.parent_id, children);
    });

    for (const children of this._childNodes.values()) {
      children.sort((a, b) => this._stepSortTime(a) - this._stepSortTime(b));
    }
  }

  isRootStep(node) {
    return !this._stepById.has(node?.parent_id);
  }

  _activeLiveContainer() {
    if (this._liveToolHostEl && document.body.contains(this._liveToolHostEl)) {
      return this._liveToolHostEl;
    }
    return this._liveContainerEl && this._liveContainerEl.isConnected
      ? this._liveContainerEl
      : null;
  }

  _isLiveStep(node) {
    if (!this._liveStartedAt) return true;
    if (!node.start_time) return node.status === "running";
    const startedAt = new Date(node.start_time).getTime();
    return Number.isFinite(startedAt) && startedAt >= this._liveStartedAt - 2000;
  }

  highlight(nodeId) {
    this._highlightedId = nodeId;
    for (const [id, card] of this._cards.entries()) {
      card.classList.toggle("step-feed-highlight", id === nodeId);
    }
    const card = this._cards.get(nodeId);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setTimeout(() => card.classList.remove("step-feed-highlight"), 1600);
    }
  }

  _upsert(node) {
    let outer = this._cards.get(node.id);
    const nextSortTime = this._stepSortTime(node);
    if (!outer || !this._chatArea.contains(outer)) {
      outer = this._createCard(node);
      this._cards.set(node.id, outer);
      this._placeCard(outer, node);
    } else if (this._isSending() || outer.dataset.stepStartTime !== String(nextSortTime)) {
      this._placeCard(outer, node);
    }
    this._renderCard(outer, node);
  }

  appendStatic(node, container = this._chatArea) {
    let outer = this._cards.get(node.id);
    if (!outer || !container.contains(outer)) {
      outer = this._createCard(node);
      this._cards.set(node.id, outer);
    }
    outer.dataset.stepStartTime = String(this._stepSortTime(node));
    container.appendChild(outer);
    this._renderCard(outer, node);
    return outer;
  }

  _placeCard(outer, node) {
    outer.classList.remove("step-feed-child-message");
    const liveContainer = this._activeLiveContainer();
    if (liveContainer) {
      this._insertIntoLiveContainer(liveContainer, outer, node);
      return;
    }
    if (this._isSending() && this._liveContainerEl) {
      this._insertIntoLiveContainer(this._liveContainerEl, outer, node);
      return;
    }
    this._insertSorted(outer, node);
  }

  _stepSortTime(node) {
    return node?.start_time ? new Date(node.start_time).getTime() : Infinity;
  }

  _upsertNested(node, container, ancestors) {
    let outer = this._cards.get(node.id);
    if (!outer) {
      outer = this._createCard(node);
      this._cards.set(node.id, outer);
    }
    outer.classList.add("step-feed-child-message");
    this._insertIntoLiveContainer(container, outer, node);
    this._renderCard(outer, node, ancestors);
    return outer;
  }

  _insertIntoLiveContainer(container, outer, node) {
    const newTime = this._stepSortTime(node);
    outer.dataset.stepStartTime = String(newTime);

    for (const el of [...container.children]) {
      if (el === outer) continue;
      if (!el.dataset.stepStartTime) continue;
      if (newTime < Number(el.dataset.stepStartTime)) {
        container.insertBefore(outer, el);
        return;
      }
    }
    container.appendChild(outer);
  }

  _insertSorted(outer, node) {
    const newTime = this._stepSortTime(node);
    outer.dataset.stepStartTime = String(newTime);

    // Walk all chat children to find the right insertion point.
    // - Step cards: compare by start_time, insert before the first later one.
    // - User/agent messages: track as anchor, but reset when a step card is
    //   found after them (so we insert after the most recent step card too).
    const children = [...this._chatArea.children];
    const liveAnchor = this._liveAnchorEl && this._chatArea.contains(this._liveAnchorEl)
      ? this._liveAnchorEl
      : null;
    let insertAfter = liveAnchor; // element to insert after (null = before first child)
    let passedLiveAnchor = !liveAnchor;

    for (const el of children) {
      if (el === outer) continue;
      if (liveAnchor && !passedLiveAnchor) {
        if (el === liveAnchor) passedLiveAnchor = true;
        continue;
      }

      if (el.dataset.stepStartTime) {
        const elTime = Number(el.dataset.stepStartTime);
        if (newTime < elTime) {
          // Found a later step card — insert before it
          if (insertAfter) {
            this._chatArea.insertBefore(outer, insertAfter.nextElementSibling);
          } else {
            this._chatArea.insertBefore(outer, el);
          }
          return;
        }
        // This step card is earlier — update anchor
        insertAfter = el;
      } else if (el.dataset.msgIndex !== undefined) {
        // User/agent message — update anchor
        insertAfter = el;
      } else if (el.classList.contains("user-message")) {
        // Live messages have no msgIndex until the session is reloaded.
        insertAfter = el;
      } else if (
        insertAfter &&
        el.classList.contains("agent-message") &&
        !el.classList.contains("step-feed-message")
      ) {
        this._chatArea.insertBefore(outer, el);
        return;
      }
    }

    // No later step card found — insert after the last tracked element.
    if (insertAfter) {
      this._chatArea.insertBefore(outer, insertAfter.nextElementSibling);
    } else {
      this._chatArea.appendChild(outer);
    }
  }

  _createCard(node) {
    const outer = document.createElement("div");
    outer.className = "message agent-message step-feed-message";
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStartTime = node.start_time ? String(new Date(node.start_time).getTime()) : "";
    outer.appendChild(this._createAgentAvatarEl());

    const bubble = document.createElement("div");
    bubble.className = "message-bubble step-feed-bubble";
    const details = document.createElement("details");
    details.className = "step-feed-details";
    details.addEventListener("toggle", () => {
      this._userOpen.set(node.id, details.open);
    });
    bubble.appendChild(details);
    outer.appendChild(bubble);
    return outer;
  }

  _wireNested(nodeId, key, details) {
    let nodeState = this._nestedOpen.get(nodeId);
    if (!nodeState) {
      nodeState = new Map();
      this._nestedOpen.set(nodeId, nodeState);
    }
    if (nodeState.has(key)) {
      details.open = nodeState.get(key);
    }
    details.dataset.stepNestedKey = key;
    details.addEventListener("toggle", (event) => {
      if (event.target !== details) return;
      nodeState.set(key, details.open);
    });
    return details;
  }

  _renderCard(outer, node, ancestors = new Set([node.id])) {
    outer.dataset.stepNodeId = node.id;
    outer.classList.toggle("step-feed-highlight", this._highlightedId === node.id);

    const bubble = outer.querySelector(".step-feed-bubble");
    bubble?.querySelector(":scope > .step-feed-child-section")?.remove();

    const details = outer.querySelector(".step-feed-details");
    const userChoice = this._userOpen.get(node.id);
    details.open = userChoice === undefined ? node.status === "running" : userChoice;
    details.innerHTML = "";

    const summary = document.createElement("summary");
    summary.className = "step-feed-summary";
    const title = document.createElement("span");
    title.className = "step-feed-title";
    title.textContent = this._stepFeedTitle(node);
    const badge = document.createElement("span");
    badge.className = `badge badge-${node.status || "idle"}`;
    badge.textContent = node.status || "idle";
    const meta = document.createElement("span");
    meta.className = "step-feed-meta";
    meta.textContent = this._formatStepDuration(node);
    summary.append(title, badge, meta);

    const stepNumber = node.input && node.input.step_number;
    if (node.status === "running" && stepNumber !== undefined && stepNumber !== null) {
      const stopBtn = document.createElement("button");
      stopBtn.type = "button";
      stopBtn.className = "step-feed-stop-btn";
      stopBtn.textContent = "Stop";
      stopBtn.title = `Stop step ${stepNumber}`;
      stopBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        stopBtn.disabled = true;
        stopBtn.textContent = "Stopping…";
        await this._requestStepCancellation(stepNumber);
      });
      summary.appendChild(stopBtn);
    }
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "step-feed-body";

    if (node.summary) {
      const p = document.createElement("div");
      p.className = "step-feed-node-summary";
      p.textContent = node.summary;
      body.appendChild(p);
    }

    if (node.input && Object.keys(node.input).length) {
      body.appendChild(this._wireNested(node.id, "input", this._renderStepInput(node.input)));
    }

    const childNodes = (this._childNodes.get(node.id) || [])
      .filter((child) => !ancestors.has(child.id));
    if (childNodes.length) {
      const section = document.createElement("div");
      section.className = "step-feed-section step-feed-child-section";
      const label = document.createElement("div");
      label.className = "step-feed-section-title";
      label.textContent = `Sub-executors (${childNodes.length})`;
      const childHost = document.createElement("div");
      childHost.className = "step-feed-child-list";
      section.append(label, childHost);

      childNodes.forEach((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        this._upsertNested(child, childHost, nextAncestors);
      });

      bubble?.appendChild(section);
    }

    const conversation = node.conversation || [];
    if (conversation.length) {
      const section = document.createElement("div");
      section.className = "step-feed-section";
      const sectionDetails = document.createElement("details");
      sectionDetails.className = "step-feed-section-details";
      const sectionSummary = document.createElement("summary");
      sectionSummary.className = "step-feed-section-title";
      sectionSummary.textContent = `Conversations (${conversation.length})`;
      sectionDetails.appendChild(sectionSummary);
      conversation.forEach((evt, idx) => {
        const key = `conversation:${idx}:${evt.timestamp || ""}:${evt.type || ""}:${evt.author || ""}`;
        sectionDetails.appendChild(this._wireNested(node.id, key, this._renderStepConversationEvent(evt)));
      });
      sectionDetails.addEventListener("toggle", () => {
        sectionSummary.classList.toggle("open", sectionDetails.open);
      });
      section.appendChild(this._wireNested(node.id, "section:conversation", sectionDetails));
      body.appendChild(section);
    }

    const toolCalls = node.tool_calls || [];
    if (toolCalls.length) {
      const section = document.createElement("div");
      section.className = "step-feed-section";
      const sectionDetails = document.createElement("details");
      sectionDetails.className = "step-feed-section-details";
      const sectionSummary = document.createElement("summary");
      sectionSummary.className = "step-feed-section-title";
      sectionSummary.textContent = `Tool calls (${toolCalls.length})`;
      sectionDetails.appendChild(sectionSummary);
      toolCalls.forEach((tc, idx) => {
        const key = `tool:${idx}:${tc.name || ""}:${tc.start_time || ""}`;
        sectionDetails.appendChild(this._wireNested(node.id, key, this._renderStepToolCall(tc)));
      });
      sectionDetails.addEventListener("toggle", () => {
        sectionSummary.classList.toggle("open", sectionDetails.open);
      });
      section.appendChild(this._wireNested(node.id, "section:toolcalls", sectionDetails));
      body.appendChild(section);
    }

    const artifacts = node.artifacts || [];
    if (artifacts.length) {
      const section = document.createElement("div");
      section.className = "step-feed-section";
      const label = document.createElement("div");
      label.className = "step-feed-section-title";
      label.textContent = "Artifacts";
      const list = document.createElement("ul");
      list.className = "detail-artifacts step-feed-artifacts";
      artifacts.forEach((artifact) => {
        list.appendChild(this._createArtifactListItem(artifact));
      });
      section.append(label, list);
      body.appendChild(section);
    }

    if (!body.childElementCount) {
      const empty = document.createElement("div");
      empty.className = "step-feed-empty";
      empty.textContent = "Waiting for step executor events…";
      body.appendChild(empty);
    }

    details.appendChild(body);
  }
}

// ---------------------------------------------------------------------------
// Execution Plan Graph (floating popup in chat column)
// ---------------------------------------------------------------------------

