import { Network, DataSet } from "vis-network/standalone";
import { createDisclosureController } from "../ui/disclosureState.js";
import { installNetworkWheelZoom } from "./networkWheelZoom.js";
import { httpClient } from "../../shared/api/http.js";
import { applyGraphUpdate } from "./graphUpdates.js";

// Node identity and execution state intentionally live in separate visual
// vocabularies. Type owns the face and its letter; state only owns a compact
// badge or the running orbit below.
const NODE_TYPE_VISUALS = {
  orchestrator: {
    fill: "224, 231, 255", border: "129, 140, 248", text: "#1e1b4b",
    dark: { fill: "99, 102, 241", border: "165, 180, 252", text: "#eef2ff" },
  },
  planning: {
    fill: "219, 234, 254", border: "96, 165, 250", text: "#172554",
    dark: { fill: "14, 165, 233", border: "125, 211, 252", text: "#ecfeff" },
  },
  execution: {
    fill: "209, 250, 229", border: "52, 211, 153", text: "#064e3b",
    dark: { fill: "16, 185, 129", border: "110, 231, 183", text: "#ecfdf5" },
  },
  tester: {
    fill: "254, 243, 199", border: "245, 158, 11", text: "#713f12",
    dark: { fill: "217, 119, 6", border: "252, 211, 77", text: "#fffbeb" },
  },
  step: {
    fill: "226, 232, 240", border: "148, 163, 184", text: "#1e293b",
    dark: { fill: "71, 85, 105", border: "148, 163, 184", text: "#f8fafc" },
  },
};

// These names mirror the graph logger / execution-plan lifecycle values.
// Symbols provide a non-colour status cue even at a glance or in grayscale.
const STATUS_VISUALS = {
  running:          { color: "251, 191, 36", edge: "254, 240, 138", symbol: null, label: "Running" },
  success:          { color: "34, 197, 94", symbol: "✓", label: "Completed" },
  failed:           { color: "239, 68, 68", symbol: "!", label: "Failed" },
  needs_replanning: { color: "245, 158, 11", symbol: "?", label: "Needs replanning" },
  blocked:          { color: "245, 158, 11", symbol: "?", label: "Blocked" },
  waiting:          { color: "100, 116, 139", symbol: "…", label: "Waiting" },
  pending:          { color: "100, 116, 139", symbol: "…", label: "Pending" },
  idle:             { color: "100, 116, 139", symbol: "…", label: "Idle" },
  cancelled:        { color: "100, 116, 139", symbol: "×", label: "Cancelled" },
};

const STATUS_TRANSITION_MS = 360;

const rgba = (rgb, alpha) => `rgba(${rgb}, ${alpha})`;

// These distances describe time, rather than graph depth.  In particular an
// execution batch gets its own row even though every execution node still has
// the same planning node as its real parent.
const VINE_BATCH_GAP = 92;
const VINE_BATCH_STAGGER = 54;
// Keep task chains legible vertically while batches themselves still use the
// tighter stagger below.
const VINE_DESCENDANT_GAP = 70;
const VINE_NODE_GAP = 64;
const VINE_PLANNER_GAP = 460;
const VINE_STEM_CLEARANCE = 42;

const STATUS_ALIASES = {
  completed: "success",
  succeeded: "success",
  cancelled: "cancelled",
  canceled: "cancelled",
  terminated: "cancelled",
};

