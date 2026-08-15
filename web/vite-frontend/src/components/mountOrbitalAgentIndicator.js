import React from "react";
import { createRoot } from "react-dom/client";
import OrbitalAgentIndicator from "./OrbitalAgentIndicator.jsx";

// The main chat is vanilla JS. This narrow bridge mounts the reusable React
// indicator without making the rest of the composer depend on React.
export function mountOrbitalAgentIndicator(target) {
  if (!target) return null;
  const root = createRoot(target);
  let renderedState = null;
  const render = (state = "idle") => {
    if (state === renderedState) return;
    renderedState = state;
    root.render(React.createElement(OrbitalAgentIndicator, {
      state,
      size: 18,
      color: "var(--accent)",
      title: `MatCreator is ${state}`,
    }));
  };
  render();
  return { render, unmount: () => root.unmount() };
}
