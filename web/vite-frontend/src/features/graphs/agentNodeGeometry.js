import { resolveAgentGraphRecipe } from "./agentGraphRecipes.js";

export const AGENT_NODE_SHAPE = Object.freeze({
  CIRCLE: "circle",
  DROPLET: "droplet",
});

export function agentNodeShapeForRecipe(styleRecipeId, styleRecipeVersion) {
  return resolveAgentGraphRecipe(styleRecipeId, styleRecipeVersion).nodeShape;
}

function safeRadius(radius) {
  const value = Number(radius);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

const TAU = Math.PI * 2;
const NEUTRAL_DROPLET_MOTION = Object.freeze({
  scaleX: 1,
  scaleY: 1,
  shear: 0,
  lobe: 0,
  curl: 0,
  highlightX: 0,
  highlightY: 0,
  touchAngle: 0,
  touchDepth: 0,
  touchX: 0,
  touchY: 0,
});

function stableUnit(value) {
  const text = String(value ?? "node");
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967296;
}

function clampUnit(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

export function runningMotionEnvelope(status, transition = null) {
  const progress = clampUnit(transition?.progress);
  const eased = progress * progress * (3 - 2 * progress);
  if (transition?.to === "running") return eased;
  if (transition?.from === "running") return 1 - eased;
  return status === "running" ? 1 : 0;
}

/**
 * Returns a deterministic, low-amplitude liquid deformation for one node.
 * The primary stretch is area-balanced; two slower harmonics only nudge
 * individual lobes, so a running node looks alive without changing graph
 * layout or jittering its visual centre.
 */
export function dropletMotionForNode(
  nodeId,
  timeMs,
  {
    active = false,
    hover = false,
    strength = null,
    touch = null,
  } = {},
) {
  const hasExplicitStrength = strength !== null
    && strength !== undefined
    && Number.isFinite(Number(strength));
  const intensity = hasExplicitStrength
    ? clampUnit(strength)
    : active ? 1 : hover ? 0.34 : 0;
  const touchDepth = clampUnit(touch?.strength);
  if (intensity === 0 && touchDepth === 0) {
    return NEUTRAL_DROPLET_MOTION;
  }

  const seed = stableUnit(nodeId);
  const safeTime = Number.isFinite(Number(timeMs)) ? Number(timeMs) : 0;
  const seedAngle = seed * TAU;
  const period = 2840 + seed * 620;
  const phase = active ? (safeTime / period) * TAU + seedAngle : seedAngle + 0.72;
  const breath = Math.sin(phase);
  const lobeWave = Math.sin(phase * 1.37 + seedAngle * 0.63);
  const curlWave = Math.sin(phase * 0.79 - seedAngle * 0.41 + 1.1);
  const stretch = (breath * 0.058 + lobeWave * 0.014) * intensity;
  const scaleX = 1 + stretch;
  const touchX = Number.isFinite(Number(touch?.x)) ? Number(touch.x) : 0;
  const touchY = Number.isFinite(Number(touch?.y)) ? Number(touch.y) : 0;
  const touchAngle = Math.hypot(touchX, touchY) > 0.08
    ? Math.atan2(touchY, touchX)
    : seedAngle;

  return {
    scaleX,
    scaleY: 1 / scaleX,
    shear: curlWave * 0.045 * intensity,
    lobe: lobeWave * 0.09 * intensity,
    curl: (curlWave * 0.068 + breath * 0.02) * intensity,
    highlightX: (lobeWave * 0.048 + curlWave * 0.018) * intensity,
    highlightY: (breath * 0.042 - curlWave * 0.016) * intensity,
    touchAngle,
    touchDepth,
    touchX,
    touchY,
  };
}

/**
 * MC-owned organic droplet geometry. Coordinates are relative to the node
 * anchor so Canvas rendering, hit dimensions, badges, and routed graph edges
 * share one contract without importing third-party demo path data.
 */
export function dropletGeometry(radius, motion = null) {
  const r = safeRadius(radius);
  const {
    scaleX = 1,
    scaleY = 1,
    shear = 0,
    lobe = 0,
    curl = 0,
    touchAngle = 0,
    touchDepth = 0,
  } = motion || {};
  const point = (x, y, lobeWeight = 0, curlWeight = 0) => {
    const angle = Math.atan2(y, x);
    const angularDelta = Math.atan2(
      Math.sin(angle - touchAngle),
      Math.cos(angle - touchAngle),
    );
    const contact = Math.exp(-((angularDelta / 0.29) ** 2) / 2) * touchDepth;
    const shoulderDistance = Math.abs(angularDelta) - 0.52;
    const shoulder = Math.exp(-((shoulderDistance / 0.23) ** 2) / 2) * touchDepth;
    const radialScale = 1 - contact * 0.32 + shoulder * 0.1;
    const deformedX = x * radialScale;
    const deformedY = y * radialScale;
    return {
      x: (deformedX * scaleX + deformedY * shear + lobe * lobeWeight) * r,
      y: (deformedY * scaleY + curl * curlWeight) * r,
    };
  };
  const start = point(-0.14, -1.0, 0.14, -0.38);
  const curves = [
    {
      cp1: point(0.34, -1.08, 0.3, -0.24),
      cp2: point(0.91, -0.66, 0.52, -0.04),
      end: point(0.91, -0.06, 0.58, 0.12),
    },
    {
      cp1: point(0.98, 0.48, 0.34, 0.32),
      cp2: point(0.56, 1.02, 0.06, 0.52),
      end: point(0.02, 0.98, -0.16, 0.45),
    },
    {
      cp1: point(-0.58, 1.08, -0.38, 0.28),
      cp2: point(-1.02, 0.6, -0.56, 0.12),
      end: point(-0.95, 0.04, -0.6, -0.08),
    },
    {
      cp1: point(-0.9, -0.5, -0.34, -0.3),
      cp2: point(-0.6, -0.94, -0.08, -0.48),
      end: start,
    },
  ];
  const pathPoints = [start, ...curves.flatMap(({ cp1, cp2, end }) => [cp1, cp2, end])];
  return {
    start,
    curves,
    bounds: {
      left: Math.min(...pathPoints.map(({ x }) => x)),
      right: Math.max(...pathPoints.map(({ x }) => x)),
      top: Math.min(...pathPoints.map(({ y }) => y)),
      bottom: Math.max(...pathPoints.map(({ y }) => y)),
    },
    opticalCenterY: (0.035 + curl * 0.1) * r,
    statusAnchor: point(0.68, -0.62, 0.34, -0.18),
  };
}

export function traceDropletPath(ctx, x, y, radius, motion = null) {
  const geometry = dropletGeometry(radius, motion);
  ctx.beginPath();
  ctx.moveTo(x + geometry.start.x, y + geometry.start.y);
  geometry.curves.forEach(({ cp1, cp2, end }) => {
    ctx.bezierCurveTo(
      x + cp1.x,
      y + cp1.y,
      x + cp2.x,
      y + cp2.y,
      x + end.x,
      y + end.y,
    );
  });
  ctx.closePath();
  return geometry;
}

export function nodeShapeDimensions(radius, shape, padding = 7) {
  const r = safeRadius(radius);
  const safePadding = Math.max(0, Number(padding) || 0);
  if (shape !== AGENT_NODE_SHAPE.DROPLET) {
    const size = (r + safePadding) * 2;
    return { width: size, height: size };
  }
  const { bounds } = dropletGeometry(r);
  return {
    width: (Math.max(Math.abs(bounds.left), bounds.right) + safePadding) * 2,
    height: (Math.max(Math.abs(bounds.top), bounds.bottom) + safePadding) * 2,
  };
}

export function nodeShapeVerticalExtent(radius, shape, direction) {
  const r = safeRadius(radius);
  if (shape !== AGENT_NODE_SHAPE.DROPLET) return r;
  const { bounds } = dropletGeometry(r);
  return direction === "top" ? Math.abs(bounds.top) : bounds.bottom;
}
