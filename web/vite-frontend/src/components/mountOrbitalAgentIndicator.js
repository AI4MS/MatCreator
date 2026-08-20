import { createOrbitalAgentIndicator } from "./OrbitalAgentIndicator.js";
import "./OrbitalAgentIndicator.css";

// Keep the original bridge API so the vanilla chat shell does not need to know
// how the SVG indicator is created or animated.
export function mountOrbitalAgentIndicator(target) {
  if (!target) return null;
  return createOrbitalAgentIndicator(target, {
    size: 18,
    color: "var(--accent)",
    title: (state) => `MatCreator is ${state}`,
  });
}
