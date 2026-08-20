const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

// Every orbital uses eight cubic Bezier segments in the same order, which lets
// us interpolate corresponding coordinates without a path-morphing library.
const ORBITALS = {
  s: "M 50 10 C 61 10 71 16 78 25 C 85 34 88 42 88 50 C 88 58 85 66 78 75 C 71 84 61 90 50 90 C 39 90 29 84 22 75 C 15 66 12 58 12 50 C 12 42 15 34 22 25 C 29 16 39 10 50 10 Z",
  p: "M 50 50 C 50 36 57 20 70 20 C 83 20 90 34 90 50 C 90 66 83 80 70 80 C 57 80 50 64 50 50 C 50 64 43 80 30 80 C 17 80 10 66 10 50 C 10 34 17 20 30 20 C 43 20 50 36 50 50 Z",
  d: "M 50 50 C 35 42 20 22 50 8 C 80 22 65 42 50 50 C 58 35 78 20 92 50 C 78 80 58 65 50 50 C 65 58 80 78 50 92 C 20 78 35 58 50 50 C 42 65 22 80 8 50 C 22 20 42 35 50 50 Z",
};

const ACTIVE_STATES = new Set(["thinking", "searching", "computing"]);
const VALID_STATES = new Set(["idle", ...ACTIVE_STATES, "done"]);
const THEME_COLORS = {
  cool: "#7dd3fc",
  violet: "#c4b5fd",
  emerald: "#6ee7b7",
};
const REST_MIN_MS = 2400;
const REST_MAX_MS = 4000;
const TRANSITION_MS = 560;

let indicatorId = 0;

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
  const shifted = progress - 1;
  return 1 + 2.1 * shifted ** 3 + 1.1 * shifted ** 2;
}

function chooseNextOrbital(current) {
  const choices = Object.keys(ORBITALS).filter((orbital) => orbital !== current);
  return choices[Math.floor(Math.random() * choices.length)];
}

function createSvgElement(document, name, attributes = {}) {
  const element = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([attribute, value]) => {
    element.setAttribute(attribute, String(value));
  });
  return element;
}

function createScheduler(view) {
  const setTimer = view?.setTimeout?.bind(view) || globalThis.setTimeout.bind(globalThis);
  const clearTimer = view?.clearTimeout?.bind(view) || globalThis.clearTimeout.bind(globalThis);
  const now = () => view?.performance?.now?.() ?? globalThis.performance.now();
  const requestFrame = view?.requestAnimationFrame?.bind(view)
    || ((callback) => setTimer(() => callback(now()), 16));
  const cancelFrame = view?.cancelAnimationFrame?.bind(view) || clearTimer;
  return { setTimer, clearTimer, now, requestFrame, cancelFrame };
}

function buildIndicatorSvg(document, { size, color, className, title, id }) {
  const svg = createSvgElement(document, "svg", {
    width: size,
    height: size,
    viewBox: "0 0 100 100",
    role: "img",
    "aria-label": title,
  });
  svg.style.setProperty("--orbital-color", color);

  const titleElement = createSvgElement(document, "title");
  titleElement.textContent = title;

  const gradientId = `orbital-gradient-${id}`;
  const glowId = `orbital-glow-${id}`;
  const definitions = createSvgElement(document, "defs");
  const gradient = createSvgElement(document, "radialGradient", {
    id: gradientId,
    cx: "35%",
    cy: "30%",
    r: "70%",
  });
  [
    ["0%", "0.56"],
    ["65%", "0.16"],
    ["100%", "0.02"],
  ].forEach(([offset, opacity]) => {
    gradient.appendChild(createSvgElement(document, "stop", {
      offset,
      "stop-color": "var(--orbital-color)",
      "stop-opacity": opacity,
    }));
  });
  const filter = createSvgElement(document, "filter", {
    id: glowId,
    x: "-35%",
    y: "-35%",
    width: "170%",
    height: "170%",
  });
  filter.appendChild(createSvgElement(document, "feGaussianBlur", { stdDeviation: "3.5" }));
  definitions.append(gradient, filter);

  const motionGroup = createSvgElement(document, "g", { class: "orbital-agent-indicator__motion" });
  const centeredGroup = createSvgElement(document, "g", { transform: "translate(50 50)" });
  const transitionGroup = createSvgElement(document, "g", { class: "orbital-agent-indicator__transition" });
  const pathGroup = createSvgElement(document, "g", { transform: "translate(-50 -50)" });
  const glowPath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: "var(--orbital-color)",
    "fill-opacity": "0.32",
    filter: `url(#${glowId})`,
    class: "orbital-agent-indicator__glow",
  });
  const cloudPath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: `url(#${gradientId})`,
    class: "orbital-agent-indicator__cloud",
  });
  const outlinePath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: "none",
    stroke: "var(--orbital-color)",
    "stroke-width": "1.6",
    "stroke-opacity": "0.85",
  });

  pathGroup.append(glowPath, cloudPath, outlinePath);
  transitionGroup.appendChild(pathGroup);
  centeredGroup.appendChild(transitionGroup);
  motionGroup.appendChild(centeredGroup);
  svg.append(titleElement, definitions, motionGroup);

  return {
    svg,
    titleElement,
    transitionGroup,
    pathGroup,
    paths: [glowPath, cloudPath, outlinePath],
    className,
  };
}

