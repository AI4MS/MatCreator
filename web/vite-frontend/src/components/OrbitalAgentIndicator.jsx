import React, { useEffect, useId, useRef, useState } from "react";
import "./OrbitalAgentIndicator.css";

// Every orbital uses eight cubic Bezier segments in the same order. Keeping the
// path commands compatible lets the browser interpolate `d` smoothly in SMIL,
// without a JavaScript animation loop or a path-morphing dependency.
const ORBITALS = {
  // A rounded, isotropic s orbital: no nodal waist or directional lobe.
  s: "M 50 10 C 61 10 71 16 78 25 C 85 34 88 42 88 50 C 88 58 85 66 78 75 C 71 84 61 90 50 90 C 39 90 29 84 22 75 C 15 66 12 58 12 50 C 12 42 15 34 22 25 C 29 16 39 10 50 10 Z",
  // px: an exact two-lobed figure-eight. The first four Beziers trace the
  // right lobe and the next four trace its horizontal mirror; both meet only
  // at the central node (50, 50), making the nodal waist unmistakable.
  p: "M 50 50 C 50 36 57 20 70 20 C 83 20 90 34 90 50 C 90 66 83 80 70 80 C 57 80 50 64 50 50 C 50 64 43 80 30 80 C 17 80 10 66 10 50 C 10 34 17 20 30 20 C 43 20 50 36 50 50 Z",
  // Four broad, equal d-orbital leaves. Each pair of curves leaves and returns
  // to the central node, so all four lobes remain full at small icon sizes.
  d: "M 50 50 C 35 42 20 22 50 8 C 80 22 65 42 50 50 C 58 35 78 20 92 50 C 78 80 58 65 50 50 C 65 58 80 78 50 92 C 20 78 35 58 50 50 C 42 65 22 80 8 50 C 22 20 42 35 50 50 Z",
};

const ACTIVE_STATES = new Set(["thinking", "searching", "computing"]);
const REST_MIN_MS = 2400;
const REST_MAX_MS = 4000;
const TRANSITION_MS = 560;

// The paths above intentionally share one `M`, eight `C`, and `Z` commands.
// We therefore interpolate their corresponding Bezier coordinates directly,
// producing a genuine intermediate SVG geometry on every transition frame.
function pathNumbers(path) {
  return path.match(/-?\d*\.?\d+/g).map(Number);
}

const PATH_POINTS = Object.fromEntries(
  Object.entries(ORBITALS).map(([name, path]) => [name, pathNumbers(path)]),
);

function serializePath(points) {
  let result = `M ${points[0].toFixed(2)} ${points[1].toFixed(2)}`;
  for (let index = 2; index < points.length; index += 6) {
    result += ` C ${points.slice(index, index + 6).map((value) => value.toFixed(2)).join(" ")}`;
  }
  return `${result} Z`;
}

function interpolatePath(from, to, progress) {
  const fromPoints = PATH_POINTS[from];
  const toPoints = PATH_POINTS[to];
  return serializePath(fromPoints.map((value, index) => value + (toPoints[index] - value) * progress));
}

function excitationEase(progress) {
  // Smooth acceleration/deceleration plus a restrained overshoot before settle.
  const shifted = progress - 1;
  return 1 + 2.1 * shifted ** 3 + 1.1 * shifted ** 2;
}

function chooseNextOrbital(current) {
  const choices = Object.keys(ORBITALS).filter((orbital) => orbital !== current);
  return choices[Math.floor(Math.random() * choices.length)];
}

const THEME_COLORS = {
  cool: "#7dd3fc",
  violet: "#c4b5fd",
  emerald: "#6ee7b7",
};

/**
 * A compact, SVG-only agent-status animation inspired by atomic orbitals.
 *
 * Active states rest on an orbital and periodically select a different s/p/d
 * target. The only per-frame work is a 560 ms coordinate interpolation during
 * an excitation; resting phases remain entirely CSS-driven. `color` accepts
 * any CSS color; `theme` provides compact default palettes.
 */
