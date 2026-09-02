function finiteUnitAlpha(value) {
  if (typeof value === "string" && value.trim() === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 && numeric <= 1
    ? numeric
    : null;
}

export function resolveAgentDropletFillAlpha(value, liquidStyle) {
  return finiteUnitAlpha(value) ?? liquidStyle.defaultFillAlpha;
}

export function agentDropletBodyAlphas(fillAlpha, liquidStyle, stateAlpha = 1) {
  const state = finiteUnitAlpha(stateAlpha) ?? 1;
  return {
    underlay: liquidStyle.underlayAlpha * state,
    highlight: liquidStyle.highlightAlpha * state,
    sheen: liquidStyle.sheenAlpha * state,
    fill: resolveAgentDropletFillAlpha(fillAlpha, liquidStyle) * state,
    rim: liquidStyle.rimAlpha * state,
  };
}