export class AgentGraphView {
  constructor(containerId, dependencies) {
    this._stepExecutionFeed = dependencies.stepExecutionFeed;
    this._graphViewport = dependencies.graphViewport;
    this._requestStepCancellation = dependencies.requestStepCancellation;
    this._createArtifactListItem = dependencies.createArtifactListItem;
    this._renderStepConversationEvent = dependencies.renderStepConversationEvent;
    this._renderStepToolCall = dependencies.renderStepToolCall;
    this._syncPanelResizerVisibility = dependencies.syncPanelResizerVisibility;
    this._container = document.getElementById(containerId);
    this._surfaceEl = document.getElementById("graph-surface");
    this._nodes = new DataSet([]);
    this._edges = new DataSet([]);
    this._network = null;
    this._pollInterval = null;
    this._eventStream = null;
    this._didInitialFit = false;
    this._pendingFit = true;
    this._animationFrame = null;
    this._lastAnimationPaint = 0;
    this._motionTime = 0;
    this._activeEdges = [];
    this._vineEdges = [];
    this._hasRunningNodes = false;
    this._nodeTransitions = new Map();
    this._lastNodeStatuses = new Map();
    this._runningNodeIds = new Set();
    this._reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
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
    this._detailConversationCount = document.getElementById("detail-conversation-count");
    this._nodeData = {};
    this._layoutKey = null;
    this._cachedDisplayEdges = [];
    this._cachedPositions = {};
    this._cachedVineEdgeIds = new Set();
    this._displayEdgesByNode = new Map();
    this._edgePhases = new Map();
    this._nodeVisualKeys = new Map();
    this._detailRenderKey = null;
    this._graphSnapshot = null;
    this._activeDetailNodeId = null;
    this._detailDisclosures = createDisclosureController({
      captureScrollPosition: () => ({ scrollTop: this._detailEl.scrollTop }),
      restoreScrollPosition: (position) => {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (position) this._detailEl.scrollTop = position.scrollTop;
        }));
      },
    });
    this._init();
  }

  _init() {
    const edgeColors = this._edgeColors();
    const options = {
      // Agent activity uses a chronology-aware layout below.  A DAG layout
      // would put every child of a planner at the same depth and erase the
      // distinction between successive planning rounds.
      layout: { hierarchical: false },
      physics: { enabled: false },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.72 } },
        color: edgeColors,
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
        hoverConnectedEdges: true,
        tooltipDelay: 200,
        dragNodes: true,
        dragView: true,
        // vis-network zooms a fixed amount for each event, which is unstable
        // for high-resolution wheels and touchpads.
        zoomView: false,
      },
    };

    this._network = new Network(
      this._container,
      { nodes: this._nodes, edges: this._edges },
      options
    );
    installNetworkWheelZoom(this._container, this._network);

    this._network.on("selectNode", (params) => {
      if (params.nodes.length) this._showDetail(params.nodes[0]);
    });
    this._network.on("deselectNode", () => this._hideDetail());
    this._network.on("beforeDrawing", (ctx) => this._drawVines(ctx));
    this._network.on("afterDrawing", (ctx) => this._drawActiveFlow(ctx));
    window.addEventListener("matcreator-theme-change", () => this._applyTheme());
    this._detailClose?.addEventListener("click", () => {
      this._network.unselectAll();
      this._hideDetail();
    });
  }

  _edgeColors() {
    // Keep these colors opaque. vis-network draws the arrowhead over the last
    // segment of its edge; translucent colors compound at that seam and create
    // a visibly darker/lighter patch.
    return document.body.dataset.theme === "light"
      ? { color: "#b8c2d0", highlight: "#64748b", hover: "#8290a3", inherit: false }
      : { color: "#526176", highlight: "#cbd5e1", hover: "#94a3b8", inherit: false };
  }

  _applyTheme() {
    const color = this._edgeColors();
    const updates = this._edges.getIds().map((id) => ({ id, color }));
    if (updates.length) this._edges.update(updates);

    // Custom nodes read body[data-theme] while painting, so one immediate
    // redraw keeps canvas pixels in lockstep with the surrounding CSS theme.
    this._network?.redraw();
  }

  _nodeTooltip(raw) {
    const status = raw.status || "idle";
    const statusVisual = STATUS_VISUALS[status];
    const lines = [
      raw.label || raw.id,
      `Status: ${statusVisual?.label || status}`,
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

  _nodeTransition(nodeId) {
    const transition = this._nodeTransitions.get(nodeId);
    if (!transition) return null;
    const elapsed = performance.now() - transition.startedAt;
    if (elapsed >= STATUS_TRANSITION_MS) return null;
    return { ...transition, progress: Math.max(0, elapsed / STATUS_TRANSITION_MS) };
  }

  _hasLiveTransitions() {
    const now = performance.now();
    let hasLiveTransition = false;
    this._nodeTransitions.forEach((transition, nodeId) => {
      if (now - transition.startedAt < STATUS_TRANSITION_MS) {
        hasLiveTransition = true;
      } else {
        this._nodeTransitions.delete(nodeId);
      }
    });
    return hasLiveTransition;
  }

  _drawRunningAura(ctx, x, y, radius, isLight) {
    const pulse = this._reduceMotion
      ? 0.35
      : (Math.sin(this._motionTime / 330 + x * 0.015) + 1) / 2;
    const glowStrength = this._reduceMotion ? 0.78 : 0.55 + pulse * 0.45;
    const haloGap = 4.2;
    const gapBoundary = radius + haloGap;
    // Radius breathing is intentionally subtle; the pulse is primarily an
    // intensity change so the halo feels alive without wobbling like a loader.
    const radiusBreath = this._reduceMotion ? 0 : (pulse - 0.5) * 1.1;
    const coreRadius = radius + haloGap + 3.1 + radiusBreath;
    const outerRadius = coreRadius + 8.5;
    const haloCore = "245, 158, 11";
    const haloHighlight = "251, 191, 36";
    const alpha = glowStrength * (isLight ? 0.8 : 1);
    const drawLayer = (outer, stops) => {
      const gradient = ctx.createRadialGradient(x, y, gapBoundary, x, y, outer);
      stops.forEach(([offset, color, opacity]) => {
        gradient.addColorStop(offset, rgba(color, alpha * opacity));
      });
      ctx.beginPath();
      ctx.arc(x, y, outer, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
    };

    // The wide outer field carries most of the breathing light, while its
    // first stop keeps the node-facing gap visibly dark.
    drawLayer(outerRadius, [
      [0, haloCore, 0],
      [0.16, haloCore, 0.12],
      [0.4, haloCore, 0.46],
      [0.64, haloHighlight, 0.28],
      [1, haloCore, 0],
    ]);
    // A narrower inward falloff gives the ring a soft inner lip without
    // painting into the dark clearance around the node.
    drawLayer(coreRadius + 1.4, [
      [0, haloCore, 0],
      [0.34, haloCore, 0.35],
      [0.78, haloHighlight, 0.82],
      [1, haloHighlight, 0.32],
    ]);
    // The running state is intentionally glow-only: the layered falloff
    // provides the shape while preserving the dark clearance around the node.
  }

  _drawStatusGlyph(ctx, symbol, x, y, radius, color) {
    const unit = radius / 5.4;
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(unit, unit);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.45;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    if (symbol === "✓") {
      ctx.beginPath();
      ctx.moveTo(-3.1, -0.1);
      ctx.lineTo(-0.8, 2.35);
      ctx.lineTo(3.5, -2.65);
      ctx.stroke();
    } else if (symbol === "!") {
      ctx.beginPath();
      ctx.moveTo(0, -3.25);
      ctx.lineTo(0, 0.75);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, 3.05, 0.78, 0, Math.PI * 2);
      ctx.fill();
    } else if (symbol === "?") {
        ctx.beginPath();

        // compact upper hook
        ctx.moveTo(-1.65, -1.65);
        ctx.bezierCurveTo(
            -1.35, -2.75,
            0.05, -3.15,
            1.25, -2.55
        );
        ctx.bezierCurveTo(
            2.15, -2.05,
            2.05, -0.85,
            1.05, -0.20
        );
        ctx.bezierCurveTo(
            0.30, 0.30,
            0.00, 0.65,
            0.00, 1.25
        );
        ctx.stroke();

        // clearly separated dot
        ctx.beginPath();
        ctx.arc(0, 3.65, 0.85, 0, Math.PI * 2);
        ctx.fill();
    } else if (symbol === "…") {
      [-2.4, 0, 2.4].forEach((offset) => {
        ctx.beginPath();
        ctx.arc(offset, 0.4, 0.75, 0, Math.PI * 2);
        ctx.fill();
      });
    } else if (symbol === "×") {
      ctx.beginPath();
      ctx.moveTo(-2.55, -2.55);
      ctx.lineTo(2.55, 2.55);
      ctx.moveTo(2.55, -2.55);
      ctx.lineTo(-2.55, 2.55);
      ctx.stroke();
    }
    ctx.restore();
  }

  _drawStatusBadge(ctx, x, y, radius, status, transition) {
    const visual = STATUS_VISUALS[status] || STATUS_VISUALS.idle;
    if (!visual.symbol) return;

    const arrival = transition?.to === status ? Math.min(1, transition.progress / 0.62) : 1;
    const easedArrival = 1 - (1 - arrival) ** 3;
    const failurePulse = status === "failed" && transition?.to === status
      ? 1 + Math.sin(Math.min(1, transition.progress) * Math.PI) * 0.16
      : 1;
    const badgeRadius = Math.max(5, radius * 0.34) * (0.72 + easedArrival * 0.28) * failurePulse;
    const badgeX = x + radius * 0.73;
    const badgeY = y - radius * 0.73;

    ctx.save();
    ctx.globalAlpha = 0.78 + easedArrival * 0.22;
    ctx.beginPath();
    ctx.arc(badgeX, badgeY, badgeRadius, 0, Math.PI * 2);
    ctx.fillStyle = rgba(visual.color, 1);
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = document.body.dataset.theme === "light"
      ? "rgba(15, 23, 42, 0.16)"
      : "rgba(255, 255, 255, 0.42)";
    ctx.stroke();
    const glyphColor = status === "waiting" || status === "pending" || status === "idle" || status === "cancelled"
      ? "#f8fafc"
      : "#172033";
    this._drawStatusGlyph(ctx, visual.symbol, badgeX, badgeY, badgeRadius, glyphColor);
    ctx.restore();
  }

  _nodeRenderer(raw, typeVisual, badge, radius) {
    return ({ ctx, x, y, state }) => {
      const selected = Boolean(state?.selected);
      const hover = Boolean(state?.hover);
      const status = raw.status || "idle";
      const isRunning = status === "running";
      const isCancelled = status === "cancelled";
      const drawRadius = radius + (selected ? 2 : hover ? 1 : 0);
      const borderWidth = selected ? 2.4 : hover ? 2.1 : 1.55;

      return {
        drawNode: () => {
          // vis-network calls custom renderers once with NaN coordinates while
          // measuring a new hierarchical node. Canvas drawing APIs reject
          // those values, so leave that sizing pass blank; nodeDimensions below are
          // still returned and the following positioned redraw paints it.
          if (!Number.isFinite(x) || !Number.isFinite(y)) return;
          ctx.save();
          const isLight = document.body.dataset.theme === "light";
          const transition = this._reduceMotion ? null : this._nodeTransition(raw.id);

          // Preserve the established active-node treatment: a warm, breathing
          // aura that is distinct from the quiet static status badges.
          if (isRunning) this._drawRunningAura(ctx, x, y, drawRadius, isLight);

          // Selection is neutral and deliberately tight so it cannot be
          // mistaken for a lifecycle state.
          if (selected) {
            ctx.beginPath();
            ctx.arc(x, y, drawRadius + 3.4, 0, Math.PI * 2);
            ctx.lineWidth = 1.45;
            ctx.strokeStyle = isLight ? "rgba(15, 23, 42, 0.72)" : "rgba(248, 250, 252, 0.78)";
            ctx.stroke();
          }

          // First paint an opaque backing plate. Edges are rendered on the
          // layer below nodes, so this makes connections terminate cleanly at
          // the badge boundary instead of showing through its colored face.
          ctx.beginPath();
          ctx.arc(x, y, drawRadius, 0, Math.PI * 2);
          ctx.fillStyle = isLight ? "#f8fafc" : "#172033";
          ctx.fill();

          // A flat type face keeps the identity legible without making
          // lifecycle state compete through the same colour channel. Dark
          // mode uses a more saturated palette and an opaque face; blending
          // the light pastel colors into the backing plate made every node
          // look like the same grey, translucent chip.
          ctx.beginPath();
          ctx.arc(x, y, drawRadius - 0.7, 0, Math.PI * 2);
          const palette = isLight ? typeVisual : typeVisual.dark || typeVisual;
          const faceAlpha = isCancelled ? 0.55 : 1;
          ctx.fillStyle = rgba(palette.fill, faceAlpha * (isLight
            ? (hover || selected ? 0.9 : 0.78)
            : 1));
          ctx.fill();
          ctx.lineWidth = borderWidth;
          ctx.strokeStyle = rgba(
            palette.border,
            faceAlpha * (isLight ? (selected ? 1 : hover ? 0.96 : 0.82) : 1),
          );
          ctx.stroke();

          ctx.fillStyle = palette.text;
          if (isCancelled) ctx.globalAlpha = 0.72;
          ctx.font = `800 ${badge.length > 1 ? 11 : 12.5}px Manrope, system-ui, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          const metrics = ctx.measureText(badge);
          const opticalOffset = metrics.actualBoundingBoxLeft !== undefined
            ? (metrics.actualBoundingBoxLeft - metrics.actualBoundingBoxRight) / 2
            : 0;
          ctx.fillText(badge, x + opticalOffset, y);
          this._drawStatusBadge(ctx, x, y, drawRadius, status, transition);
          ctx.restore();
        },
        nodeDimensions: {
          width: (radius + 7) * 2,
          height: (radius + 7) * 2,
        },
      };
    };
  }

  _visNode(raw) {
    const typeVisual = NODE_TYPE_VISUALS[raw.type] || NODE_TYPE_VISUALS.step;
    const palette = document.body.dataset.theme === "light"
      ? typeVisual
      : typeVisual.dark || typeVisual;
    const badge = this._nodeBadge(raw);
    const radius = this._nodeRadius(raw);
    return {
      id: raw.id,
      label: "",
      shape: "custom",
      color: {
        background: rgba(palette.fill, 1),
        border: rgba(palette.border, 1),
        highlight: { background: rgba(palette.fill, 1), border: rgba(palette.border, 1) },
      },
      // vis-network may retain a custom renderer between DataSet updates.
      // Resolve the current node data while painting so a completed node can
      // never keep the renderer closure from its earlier running state.
      ctxRenderer: (params) => {
        const current = this._nodeData[raw.id] || raw;
        const currentTypeVisual = NODE_TYPE_VISUALS[current.type] || NODE_TYPE_VISUALS.step;
        return this._nodeRenderer(
          current,
          currentTypeVisual,
          this._nodeBadge(current),
          this._nodeRadius(current),
        )(params);
      },
      title: this._nodeTooltip(raw),
    };
  }

  _normalizeNodeStatus(status) {
    const normalized = String(status || "idle").toLowerCase();
    return STATUS_ALIASES[normalized] || (STATUS_VISUALS[normalized] ? normalized : "idle");
  }

  _drawActiveFlow(ctx) {
    if (!this._network || !this._activeEdges.length) return;
    const positions = this._network.getPositions();
    const time = this._reduceMotion ? 0 : this._motionTime;

    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const edge of this._activeEdges) {
      const from = positions[edge.from];
      const to = positions[edge.to];
      if (
        !from || !to ||
        !Number.isFinite(from.x) || !Number.isFinite(from.y) ||
        !Number.isFinite(to.x) || !Number.isFinite(to.y)
      ) continue;
      const color = edge.color || STATUS_VISUALS.running;
      const midY = (from.y + to.y) / 2;

      const pointOnCurve = (progress) => {
        const inverse = 1 - progress;
        return {
          x: inverse ** 3 * from.x
            + 3 * inverse ** 2 * progress * from.x
            + 3 * inverse * progress ** 2 * to.x
            + progress ** 3 * to.x,
          y: inverse ** 3 * from.y
            + 3 * inverse ** 2 * progress * midY
            + 3 * inverse * progress ** 2 * midY
            + progress ** 3 * to.y,
        };
      };

      const particleCount = this._reduceMotion ? 1 : 2;
      for (let index = 0; index < particleCount; index++) {
        const progress = this._reduceMotion
          ? 0.6
          : ((time / 1500 + index / particleCount + edge.phase) % 1);
        const point = pointOnCurve(progress);
        const fade = Math.sin(progress * Math.PI);
        ctx.beginPath();
        ctx.arc(point.x, point.y, 2.1, 0, Math.PI * 2);
        ctx.fillStyle = rgba(color.edge, 0.28 + fade * 0.62);
        ctx.shadowColor = rgba(color.color, 0.9);
        ctx.shadowBlur = 9;
        ctx.fill();
      }
    }
    ctx.restore();
  }

  _syncAnimation() {
    if (this._reduceMotion) this._nodeTransitions.clear();
    const needsAnimation = !this._reduceMotion && (this._hasRunningNodes || this._hasLiveTransitions());
    if (!needsAnimation) {
      if (this._animationFrame !== null) cancelAnimationFrame(this._animationFrame);
      this._animationFrame = null;
      this._network?.redraw();
      return;
    }
    if (this._animationFrame !== null) return;

    const animate = (time) => {
      this._motionTime = time;
      // 30fps is smooth for slow orbital/flow motion and avoids paying for a
      // full vis-network canvas redraw on every display refresh.
      if (time - this._lastAnimationPaint >= 32) {
        this._network?.redraw();
        this._lastAnimationPaint = time;
      }
      if (!this._reduceMotion && (this._hasRunningNodes || this._hasLiveTransitions())) {
        this._animationFrame = requestAnimationFrame(animate);
      } else {
        // The final redraw commits the static badge/orbit after a short
        // transition has ended, even if the last throttled paint was early.
        this._network?.redraw();
        this._animationFrame = null;
      }
    };
    this._animationFrame = requestAnimationFrame(animate);
  }

  _timeKey(node) {
    const value = node?.start_time ? new Date(node.start_time).getTime() : NaN;
    return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
  }

  _executionBatchId(node, plannerId) {
    // batch_id is optional for older graph snapshots. Untagged direct siblings
    // belong to one legacy batch rather than being split into one layer per
    // node: sequential dispatch is still work from the same planning round.
    // New snapshots carry explicit IDs, so replanning rounds remain separate.
    return String(
      node.batch_id
      ?? node.execution_batch_id
      ?? node.input?.batch_id
      ?? node.input?.execution_batch_id
      ?? `legacy:${plannerId}`,
    );
  }

  _chronologicalTaskRows(nodeIds, nodeMap) {
    const timed = nodeIds.map((id) => {
      const node = nodeMap[id];
      const startValue = node?.start_time ? new Date(node.start_time).getTime() : NaN;
      const endValue = node?.end_time ? new Date(node.end_time).getTime() : NaN;
      return {
        id,
        start: startValue,
        hasStart: Number.isFinite(startValue),
        // An unfinished task keeps its row open. That makes concurrently
        // running work stay together instead of being rendered as a sequence.
        end: Number.isFinite(endValue) ? endValue : Number.MAX_SAFE_INTEGER,
      };
    }).sort((a, b) => {
      if (a.hasStart !== b.hasStart) return a.hasStart ? -1 : 1;
      return a.start - b.start || a.id.localeCompare(b.id);
    });

    if (!timed.some((task) => task.hasStart)) return [nodeIds];

    const rows = [];
    let rowEnd = -Infinity;
    timed.forEach((task) => {
      // A snapshot without a start time has no reliable chronology. Keep it
      // beside the current wave instead of inventing a sequential ordering.
      if (!task.hasStart) {
        rows[rows.length - 1].push(task.id);
        return;
      }
      if (!rows.length || task.start >= rowEnd) {
        rows.push([task.id]);
        rowEnd = task.end;
      } else {
        rows[rows.length - 1].push(task.id);
        rowEnd = Math.max(rowEnd, task.end);
      }
    });
    return rows;
  }

  _sequenceTaskDisplayEdges(displayEdges, nodeMap) {
    const directTaskEdges = new Map();
    displayEdges.forEach((edge) => {
      if (nodeMap[edge.from]?.type !== "execution" || nodeMap[edge.to]?.type !== "step") return;
      if (!directTaskEdges.has(edge.from)) directTaskEdges.set(edge.from, []);
      directTaskEdges.get(edge.from).push(edge);
    });
    if (!directTaskEdges.size) return displayEdges;

    const replacedEdgeIds = new Set();
    const sequenceEdges = [];
    directTaskEdges.forEach((taskEdges, executionId) => {
      const rows = this._chronologicalTaskRows(taskEdges.map((edge) => edge.to), nodeMap);
      if (rows.length < 2) return;
      taskEdges.forEach((edge) => replacedEdgeIds.add(edge.id));

      // The first concurrent wave keeps E as its source. Later waves receive
      // their visual connection from the preceding wave, yielding E -> 1 -> 2
      // for a sequential pair while preserving same-row parallelism.
      rows[0].forEach((to) => sequenceEdges.push({
        id: `sequence__${executionId}__${to}`,
        from: executionId,
        to,
      }));
      for (let rowIndex = 1; rowIndex < rows.length; rowIndex++) {
        const previous = rows[rowIndex - 1];
        // A following wave begins only after the preceding parallel wave has
        // completed. Draw every predecessor so a join such as 1 + 2 -> 3
        // keeps both dependency lines rather than silently choosing task 1.
        previous.forEach((from) => rows[rowIndex].forEach((to) => sequenceEdges.push({
          id: `sequence__${executionId}__${from}__${to}`,
          from,
          to,
        })));
      }
    });
    return [
      ...displayEdges.filter((edge) => !replacedEdgeIds.has(edge.id)),
      ...sequenceEdges,
    ];
  }

  _computeVineLayout(rawNodes, edges) {
    const nodeMap = Object.fromEntries(rawNodes.map((node) => [node.id, node]));
    const children = Object.fromEntries(rawNodes.map((node) => [node.id, []]));
    (edges || []).forEach((edge) => {
      if (children[edge.from] && nodeMap[edge.to]) children[edge.from].push(edge.to);
    });
    Object.values(children).forEach((ids) => ids.sort((a, b) =>
      this._timeKey(nodeMap[a]) - this._timeKey(nodeMap[b]) || String(a).localeCompare(String(b))));

    const positions = {};
    const placed = new Set();
    const planners = rawNodes.filter((node) => node.type === "planning")
      .sort((a, b) => this._timeKey(a) - this._timeKey(b) || a.id.localeCompare(b.id));
    const plannerCount = planners.length;

    planners.forEach((planner, plannerIndex) => {
      const plannerX = (plannerIndex - (plannerCount - 1) / 2) * VINE_PLANNER_GAP;
      positions[planner.id] = { x: plannerX, y: 0 };
      placed.add(planner.id);

      const batches = new Map();
      children[planner.id]
        .filter((id) => nodeMap[id]?.type === "execution")
        .forEach((id) => {
          const batchId = this._executionBatchId(nodeMap[id], planner.id);
          if (!batches.has(batchId)) batches.set(batchId, []);
          batches.get(batchId).push(id);
        });
      const orderedBatches = [...batches.entries()].sort(([aId, a], [bId, b]) => {
        const aTime = Math.min(...a.map((id) => this._timeKey(nodeMap[id])));
        const bTime = Math.min(...b.map((id) => this._timeKey(nodeMap[id])));
        return aTime - bTime || aId.localeCompare(bId);
      });

      let previousBatchY = VINE_BATCH_GAP - VINE_BATCH_STAGGER;
      // Alternating branches use independent vertical lanes. A new branch on
      // the opposite side may tuck in after a small stagger; returning to a
      // side waits until that side's existing leaf fan has cleared.
      const sideClearY = new Map([[-1, VINE_BATCH_GAP], [1, VINE_BATCH_GAP]]);
      orderedBatches.forEach(([, executionIds], batchIndex) => {
        executionIds.sort((a, b) => this._timeKey(nodeMap[a]) - this._timeKey(nodeMap[b]) || a.localeCompare(b));
        const isLatestBatch = batchIndex === orderedBatches.length - 1;
        // Measure each E subtree independently. Pooling all sibling task
        // nodes into global rows made their branches cross and obscured which
        // execution owned each task.
        const subtrees = executionIds.map((rootId) => {
          const rows = [];
          const seen = new Set([rootId]);
          let frontier = [rootId];
          while (frontier.length) {
            const next = [];
            frontier.forEach((parentId) => children[parentId].forEach((childId) => {
              if (!seen.has(childId) && nodeMap[childId]?.type !== "execution") {
                seen.add(childId);
                next.push(childId);
              }
            }));
            if (!next.length) break;
            rows.push(next);
            frontier = next;
          }
          const widestRow = Math.max(0, ...rows.map((ids) => (ids.length - 1) * VINE_NODE_GAP));
          return {
            rootId,
            rows,
            maxDepth: rows.length,
            width: Math.max(VINE_NODE_GAP, widestRow + VINE_NODE_GAP),
          };
        });
        const batchWidth = subtrees.reduce((total, subtree) => total + subtree.width, 0);
        const maxDepth = Math.max(0, ...subtrees.map((subtree) => subtree.maxDepth));
        const subtreeHalfWidth = batchWidth / 2 + this._nodeRadius({ type: "step" });
        const side = batchIndex % 2 === 0 ? 1 : -1;
        const nextStaggerY = previousBatchY + VINE_BATCH_STAGGER;
        const batchY = isLatestBatch
          // The centered tip shares horizontal space with both historical
          // sides. Place it below the deepest task generation from either
          // side, not merely below the preceding execution node.
          ? Math.max(nextStaggerY, ...sideClearY.values())
          : Math.max(nextStaggerY, sideClearY.get(side));
        const batchCenterX = isLatestBatch
          ? plannerX
          : plannerX + side * (subtreeHalfWidth + VINE_STEM_CLEARANCE);

        let subtreeLeft = batchCenterX - batchWidth / 2;
        subtrees.forEach((subtree) => {
          const rootX = subtreeLeft + subtree.width / 2;
          positions[subtree.rootId] = { x: rootX, y: batchY };
          placed.add(subtree.rootId);
          subtree.rows.forEach((ids, depthIndex) => {
            const rowWidth = (ids.length - 1) * VINE_NODE_GAP;
            ids.forEach((id, index) => {
              positions[id] = {
                x: rootX + index * VINE_NODE_GAP - rowWidth / 2,
                y: batchY + (depthIndex + 1) * VINE_DESCENDANT_GAP,
              };
              placed.add(id);
            });
          });
          subtreeLeft += subtree.width;
        });
        previousBatchY = batchY;
        if (!isLatestBatch) {
          sideClearY.set(
            side,
            batchY + maxDepth * VINE_DESCENDANT_GAP + VINE_BATCH_STAGGER,
          );
        }
      });
    });

    // Keep the orchestrator immediately above its planners.  Any malformed or
    // unrelated node remains visible in a small fallback strip instead of
    // being silently omitted from the graph.
    rawNodes.filter((node) => node.type === "orchestrator").forEach((node, index) => {
      positions[node.id] = { x: index * VINE_PLANNER_GAP, y: -VINE_BATCH_GAP };
      placed.add(node.id);
    });
    rawNodes.filter((node) => !placed.has(node.id)).forEach((node, index) => {
      positions[node.id] = { x: index * VINE_NODE_GAP, y: VINE_BATCH_GAP };
    });
    return positions;
  }

  _drawVines(ctx) {
    if (!this._network || !this._vineEdges.length) return;
    const positions = this._network.getPositions();
    const isLight = document.body.dataset.theme === "light";
    const color = isLight ? "#8290a3" : "#64748b";
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.7;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    this._vineEdges.forEach(({ from, branches }) => {
      const source = positions[from];
      if (!source || !branches.length) return;
      const sourceY = source.y + this._nodeRadius(this._nodeData[from]) + 1;
      const stemEndY = Math.max(...branches.map(({ to }) => {
        const target = positions[to];
        return target ? target.y - this._nodeRadius(this._nodeData[to]) - 34 : sourceY;
      }));

      // The stem is drawn once, so later planning rounds visibly extend the
      // same P connection rather than appearing as unrelated long edges.
      ctx.beginPath();
      ctx.moveTo(source.x, sourceY);
      ctx.lineTo(source.x, stemEndY);
      ctx.stroke();

      branches.forEach(({ to }) => {
        const target = positions[to];
        if (!target) return;
        const targetY = target.y - this._nodeRadius(this._nodeData[to]) - 1;
        const branchY = targetY - 34;
        ctx.beginPath();
        ctx.moveTo(source.x, branchY);
        ctx.bezierCurveTo(
          source.x, branchY + 18,
          target.x, branchY - 18,
          target.x, targetY,
        );
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(target.x, targetY);
        ctx.lineTo(target.x - 4, targetY - 7);
        ctx.lineTo(target.x + 4, targetY - 7);
        ctx.closePath();
        ctx.fill();
      });
    });
    ctx.restore();
  }

  _buildDisplayEdges(rawNodes, edges) {
    const nodeMap = Object.fromEntries(rawNodes.map((n) => [n.id, n]));
    const phaseTypes = new Set(["planning", "execution", "tester"]);
    const displayEdges = [];
    const phaseNodes = rawNodes
      .filter((n) => phaseTypes.has(n.type))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });

    const planningNodes = phaseNodes.filter((node) => node.type === "planning");
    const childPhaseNodes = phaseNodes.filter((node) => node.type !== "planning");

    planningNodes.forEach((planning) => {
      displayEdges.push({
        id: `phase__orchestrator__${planning.id}`,
        from: "orchestrator",
        to: planning.id,
      });
    });

    // Newer graph records persist the actual planning parent. Older sessions
    // logged every phase under the orchestrator, so retain temporal grouping as
    // a backwards-compatible fallback.
    childPhaseNodes.forEach((node) => {
      const persistedParent = nodeMap[node.parent_id];
      if (persistedParent?.type === "planning") {
        displayEdges.push({
          id: `phase__${persistedParent.id}__${node.id}`,
          from: persistedParent.id,
          to: node.id,
        });
        return;
      }

      const nodeStart = node.start_time ? new Date(node.start_time).getTime() : Infinity;
      let parentPlanning = null;
      for (const planning of planningNodes) {
        const planningStart = planning.start_time
          ? new Date(planning.start_time).getTime()
          : -Infinity;
        if (planningStart <= nodeStart) parentPlanning = planning;
        else break;
      }

      const parentId = parentPlanning?.id || "orchestrator";
      displayEdges.push({
        id: `phase__${parentId}__${node.id}`,
        from: parentId,
        to: node.id,
      });
    });

    (edges || []).forEach((edge) => {
      const fromNode = nodeMap[edge.from];
      const toNode = nodeMap[edge.to];
      if (!fromNode || !toNode) return;

      // Phase relationships are normalized above. Ignore their persisted
      // incoming edges so sessions created by either logger version render
      // with the same Planning -> Execution grouping.
      if (phaseTypes.has(toNode.type)) return;

      displayEdges.push({
        id: edge.id || `${edge.from}__${edge.to}`,
        from: edge.from,
        to: edge.to,
      });
    });

    return this._sequenceTaskDisplayEdges(displayEdges, nodeMap);
  }

  _resizeSurface() {
    if (!this._surfaceEl || !this._graphViewport) return null;

    // Match the canvas to the visible viewport exactly; larger off-screen
    // surfaces make fit() center against hidden space instead of the panel.
    const targetWidth = Math.max(1, Math.round(this._graphViewport.clientWidth || 1));
    const targetHeight = Math.max(1, Math.round(this._graphViewport.clientHeight || 1));
    const width = `${targetWidth}px`;
    const height = `${targetHeight}px`;
    if (this._surfaceEl.style.width === width && this._surfaceEl.style.height === height) return null;
    this._surfaceEl.style.width = width;
    this._surfaceEl.style.height = height;
    return { width, height };
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

  _graphLayoutKey(rawNodes, edges) {
    // Conversation/tool payloads can grow without changing graph geometry.
    // Keep them out of this key so those hot updates reuse the cold layout.
    return JSON.stringify({
      nodes: rawNodes.map((node) => [
        node.id,
        node.type,
        node.parent_id || "",
        node.batch_id || node.execution_batch_id || "",
        node.start_time || "",
        node.end_time || "",
        node.input?.node_id || node.input?.step_id || "",
      ]),
      edges: (edges || []).map((edge) => [edge.id || "", edge.from, edge.to]),
    });
  }

  _nodeVisualKey(node) {
    return JSON.stringify([
      node.status,
      node.type,
      node.label,
      node.summary,
      node.start_time,
      node.end_time,
      node.input?.step_number,
    ]);
  }

  _nodeDetailKey(node) {
    if (!node) return "";
    return JSON.stringify([
      this._nodeVisualKey(node),
      node.input,
      node.artifacts,
      node.tool_calls,
      node.type === "step" ? null : node.conversation,
    ]);
  }

  update(incomingGraphData) {
    if (!incomingGraphData || typeof incomingGraphData.nodes !== "object") return;
    const patch = applyGraphUpdate(this._graphSnapshot, incomingGraphData);
    const graphData = patch.graph;
    this._graphSnapshot = graphData;

    const layoutMayChange = !patch.isDelta || patch.layoutChanged;
    const prevNodeIds = layoutMayChange ? new Set(this._nodes.getIds()) : null;
    const prevEdgeIds = layoutMayChange ? new Set(this._edges.getIds()) : null;
    let rawNodeMap;
    if (patch.isDelta) {
      rawNodeMap = { ...this._nodeData };
      for (const id of patch.changedNodeIds) {
        const node = graphData.nodes[id];
        if (!node) {
          delete rawNodeMap[id];
          continue;
        }
        const status = this._normalizeNodeStatus(node.status);
        rawNodeMap[id] = status === node.status ? node : { ...node, status };
      }
    } else {
      rawNodeMap = Object.fromEntries(Object.values(graphData.nodes).map((node) => {
        const status = this._normalizeNodeStatus(node.status);
        const normalized = status === node.status ? node : { ...node, status };
        return [normalized.id, normalized];
      }));
    }
    const rawNodes = Object.values(rawNodeMap);
    const transitionStartedAt = performance.now();
    const nextNodeStatuses = patch.isDelta ? new Map(this._lastNodeStatuses) : new Map();
    if (!patch.isDelta) this._runningNodeIds.clear();
    const statusNodes = patch.isDelta
      ? [...patch.changedNodeIds].map((id) => rawNodeMap[id]).filter(Boolean)
      : rawNodes;
    patch.changedNodeIds.forEach((id) => {
      if (!rawNodeMap[id]) {
        nextNodeStatuses.delete(id);
        this._runningNodeIds.delete(id);
      }
    });
    statusNodes.forEach((node) => {
      const previousStatus = this._lastNodeStatuses.get(node.id);
      if (!this._reduceMotion && previousStatus && previousStatus !== node.status) {
        this._nodeTransitions.set(node.id, {
          from: previousStatus,
          to: node.status,
          startedAt: transitionStartedAt,
        });
      }
      nextNodeStatuses.set(node.id, node.status);
      if (node.status === "running") this._runningNodeIds.add(node.id);
      else this._runningNodeIds.delete(node.id);
    });
    this._lastNodeStatuses = nextNodeStatuses;
    this._nodeData = rawNodeMap;
    this._stepExecutionFeed.update(graphData, patch);
    const layoutKey = patch.isDelta && !patch.layoutChanged
      ? this._layoutKey
      : this._graphLayoutKey(rawNodes, graphData.edges || []);
    const layoutChanged = patch.isDelta ? patch.layoutChanged : layoutKey !== this._layoutKey;
    if (layoutChanged) {
      this._layoutKey = layoutKey;
      this._cachedDisplayEdges = this._buildDisplayEdges(rawNodes, graphData.edges || []);
      this._displayEdgesByNode = new Map();
      this._edgePhases = new Map();
      this._cachedDisplayEdges.forEach((edge, index) => {
        const edgeId = edge.id || `${edge.from}__${edge.to}`;
        this._edgePhases.set(edgeId, (index * 0.173) % 1);
        for (const nodeId of [edge.from, edge.to]) {
          const adjacent = this._displayEdgesByNode.get(nodeId) || [];
          adjacent.push(edge);
          this._displayEdgesByNode.set(nodeId, adjacent);
        }
      });
      this._cachedPositions = this._computeVineLayout(rawNodes, this._cachedDisplayEdges);
      this._cachedVineEdgeIds = new Set(this._cachedDisplayEdges
        .filter((edge) => rawNodeMap[edge.from]?.type === "planning" && rawNodeMap[edge.to]?.type === "execution")
        .map((edge) => edge.id || `${edge.from}__${edge.to}`));
    }
    const displayEdges = this._cachedDisplayEdges;
    this._hasRunningNodes = this._runningNodeIds.size > 0;
    const activeEdgeIds = new Set();
    this._activeEdges = [];
    for (const nodeId of this._runningNodeIds) {
      for (const edge of this._displayEdgesByNode.get(nodeId) || []) {
        const edgeId = edge.id || `${edge.from}__${edge.to}`;
        if (activeEdgeIds.has(edgeId)
          || rawNodeMap[edge.from]?.status !== "running"
          || rawNodeMap[edge.to]?.status !== "running") continue;
        activeEdgeIds.add(edgeId);
        this._activeEdges.push({
          ...edge,
          color: STATUS_VISUALS.running,
          phase: this._edgePhases.get(edgeId) || 0,
        });
      }
    }
    const positions = this._cachedPositions;
    const vineEdgeIds = this._cachedVineEdgeIds;
    if (layoutChanged) {
      const vineTargetsByPlanner = new Map();
      displayEdges.forEach((edge) => {
        if (!vineEdgeIds.has(edge.id || `${edge.from}__${edge.to}`)) return;
        if (!vineTargetsByPlanner.has(edge.from)) vineTargetsByPlanner.set(edge.from, []);
        vineTargetsByPlanner.get(edge.from).push(edge.to);
      });
      this._vineEdges = [...vineTargetsByPlanner.entries()].map(([from, targetIds]) => ({
        from,
        branches: targetIds.map((to) => ({ to })),
      }));
    }
    this._resizeSurface();
    const nextNodeIds = layoutChanged ? new Set(rawNodes.map((raw) => raw.id)) : null;
    const nextEdgeIds = layoutChanged
      ? new Set(displayEdges.map((e) => e.id || `${e.from}__${e.to}`))
      : null;
    const topologyChanged = layoutChanged && (
      prevNodeIds.size !== nextNodeIds.size ||
      prevEdgeIds.size !== nextEdgeIds.size ||
      [...nextNodeIds].some((id) => !prevNodeIds.has(id)) ||
      [...nextEdgeIds].some((id) => !prevEdgeIds.has(id))
    );

    if (layoutChanged) this._nodes.getIds().forEach((nodeId) => {
      if (!nextNodeIds.has(nodeId)) this._nodes.remove(nodeId);
    });

    const nextNodeVisualKeys = layoutChanged || !patch.isDelta
      ? new Map()
      : new Map(this._nodeVisualKeys);
    const nodesToUpdate = layoutChanged || !patch.isDelta
      ? rawNodes
      : [...patch.changedNodeIds].map((id) => rawNodeMap[id]).filter(Boolean);
    patch.changedNodeIds.forEach((id) => {
      if (!rawNodeMap[id]) nextNodeVisualKeys.delete(id);
    });
    nodesToUpdate.forEach((raw) => {
      const visualKey = this._nodeVisualKey(raw);
      nextNodeVisualKeys.set(raw.id, visualKey);
      const isNew = !this._nodes.get(raw.id);
      if (!isNew && !layoutChanged && this._nodeVisualKeys.get(raw.id) === visualKey) return;
      const vis = this._visNode(raw);
      const position = positions[raw.id] || { x: 0, y: 0 };
      vis.x = position.x;
      vis.y = position.y;
      vis.fixed = { x: true, y: true };
      if (!isNew) {
        this._nodes.update(vis);
      } else {
        this._nodes.add(vis);
      }
    });
    this._nodeVisualKeys = nextNodeVisualKeys;

    if (layoutChanged) this._edges.getIds().forEach((edgeId) => {
      if (!nextEdgeIds.has(edgeId)) this._edges.remove(edgeId);
    });

    if (layoutChanged) displayEdges.forEach((e) => {
      const edgeId = e.id || `${e.from}__${e.to}`;
      const visEdge = {
        id: edgeId,
        from: e.from,
        to: e.to,
        // Planner -> execution links are painted as routed vines in
        // beforeDrawing. The edge itself stays in the DataSet, preserving the
        // real graph topology for interaction and future consumers.
        hidden: vineEdgeIds.has(edgeId),
        physics: false,
        width: 1.35,
        color: this._edgeColors(),
        smooth: { type: "cubicBezier", forceDirection: "vertical" },
      };
      if (this._edges.get(edgeId)) this._edges.update(visEdge);
      else this._edges.add(visEdge);
    });

    if (rawNodes.length > 0 && (topologyChanged || !this._didInitialFit || this._pendingFit)) {
      this._fitGraph();
    }
    this._syncAnimation();

    if (this._activeDetailNodeId) {
      if (this._nodeData[this._activeDetailNodeId]) {
        const detailKey = this._nodeDetailKey(this._nodeData[this._activeDetailNodeId]);
        if (detailKey !== this._detailRenderKey) {
          this._showDetail(this._activeDetailNodeId, { preserveScroll: true, scrollToStep: false });
        }
      } else {
        this._hideDetail();
      }
    }
  }

  startPolling(sessionId) {
    this.stopPolling();
    this._currentSessionId = sessionId;
    void this._poll(sessionId);
    const eventStream = new EventSource(`/api/agent-graph/${encodeURIComponent(sessionId)}/events`);
    this._eventStream = eventStream;
    eventStream.onmessage = (event) => {
      try {
        if (sessionId !== this._currentSessionId) return;
        this.update(JSON.parse(event.data));
      } catch (_) {
        // Ignore a malformed snapshot; EventSource will deliver the next one.
      }
    };
    // Keep a low-frequency fallback for deployments running an older web
    // backend that does not yet expose the graph event endpoint. Close the
    // EventSource before starting it so browser reconnects cannot deliver the
    // same snapshots alongside the fallback poller.
    eventStream.onerror = () => {
      if (this._eventStream !== eventStream) return;
      eventStream.close();
      this._eventStream = null;
      if (!this._pollInterval) this._pollInterval = setInterval(() => this._poll(sessionId), 2000);
    };
  }

  stopPolling() {
    this._eventStream?.close();
    this._eventStream = null;
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
    // Polling owns the running-node animation lifecycle. A cancellation can
    // stop updates while the last received snapshot still says "running";
    // without clearing that stale flag the canvas redraw loop never ends.
    this._hasRunningNodes = false;
    this._activeEdges = [];
    this._nodeTransitions.clear();
    this._lastNodeStatuses.clear();
    this._runningNodeIds.clear();
    if (this._animationFrame !== null) cancelAnimationFrame(this._animationFrame);
    this._animationFrame = null;
    this._network?.redraw();
  }

  async _poll(sessionId) {
    try {
      const data = await httpClient.getJson(`/api/agent-graph/${encodeURIComponent(sessionId)}`);
      if (data === null) return;
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
    this._graphSnapshot = null;
    this._layoutKey = null;
    this._cachedDisplayEdges = [];
    this._cachedPositions = {};
    this._cachedVineEdgeIds = new Set();
    this._displayEdgesByNode.clear();
    this._edgePhases.clear();
    this._nodeVisualKeys.clear();
    this._detailRenderKey = null;
    this._didInitialFit = false;
    this._pendingFit = true;
    this._hasRunningNodes = false;
    this._activeEdges = [];
    this._runningNodeIds.clear();
    this._detailDisclosures.clear();
    if (this._animationFrame !== null) cancelAnimationFrame(this._animationFrame);
    this._animationFrame = null;
    this._lastAnimationPaint = 0;
    this._resizeSurface([], { 0: 1 });
    this._hideDetail();
    this.stopPolling();
  }

  _showDetail(nodeId, options = {}) {
    const raw = this._nodeData[nodeId];
    if (!raw) return;
    this._activeDetailNodeId = nodeId;
    this._detailRenderKey = this._nodeDetailKey(raw);
    const preserveScroll = Boolean(options.preserveScroll);
    const prevScrollTop = preserveScroll ? this._detailEl.scrollTop : 0;
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
      toolCalls.forEach((tc, index) => {
        const d = this._renderStepToolCall(tc);
        const disclosureKey = `detail:${nodeId}:tool:${tc.id || `${index}:${tc.name}:${tc.start_time || ""}`}`;
        if (d?.tagName === "DETAILS") this._detailDisclosures.wire(d, disclosureKey);
        this._detailToolcalls.appendChild(d);
      });
      document.getElementById("detail-toolcalls-row").style.display = "";
    } else {
      document.getElementById("detail-toolcalls-row").style.display = "none";
    }

    // Conversation transcript is rendered live in the main chat step feed.
    const conversation = raw.type === "step" ? [] : (raw.conversation || []);
    this._detailConversation.innerHTML = "";
    this._detailConversationCount.textContent = conversation.length;
    if (conversation.length) {
      conversation.forEach((evt, index) => {
        const d = this._renderStepConversationEvent(evt);
        if (d?.tagName === "DETAILS") {
          this._detailDisclosures.wire(d, `detail:${nodeId}:conversation:${index}:${evt.timestamp || ""}:${evt.type || ""}`);
        }
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
      this._detailEl.scrollTop = prevScrollTop;
    }
  }

  _hideDetail() {
    this._activeDetailNodeId = null;
    this._detailRenderKey = null;
    this._detailEl.classList.add("hidden");
    this._syncPanelResizerVisibility();
  }

  notifyLayoutChanged() {
    if (!this._network) return;
    const size = this._resizeSurface();
    if (size) {
      // vis-network's automatic resize observer can trail a CSS transition by
      // a frame. Resize its canvas explicitly so it never paints below the
      // adjacent Remote Jobs pane while the pane is moving.
      this._network.setSize(size.width, size.height);
      return;
    }
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
    this._updatePreservingReadingPosition = dependencies.updatePreservingReadingPosition;
    this._createAgentAvatarEl = dependencies.createAgentAvatarEl;
    this._stepFeedTitle = dependencies.stepFeedTitle;
    this._formatStepDuration = dependencies.formatStepDuration;
    this._renderStepInput = dependencies.renderStepInput;
    this._renderStepConversationEvent = dependencies.renderStepConversationEvent;
    this._renderStepToolCall = dependencies.renderStepToolCall;
    this._requestStepCancellation = dependencies.requestStepCancellation;
    this._createArtifactListItem = dependencies.createArtifactListItem;
    this._cards = new Map();
    this._disclosures = dependencies.disclosureController;
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts = new Map();
    this._stepById = new Map();
    this._childNodes = new Map();
    this._elapsedTimer = null;
  }

  reset({ preserveDisclosures = false } = {}) {
    this._stopElapsedTimer();
    // `_cards` is the ownership registry, not merely a render cache. Never
    // forget a card while leaving its DOM node behind: a later graph replay
    // would otherwise create a second bubble for the same graph node.
    for (const card of new Set(this._cards.values())) card.remove();
    this._cards.clear();
    if (!preserveDisclosures) this._disclosures.clear();
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts.clear();
    this._stepById = new Map();
    this._childNodes = new Map();
  }

  captureDisclosureState() {
    this._disclosures.capture(this._chatArea);
  }

  startLiveTurn(anchorEl, startedAt = Date.now(), hostEl = null) {
    this._liveAnchorEl = anchorEl || null;
    this._liveStartedAt = startedAt;
    this._liveContainerEl = document.createElement("div");
    this._rootHosts.clear();

    // `hostEl` is the message timeline, used only to recover already-rendered
    // invocation slots. A graph node without a launcher slot stays detached;
    // guessing a visible fallback position is what made tasks jump later.
    hostEl?.querySelectorAll?.(".delegation-task-host[data-step-execution-key]").forEach((host) => {
      if (host.dataset.stepExecutionKey) this._rootHosts.set(host.dataset.stepExecutionKey, host);
    });

    return this._liveContainerEl;
  }

  resumeLiveTurn(hostEl, startedAt = Date.now()) {
    if (!hostEl) return;
    this._liveAnchorEl = null;
    this._liveStartedAt = startedAt;
    this._liveContainerEl = document.createElement("div");
    this._rootHosts.clear();
    hostEl.querySelectorAll?.(".delegation-task-host[data-step-execution-key]").forEach((host) => {
      if (host.dataset.stepExecutionKey) this._rootHosts.set(host.dataset.stepExecutionKey, host);
    });
    hostEl.querySelectorAll?.(".step-feed-message[data-step-node-id]").forEach((card) => {
      if (card._stepNode) this._cards.set(card.dataset.stepNodeId, card);
    });
    this._syncElapsedTimer();
  }

  bindRootHost(hostEl, executionKey = "") {
    const key = String(executionKey || "");
    if (!hostEl || !key) return false;
    hostEl.dataset.stepExecutionKey = key;
    this._rootHosts.set(key, hostEl);
    const node = [...this._stepById.values()].find((candidate) => (
      this._nodeExecutionKey(candidate) === key || String(candidate.id || "").endsWith(`__node_${key}`)
    ));
    const card = node && this._cards.get(node.id);
    if (node && card) this._insertIntoLiveContainer(hostEl, card, node);
    return true;
  }

  finishLiveTurn() {
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts.clear();
  }

  update(graphData, patch = {}) {
    if (!graphData || typeof graphData.nodes !== "object") return;
    const hasLiveDestination = Boolean(this._liveStartedAt);
    // Rebuild the small hierarchy index for every graph snapshot, including
    // deltas. Parent nodes and children often arrive in separate updates; the
    // former incremental path updated node values but not topology, leaving a
    // child rendered once as a root and again beneath its eventual parent.
    const steps = Object.values(graphData.nodes)
      .filter((node) => node.type === "step")
      .filter((node) => !hasLiveDestination || this._isLiveStep(node))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });
    this.setHierarchy(steps);
    const rootSteps = steps.filter((node) => this.isRootStep(node));

    this._updatePreservingReadingPosition(() => {
      rootSteps.forEach((node) => this._upsert(node));
    });
    this._syncElapsedTimer();
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

  _nodeExecutionKey(node) {
    const input = node?.input || {};
    return String(input.node_id || input.step_id || node?.id || "");
  }

  _rootHostForNode(node) {
    const directKey = this._nodeExecutionKey(node);
    let host = this._rootHosts.get(directKey);
    if (!host && node?.id) {
      const matchingKey = [...this._rootHosts.keys()].find((key) => String(node.id).endsWith(`__node_${key}`));
      if (matchingKey) host = this._rootHosts.get(matchingKey);
    }
    return host?.isConnected ? host : null;
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
    const outer = this._ensureCard(node);
    // Placement is idempotent and is part of reconciliation, not creation.
    // A node can become a child (or a root) after an incremental graph update;
    // checking only DOM presence left it in its former host indefinitely.
    this._placeCard(outer, node);
    this._renderCardIfChanged(outer, node);
  }

  appendStatic(node, container) {
    if (!container) {
      console.warn("StepExecutionFeed received a static card without a delegated-task host.");
      return null;
    }
    const outer = this._ensureCard(node);
    outer.classList.remove("step-feed-child-message");
    this._insertIntoLiveContainer(container, outer, node);
    this._renderCardIfChanged(outer, node);
    this._syncElapsedTimer();
    return outer;
  }

  _placeCard(outer, node) {
    outer.classList.remove("step-feed-child-message");
    const rootHost = this._rootHostForNode(node);
    if (rootHost) {
      this._insertIntoLiveContainer(rootHost, outer, node);
      return;
    }
    if (this._isSending() && this._liveContainerEl) {
      // This holding element is deliberately detached. The function-call
      // event will bind the node to its permanent chronological slot.
      this._insertIntoLiveContainer(this._liveContainerEl, outer, node);
      return;
    }

    // A reconnect snapshot can restore cards into an inline region before
    // graph polling resumes. Retain that restored in-bubble host instead of
    // moving the card back to the chat root when its position changes.
    const existingHost = outer.parentElement;
    if (existingHost && existingHost !== this._chatArea && this._chatArea.contains(existingHost)) {
      this._insertIntoLiveContainer(existingHost, outer, node);
      return;
    }
    // A step card has no valid presentation at the chat root.  Historical
    // rendering supplies an inline host, while a live turn supplies its
    // dedicated Delegated tasks host above.  Keeping an orphan detached is
    // preferable to silently rendering it as a sibling of the chat bubble.
  }

  _stepSortTime(node) {
    return node?.start_time ? new Date(node.start_time).getTime() : Infinity;
  }

  _upsertNested(node, container, ancestors) {
    const outer = this._ensureCard(node);
    outer.classList.add("step-feed-child-message");
    this._insertIntoLiveContainer(container, outer, node);
    this._renderCardIfChanged(outer, node, ancestors);
    return outer;
  }

  _ensureCard(node) {
    const nodeId = String(node?.id || "");
    let outer = this._cards.get(nodeId) || null;
    if (!outer) outer = this._createCard(node);
    this._cards.set(nodeId, outer);
    return outer;
  }

  _renderKey(node, ancestors = new Set([node.id])) {
    const children = (this._childNodes.get(node.id) || [])
      .filter((child) => !ancestors.has(child.id))
      .map((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        return this._renderKey(child, nextAncestors);
      });
    return JSON.stringify({
      status: node.status,
      startTime: node.start_time,
      endTime: node.end_time,
      summary: node.summary,
      input: node.input,
      conversation: node.conversation,
      toolCalls: node.tool_calls,
      artifacts: node.artifacts,
      children,
    });
  }

  _renderCardIfChanged(outer, node, ancestors = new Set([node.id])) {
    const renderKey = this._renderKey(node, ancestors);
    if (outer._stepRenderKey === renderKey) return;
    outer._stepRenderKey = renderKey;
    this._renderCard(outer, node, ancestors);
  }

  _insertIntoLiveContainer(container, outer, node) {
    const delegationGroup = container.closest?.(".delegation-group");
    if (delegationGroup) {
      delegationGroup.hidden = false;
      delegationGroup.closest(".agent-message")?.classList.remove("is-pending", "is-waiting");
    }
    const newTime = this._stepSortTime(node);
    outer.dataset.stepStartTime = String(newTime);

    let followingCard = null;
    for (const el of [...container.children]) {
      if (el === outer) continue;
      if (!el.dataset.stepStartTime) continue;
      if (newTime < Number(el.dataset.stepStartTime)) {
        followingCard = el;
        break;
      }
    }
    if (followingCard) {
      // Avoid reinserting a card that is already in its sorted position. In a
      // live message this method runs with every text render; needless DOM
      // moves restart the card's entry animation and look like a flash.
      if (outer.parentElement !== container || outer.nextElementSibling !== followingCard) {
        container.insertBefore(outer, followingCard);
      }
      return;
    }

    // The card is already correctly placed after all earlier cards. Leave it
    // alone so streamed assistant prose does not repeatedly remount it.
    if (outer.parentElement !== container) container.appendChild(outer);
  }

  _createCard(node) {
    const outer = document.createElement("div");
    outer.className = "message agent-message step-feed-message is-entering";
    const clearEntryAnimation = (event) => {
      if (event.target !== outer) return;
      outer.classList.remove("is-entering");
      outer.removeEventListener("animationend", clearEntryAnimation);
    };
    outer.addEventListener("animationend", clearEntryAnimation);
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStartTime = node.start_time ? String(new Date(node.start_time).getTime()) : "";
    outer.appendChild(this._createAgentAvatarEl());

    const bubble = document.createElement("div");
    bubble.className = "message-bubble step-feed-bubble";
    const details = document.createElement("details");
    details.className = "step-feed-details";
    this._disclosures.wire(details, `step:${node.id}:card`, {
      defaultOpen: false,
    });
    bubble.appendChild(details);
    outer.appendChild(bubble);
    return outer;
  }

  _wireNested(nodeId, key, element) {
    if (element?.tagName !== "DETAILS") return element;
    element.dataset.stepNestedKey = key;
    this._disclosures.wire(element, `step:${nodeId}:nested:${key}`);
    return element;
  }

  _renderCard(outer, node, ancestors = new Set([node.id])) {
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStatus = node.status || "idle";
    outer._stepNode = node;
    outer.classList.toggle("step-feed-highlight", this._highlightedId === node.id);

    const bubble = outer.querySelector(".step-feed-bubble");

    const details = outer.querySelector(".step-feed-details");
    const cardKey = `step:${node.id}:card`;
    const isRunning = node.status === "running";
    if (!isRunning) this._disclosures.state.delete(cardKey);
    const userChoice = this._disclosures.state.get(cardKey);
    // Start compact even while work is live. A reader can explicitly open a
    // card for its activity stream; completed cards always compact again.
    details.open = isRunning && userChoice === true;
    details.innerHTML = "";

    const summary = document.createElement("summary");
    summary.className = "step-feed-summary";
    const titleInfo = this._stepFeedTitle(node);
    const title = document.createElement("span");
    title.className = "step-feed-title";
    const task = document.createElement("span");
    task.className = "step-feed-task";
    task.textContent = titleInfo.action;
    title.appendChild(task);
    if (titleInfo.identifier) {
      const identity = document.createElement("span");
      identity.className = "step-feed-identity";
      identity.textContent = `Sub-agent · ${titleInfo.identifier}`;
      title.appendChild(identity);
    }
    const status = document.createElement("span");
    status.className = `step-feed-status step-feed-status-${node.status || "idle"}`;
    status.textContent = ["failed", "cancelled", "blocked"].includes(node.status) ? "!" : node.status === "running" ? "◌" : "✓";
    status.title = node.status || "idle";
    const meta = document.createElement("span");
    meta.className = "step-feed-meta";
    meta.textContent = this._formatStepDuration(node);
    if (isRunning && node.start_time) meta.title = "Elapsed time";
    summary.append(status, title, meta);

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
      let section = bubble?.querySelector(":scope > .step-feed-child-section");
      if (!section) {
        section = document.createElement("div");
        section.className = "step-feed-section step-feed-child-section";
        const label = document.createElement("div");
        label.className = "step-feed-section-title";
        const childHost = document.createElement("div");
        childHost.className = "step-feed-child-list";
        section.append(label, childHost);
        bubble?.appendChild(section);
      }
      const label = section.querySelector(":scope > .step-feed-section-title");
      label.textContent = `Sub-executors (${childNodes.length})`;
      const childHost = section.querySelector(":scope > .step-feed-child-list");

      childNodes.forEach((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        this._upsertNested(child, childHost, nextAncestors);
      });
    } else {
      bubble?.querySelector(":scope > .step-feed-child-section")?.remove();
    }

    const activityItems = this._activityStream(node);
    if (activityItems.length) {
      const activity = document.createElement("div");
      activity.className = "step-feed-activity-list agent-activity-action-list";
      activityItems.forEach((item) => {
        if (item.kind === "conversation") {
          const { event, index } = item;
          const key = `conversation:${index}:${event.timestamp || ""}:${event.type || ""}:${event.author || ""}`;
          activity.appendChild(this._wireNested(node.id, key, this._renderStepConversationEvent(event, {
            collapsed: node.status !== "running",
            timelineId: `step:${node.id}:${key}`,
          })));
          return;
        }
        const { toolCall, index } = item;
        const key = `tool:${index}:${toolCall.name || ""}:${toolCall.start_time || ""}`;
        activity.appendChild(this._wireNested(node.id, key, this._renderStepToolCall(toolCall)));
      });
      body.appendChild(activity);
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

  _syncElapsedTimer() {
    const hasTimedRunningCard = [...this._cards.values()].some((outer) => (
      outer.isConnected
      && outer.dataset.stepStatus === "running"
      && Number.isFinite(new Date(outer._stepNode?.start_time || "").getTime())
    ));

    if (hasTimedRunningCard) {
      this._refreshRunningDurations();
      if (this._elapsedTimer === null) {
        this._elapsedTimer = window.setInterval(() => this._refreshRunningDurations(), 1000);
      }
      return;
    }
    this._stopElapsedTimer();
  }

  _refreshRunningDurations() {
    for (const outer of this._cards.values()) {
      if (!outer.isConnected || outer.dataset.stepStatus !== "running") continue;
      const node = outer._stepNode;
      if (!node?.start_time) continue;
      const meta = outer.querySelector(".step-feed-meta");
      if (meta) meta.textContent = this._formatStepDuration(node);
    }
  }

  _stopElapsedTimer() {
    if (this._elapsedTimer === null) return;
    window.clearInterval(this._elapsedTimer);
    this._elapsedTimer = null;
  }

  _activityStream(node) {
    const toolCalls = node.tool_calls || [];
    const toolMatchesConversationEvent = (event) => {
      if (!["function_call", "function_response"].includes(event.type)) return false;
      const content = String(event.content || "");
      return toolCalls.some((toolCall) => {
        const name = toolCall.name || "";
        return name && (content.startsWith(`${name}(`) || content.startsWith(`${name} →`));
      });
    };
    const timeValue = (value) => {
      const time = new Date(value || "").getTime();
      return Number.isFinite(time) ? time : null;
    };
    const items = [
      ...(node.conversation || [])
        .filter((event) => !toolMatchesConversationEvent(event))
        .map((event, index) => ({ kind: "conversation", event, index, time: timeValue(event.timestamp), sequence: index })),
      ...toolCalls.map((toolCall, index) => ({
        kind: "tool",
        toolCall,
        index,
        time: timeValue(toolCall.start_time || toolCall.end_time),
        sequence: (node.conversation || []).length + index,
      })),
    ];
    return items.sort((a, b) => {
      if (a.time !== null && b.time !== null && a.time !== b.time) return a.time - b.time;
      if (a.time !== null && b.time === null) return -1;
      if (a.time === null && b.time !== null) return 1;
      return a.sequence - b.sequence;
    });
  }
}

// ---------------------------------------------------------------------------
// Execution Plan Graph (floating popup in chat column)
// ---------------------------------------------------------------------------