/**
 * Create a compact SVG status indicator without introducing a UI-framework
 * runtime. The returned interface intentionally matches the legacy mount
 * bridge: callers update it with render(state) and release it with unmount().
 */
export function createOrbitalAgentIndicator(target, {
  state = "idle",
  size = 32,
  color,
  theme = "cool",
  className = "",
  title = "MatCreator status",
} = {}) {
  const document = target.ownerDocument || globalThis.document;
  const scheduler = createScheduler(document.defaultView);
  const orbitalColor = color || THEME_COLORS[theme] || THEME_COLORS.cool;
  const titleForState = typeof title === "function" ? title : () => title;
  const elements = buildIndicatorSvg(document, {
    size,
    color: orbitalColor,
    className,
    title: titleForState(state),
    id: ++indicatorId,
  });

  let renderedState;
  let safeState;
  let orbital = "s";
  let nextOrbital = null;
  let isInitialDwell = true;
  let restTimer = null;
  let frameId = null;
  let transitionAnimation = null;
  let destroyed = false;

  function clearRestTimer() {
    if (restTimer === null) return;
    scheduler.clearTimer(restTimer);
    restTimer = null;
  }

  function setDisplayPath(path) {
    elements.paths.forEach((element) => element.setAttribute("d", path));
  }

  function removeTransitionAnimation() {
    transitionAnimation?.remove();
    transitionAnimation = null;
  }

  function scheduleRest() {
    clearRestTimer();
    if (destroyed || nextOrbital || !ACTIVE_STATES.has(safeState)) return;
    const delay = isInitialDwell
      ? REST_MAX_MS
      : REST_MIN_MS + Math.random() * (REST_MAX_MS - REST_MIN_MS);
    restTimer = scheduler.setTimer(startTransition, delay);
  }

  function startTransition() {
    restTimer = null;
    isInitialDwell = false;
    nextOrbital = chooseNextOrbital(orbital);
    transitionAnimation = createSvgElement(document, "animateTransform", {
      attributeName: "transform",
      type: "scale",
      values: "1;1.055;1",
      keyTimes: "0;0.55;1",
      keySplines: "0.22 1 0.36 1;0.22 1 0.36 1",
      calcMode: "spline",
      dur: `${TRANSITION_MS}ms`,
      repeatCount: "1",
    });
    elements.transitionGroup.insertBefore(transitionAnimation, elements.pathGroup);

    const fromOrbital = orbital;
    const toOrbital = nextOrbital;
    const startedAt = scheduler.now();
    const animate = (now) => {
      const progress = Math.min((now - startedAt) / TRANSITION_MS, 1);
      setDisplayPath(interpolatePath(fromOrbital, toOrbital, excitationEase(progress)));
      if (progress < 1) {
        frameId = scheduler.requestFrame(animate);
        return;
      }
      frameId = null;
      setDisplayPath(ORBITALS[toOrbital]);
      orbital = toOrbital;
      nextOrbital = null;
      removeTransitionAnimation();
      scheduleRest();
    };
    frameId = scheduler.requestFrame(animate);
  }

  function render(nextState = "idle") {
    if (destroyed || nextState === renderedState) return;
    renderedState = nextState;

    const nextSafeState = VALID_STATES.has(nextState) ? nextState : "idle";
    const stateChanged = nextSafeState !== safeState;
    safeState = nextSafeState;
    elements.svg.setAttribute(
      "class",
      `orbital-agent-indicator orbital-agent-indicator--${safeState} ${elements.className}`.trim(),
    );
    const nextTitle = String(titleForState(nextState));
    elements.svg.setAttribute("aria-label", nextTitle);
    elements.titleElement.textContent = nextTitle;

    if (stateChanged) scheduleRest();
  }

  function unmount() {
    if (destroyed) return;
    destroyed = true;
    clearRestTimer();
    if (frameId !== null) scheduler.cancelFrame(frameId);
    frameId = null;
    removeTransitionAnimation();
    elements.svg.remove();
  }

  target.replaceChildren(elements.svg);
  render(state);
  return { render, unmount };
}
