export const DEFAULT_AGENT_DROPLET_FILL_ALPHA = 0.07;

const FIXED_BODY_ALPHAS = Object.freeze({
  highlight: 0.18,
  sheen: 0.04,
  rim: 0.11,
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