export function OrbitalAgentIndicator({
  state = "idle",
  size = 32,
  color,
  theme = "cool",
  className = "",
  title = "MatCreator status",
}) {
  const id = useId().replaceAll(":", "");
  const safeState = ["idle", "thinking", "searching", "computing", "done"].includes(state) ? state : "idle";
  // Always begin at the s orbital. The active cycle subsequently chooses among
  // all three states rather than imposing a fixed mode-specific sequence.
  const [orbital, setOrbital] = useState("s");
  const [nextOrbital, setNextOrbital] = useState(null);
  const [displayPath, setDisplayPath] = useState(ORBITALS.s);
  const isInitialDwell = useRef(true);
  const isTransitioning = nextOrbital !== null;

  // Rest on an orbital, then choose a different s/p/d target at random.
  useEffect(() => {
    if (!ACTIVE_STATES.has(safeState) || isTransitioning) return undefined;
    // Ensure the first visible active state is a clearly recognizable s cloud.
    const delay = isInitialDwell.current
      ? REST_MAX_MS
      : REST_MIN_MS + Math.random() * (REST_MAX_MS - REST_MIN_MS);
    const timer = window.setTimeout(() => {
      isInitialDwell.current = false;
      setNextOrbital(chooseNextOrbital(orbital));
    }, delay);
    return () => window.clearTimeout(timer);
  }, [safeState, orbital, nextOrbital, isTransitioning]);

  useEffect(() => {
    if (!nextOrbital) return undefined;
    const start = performance.now();
    let frameId;
    const animate = (now) => {
      const progress = Math.min((now - start) / TRANSITION_MS, 1);
      setDisplayPath(interpolatePath(orbital, nextOrbital, excitationEase(progress)));
      if (progress < 1) frameId = requestAnimationFrame(animate);
      else {
        setDisplayPath(ORBITALS[nextOrbital]);
        setOrbital(nextOrbital);
        setNextOrbital(null);
      }
    };
    frameId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameId);
  }, [orbital, nextOrbital]);

  const orbitalColor = color || THEME_COLORS[theme] || THEME_COLORS.cool;
  const gradientId = `orbital-gradient-${id}`;
  const glowId = `orbital-glow-${id}`;

  return (
    <svg
      className={`orbital-agent-indicator orbital-agent-indicator--${safeState} ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role="img"
      aria-label={title}
      style={{ "--orbital-color": orbitalColor }}
    >
      <title>{title}</title>
      <defs>
        <radialGradient id={gradientId} cx="35%" cy="30%" r="70%">
          <stop offset="0%" stopColor="var(--orbital-color)" stopOpacity="0.56" />
          <stop offset="65%" stopColor="var(--orbital-color)" stopOpacity="0.16" />
          <stop offset="100%" stopColor="var(--orbital-color)" stopOpacity="0.02" />
        </radialGradient>
        <filter id={glowId} x="-35%" y="-35%" width="170%" height="170%">
          <feGaussianBlur stdDeviation="3.5" />
        </filter>
      </defs>

      <g className="orbital-agent-indicator__motion">
        <g transform="translate(50 50)">
          <g className="orbital-agent-indicator__transition">
            {isTransitioning && (
              <animateTransform
                attributeName="transform"
                type="scale"
                values="1;1.055;1"
                keyTimes="0;0.55;1"
                keySplines="0.22 1 0.36 1;0.22 1 0.36 1"
                calcMode="spline"
                dur={`${TRANSITION_MS}ms`}
                repeatCount="1"
              />
            )}
            <g transform="translate(-50 -50)">
              <path
                d={displayPath}
                fill="var(--orbital-color)"
                fillOpacity="0.32"
                filter={`url(#${glowId})`}
                className="orbital-agent-indicator__glow"
              />
              <path d={displayPath} fill={`url(#${gradientId})`} className="orbital-agent-indicator__cloud" />
              <path d={displayPath} fill="none" stroke="var(--orbital-color)" strokeWidth="1.6" strokeOpacity="0.85" />
            </g>
          </g>
        </g>
      </g>
    </svg>
  );
}

export default OrbitalAgentIndicator;
