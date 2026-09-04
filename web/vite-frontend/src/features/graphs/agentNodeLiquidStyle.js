export const DEFAULT_AGENT_DROPLET_FILL_ALPHA = 0.07;

const FIXED_BODY_ALPHAS = Object.freeze({
  highlight: 0.18,
  sheen: 0.04,
  rim: 0.11,
});

const FIXED_OPTIC_ALPHAS = Object.freeze({
  spectrum: 0.2,
  caustic: 0.18,
  innerRim: 0.64,
  outerGlow: 0.34,
  specular: 0.72,
});

function finiteUnitAlpha(value) {
  if (typeof value === "string" && value.trim() === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 && numeric <= 1
    ? numeric
    : null;
}

export function resolveAgentDropletFillAlpha(value) {
  return finiteUnitAlpha(value) ?? DEFAULT_AGENT_DROPLET_FILL_ALPHA;
}

export function agentDropletBodyAlphas(fillAlpha, stateAlpha = 1) {
  const state = finiteUnitAlpha(stateAlpha) ?? 1;
  return {
    highlight: FIXED_BODY_ALPHAS.highlight * state,
    sheen: FIXED_BODY_ALPHAS.sheen * state,
    fill: resolveAgentDropletFillAlpha(fillAlpha) * state,
    rim: FIXED_BODY_ALPHAS.rim * state,
  };
}

/**
 * Optical layers are deliberately independent from lifecycle colour.  The
 * Rack Lab droplet keeps the node type/status contract while a small hover or
 * selection emphasis only makes its glass highlights easier to read.
 */
export function agentDropletOpticAlphas(stateAlpha = 1, emphasis = 0) {
  const state = finiteUnitAlpha(stateAlpha) ?? 1;
  const focus = Math.max(0, Math.min(1, Number(emphasis) || 0));
  const boost = 1 + focus * 0.2;
  return Object.fromEntries(
    Object.entries(FIXED_OPTIC_ALPHAS).map(([key, alpha]) => [
      key,
      Math.min(1, alpha * state * boost),
    ]),
  );
}
