const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const TAU = Math.PI * 2;

// Every orbital comes from one weighted polar equation:
// r(theta) = radius * (base + twoLobe*|cos(theta)| + fourLobe*|cos(2theta)|).
// The p/d presets are two/four-petal rose curves. They meet at the centre but
// retain horizontal and vertical reflection symmetry, unlike the old pinwheel.
const ORBITAL_PARAMETERS = Object.freeze({
  s: Object.freeze({ radius: 40, twoLobe: 0, fourLobe: 0, fourLobeBroadening: 0 }),
  p: Object.freeze({ radius: 42, twoLobe: 1, fourLobe: 0, fourLobeBroadening: 0 }),
  // This fills out each d-orbital leaf without moving its centre crossing or
  // outer tips. Keep it local to d so s/p stay unchanged.
  d: Object.freeze({ radius: 42, twoLobe: 0, fourLobe: 1, fourLobeBroadening: 0.65 }),
});
const CURVE_SEGMENTS = 16;

const ACTIVE_STATES = new Set(["thinking", "searching", "computing"]);
const VALID_STATES = new Set(["idle", ...ACTIVE_STATES, "done"]);
const THEME_COLORS = {
  cool: "#38bdf8",
  violet: "#a78bfa",
  emerald: "#34d399",
};

// The complete choreography is driven from these parameters and one clock.
// Changing the timings or amplitudes here keeps outline and density in phase.
const MOTION = Object.freeze({
  dwellMin: 2600,
  dwellMax: 4200,
  preTransition: 720,
  morph: 440,
  breathePeriod: 2300,
  breatheScale: 0.055,
  densityPeriod: 1850,
  jitterAmplitude: 2.35,
  rippleScale: 0.15,
});

const STATE_TEMPO = {
  thinking: 1,
  searching: 1.18,
  computing: 1.34,
};

let indicatorId = 0;

function clamp(value, min = 0, max = 1) {
  return Math.min(Math.max(value, min), max);
}

function smoothstep(progress) {
  const value = clamp(progress);
  return value * value * (3 - 2 * value);
}

function smootherstep(progress) {
  const value = clamp(progress);
  return value ** 3 * (value * (value * 6 - 15) + 10);
}

function interpolateParameters(from, to, progress) {
  const eased = smootherstep(progress);
  const fromParameters = ORBITAL_PARAMETERS[from];
  const toParameters = ORBITAL_PARAMETERS[to];
  return Object.fromEntries(Object.keys(fromParameters).map((name) => [
    name,
    fromParameters[name] + (toParameters[name] - fromParameters[name]) * eased,
  ]));
}

function orbitalPoint(parameters, angle, disturbance = null) {
  const { radius, twoLobe, fourLobe, fourLobeBroadening } = parameters;
  const base = 1 - twoLobe - fourLobe;
  const cos1 = Math.cos(angle);
  const cos2 = Math.cos(2 * angle);
  const absoluteCos2 = Math.abs(cos2);
  const fourLobeShape = absoluteCos2 * (
    1 + fourLobeBroadening * (1 - absoluteCos2)
  );
  const shapeDerivative = (
    -twoLobe * Math.sin(angle) * Math.sign(cos1)
    -2 * fourLobe * Math.sin(2 * angle) * Math.sign(cos2)
      * (1 + fourLobeBroadening * (1 - 2 * absoluteCos2))
  );
  const shape = base + twoLobe * Math.abs(cos1) + fourLobe * fourLobeShape;
  let radialDistance = radius * shape;
  let radialDerivative = radius * shapeDerivative;

  if (disturbance) {
    const { amplitude, phase } = disturbance;
    const wave = (
      Math.cos(4 * angle) * Math.sin(phase * 7 + 0.65)
      + 0.42 * Math.cos(8 * angle) * Math.sin(phase * 11 - 0.35)
    );
    const waveDerivative = (
      -4 * Math.sin(4 * angle) * Math.sin(phase * 7 + 0.65)
      -3.36 * Math.sin(8 * angle) * Math.sin(phase * 11 - 0.35)
    );
    radialDistance += amplitude * shape * wave;
    radialDerivative += amplitude * (shapeDerivative * wave + shape * waveDerivative);
  }

  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  return {
    x: 50 + radialDistance * cos,
    y: 50 + radialDistance * sin,
    dx: radialDerivative * cos - radialDistance * sin,
    dy: radialDerivative * sin + radialDistance * cos,
  };
}

// Convert the polar equation and its analytical tangent into a closed cubic
// Bezier path. All presets use the same segment count, keeping every frame
// smooth and directly controlled by the parameters above.
function orbitalPath(parameters, disturbance = null) {
  const step = TAU / CURVE_SEGMENTS;
  const tangentScale = (4 / 3) * Math.tan(step / 4);
  const tangentInset = step * 0.0001;
  const startAngle = -Math.PI / 2;
  const start = orbitalPoint(parameters, startAngle, disturbance);
  let path = `M ${start.x.toFixed(2)} ${start.y.toFixed(2)}`;

  for (let index = 0; index < CURVE_SEGMENTS; index += 1) {
    const fromAngle = startAngle + index * step;
    const toAngle = fromAngle + step;
    const from = orbitalPoint(parameters, fromAngle, disturbance);
    const to = orbitalPoint(parameters, toAngle, disturbance);
    // Use one-sided tangents so both sides of an |cos| zero keep their own
    // direction. That creates a symmetric centre cusp instead of a swirl.
    const fromTangent = orbitalPoint(parameters, fromAngle + tangentInset, disturbance);
    const toTangent = orbitalPoint(parameters, toAngle - tangentInset, disturbance);
    path += ` C ${(from.x + fromTangent.dx * tangentScale).toFixed(2)} ${(from.y + fromTangent.dy * tangentScale).toFixed(2)}`;
    path += ` ${(to.x - toTangent.dx * tangentScale).toFixed(2)} ${(to.y - toTangent.dy * tangentScale).toFixed(2)}`;
    path += ` ${to.x.toFixed(2)} ${to.y.toFixed(2)}`;
  }
  return `${path} Z`;
}

const ORBITALS = Object.fromEntries(
  Object.entries(ORBITAL_PARAMETERS).map(([name, parameters]) => [name, orbitalPath(parameters)]),
);

function interpolatePath(from, to, progress) {
  return orbitalPath(interpolateParameters(from, to, progress));
}

// The pre-transition ripple is another axis-symmetric radial harmonic on the
// same equation. It stays zero at every centre crossing and at both time ends.
function ripplePath(orbital, progress) {
  const safeProgress = clamp(progress);
  const envelope = Math.sin(Math.PI * safeProgress) * smoothstep(safeProgress);
  return orbitalPath(ORBITAL_PARAMETERS[orbital], {
    amplitude: MOTION.jitterAmplitude * envelope,
    phase: safeProgress * TAU,
  });
}

// Two moving sinusoidal contours make the probability density visibly flow.
// They are clipped by the same curve that is being breathed/rippled/morphed.
function densityWavePath(phase, offset = 0) {
  const points = [];
  for (let x = -8; x <= 108; x += 5.8) {
    const y = 50 + offset
      + 8.2 * Math.sin(x * 0.105 + phase)
      + 2.8 * Math.sin(x * 0.22 - phase * 1.7);
    points.push([x, y]);
  }
  return points.map(([x, y], index) => `${index ? "L" : "M"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
}

function chooseNextOrbital(current) {
  const choices = Object.keys(ORBITAL_PARAMETERS).filter((orbital) => orbital !== current);
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
  const reducedMotion = () => Boolean(view?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  return { now, requestFrame, cancelFrame, reducedMotion };
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
  const clipId = `orbital-clip-${id}`;
  const definitions = createSvgElement(document, "defs");
  const gradient = createSvgElement(document, "radialGradient", {
    id: gradientId,
    gradientUnits: "userSpaceOnUse",
    cx: "38",
    cy: "34",
    r: "52",
  });
  [
    ["0%", "0.92"],
    ["38%", "0.56"],
    ["76%", "0.24"],
    ["100%", "0.09"],
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
  filter.appendChild(createSvgElement(document, "feGaussianBlur", { stdDeviation: "2.6" }));
  const clip = createSvgElement(document, "clipPath", { id: clipId, clipPathUnits: "userSpaceOnUse" });
  const clipPath = createSvgElement(document, "path", { d: ORBITALS.s });
  clip.appendChild(clipPath);
  definitions.append(gradient, filter, clip);

  const motionGroup = createSvgElement(document, "g", { class: "orbital-agent-indicator__motion" });
  const centeredGroup = createSvgElement(document, "g", { transform: "translate(50 50)" });
  const transitionGroup = createSvgElement(document, "g", { class: "orbital-agent-indicator__transition" });
  const pathGroup = createSvgElement(document, "g", { transform: "translate(-50 -50)" });

  const glowPath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: "var(--orbital-color)",
    "fill-opacity": "0.28",
    filter: `url(#${glowId})`,
    "clip-path": `url(#${clipId})`,
    class: "orbital-agent-indicator__glow",
  });
  const cloudPath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: `url(#${gradientId})`,
    class: "orbital-agent-indicator__cloud",
  });
  const densityGroup = createSvgElement(document, "g", {
    "clip-path": `url(#${clipId})`,
    class: "orbital-agent-indicator__density",
  });
  const densityBand = createSvgElement(document, "path", {
    d: densityWavePath(0),
    fill: "none",
    stroke: "var(--orbital-color)",
    "stroke-width": "10",
    "stroke-linecap": "round",
    "stroke-opacity": "0.25",
    filter: `url(#${glowId})`,
  });
  const densityContour = createSvgElement(document, "path", {
    d: densityWavePath(0, 15),
    fill: "none",
    stroke: "var(--orbital-color)",
    "stroke-width": "2.2",
    "stroke-linecap": "round",
    "stroke-opacity": "0.72",
  });
  densityGroup.append(densityBand, densityContour);

  const outlinePath = createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: "none",
    stroke: "var(--orbital-color)",
    "stroke-width": "1.25",
    "stroke-opacity": "0.96",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "vector-effect": "non-scaling-stroke",
    class: "orbital-agent-indicator__outline",
  });
  const ripplePaths = [0, 1].map((index) => createSvgElement(document, "path", {
    d: ORBITALS.s,
    fill: "none",
    stroke: "var(--orbital-color)",
    "stroke-width": index ? "0.85" : "1.1",
    "stroke-opacity": "0",
    "vector-effect": "non-scaling-stroke",
    class: `orbital-agent-indicator__ripple orbital-agent-indicator__ripple--${index + 1}`,
  }));

  pathGroup.append(glowPath, cloudPath, densityGroup, outlinePath, ...ripplePaths);
  transitionGroup.appendChild(pathGroup);
  centeredGroup.appendChild(transitionGroup);
  motionGroup.appendChild(centeredGroup);
  svg.append(titleElement, definitions, motionGroup);

  return {
    svg,
    titleElement,
    gradient,
    transitionGroup,
    clipPath,
    glowPath,
    densityBand,
    densityContour,
    ripplePaths,
    shapePaths: [glowPath, cloudPath, outlinePath],
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
  let phase = "dwell";
  let phaseStartedAt = 0;
  let dwellDuration = MOTION.dwellMax;
  let motionTime = 0;
  let lastFrameAt = 0;
  let frameId = null;
  let destroyed = false;

  function setDisplayPath(path) {
    elements.shapePaths.forEach((element) => element.setAttribute("d", path));
    elements.ripplePaths.forEach((element) => element.setAttribute("d", path));
    elements.clipPath.setAttribute("d", path);
  }

  function setRipple(element, localProgress) {
    const progress = clamp(localProgress);
    const visibility = Math.sin(Math.PI * progress);
    const scale = 1 + MOTION.rippleScale * smoothstep(progress);
    element.setAttribute("stroke-opacity", (visibility * 0.58).toFixed(3));
    element.setAttribute(
      "transform",
      `translate(50 50) scale(${scale.toFixed(4)}) translate(-50 -50)`,
    );
  }

  function drawFrame(time) {
    const elapsed = time - phaseStartedAt;

    if (phase === "dwell" && elapsed >= dwellDuration) {
      phase = "ripple";
      phaseStartedAt = time;
      nextOrbital = chooseNextOrbital(orbital);
    } else if (phase === "ripple" && elapsed >= MOTION.preTransition) {
      phase = "morph";
      phaseStartedAt = time;
    } else if (phase === "morph" && elapsed >= MOTION.morph) {
      orbital = nextOrbital;
      nextOrbital = null;
      phase = "dwell";
      phaseStartedAt = time;
      dwellDuration = MOTION.dwellMin + Math.random() * (MOTION.dwellMax - MOTION.dwellMin);
    }

    const phaseElapsed = time - phaseStartedAt;
    const densityPhase = (time / MOTION.densityPeriod) * TAU;
    const breath = 0.5 + 0.5 * Math.sin((time / MOTION.breathePeriod) * TAU - Math.PI / 2);
    let path = ORBITALS[orbital];
    let scale = 1 - MOTION.breatheScale / 2 + MOTION.breatheScale * breath;
    let rotation = 0;
    let agitation = 0;

    if (phase === "ripple") {
      const progress = clamp(phaseElapsed / MOTION.preTransition);
      agitation = Math.sin(Math.PI * progress) * smoothstep(progress);
      path = ripplePath(orbital, progress);
      scale += 0.025 * agitation * Math.sin(progress * TAU * 9);
      rotation = 1.7 * agitation * Math.sin(progress * TAU * 11);
      setRipple(elements.ripplePaths[0], progress / 0.78);
      setRipple(elements.ripplePaths[1], (progress - 0.2) / 0.8);
    } else {
      setRipple(elements.ripplePaths[0], 0);
      setRipple(elements.ripplePaths[1], 0);
    }

    if (phase === "morph") {
      const progress = clamp(phaseElapsed / MOTION.morph);
      path = interpolatePath(orbital, nextOrbital, progress);
      // A small excitation pulse makes the jump legible without introducing a
      // discontinuity at either endpoint.
      scale += 0.075 * Math.sin(Math.PI * progress);
      rotation = 2.2 * Math.sin(TAU * progress) * Math.sin(Math.PI * progress);
    }

    setDisplayPath(path);
    elements.transitionGroup.setAttribute(
      "transform",
      `rotate(${rotation.toFixed(3)}) scale(${scale.toFixed(4)})`,
    );
    elements.glowPath.setAttribute("fill-opacity", (0.2 + breath * 0.18 + agitation * 0.08).toFixed(3));

    // Lissajous motion keeps the density from looking like a rotating decal.
    const densityX = 50 + 17 * Math.sin(densityPhase) + agitation * 3.5 * Math.sin(densityPhase * 6);
    const densityY = 50 + 14 * Math.sin(densityPhase * 1.37 + 1.1);
    elements.gradient.setAttribute("cx", densityX.toFixed(2));
    elements.gradient.setAttribute("cy", densityY.toFixed(2));
    elements.gradient.setAttribute("r", (44 + breath * 10 - agitation * 4).toFixed(2));
    elements.densityBand.setAttribute("d", densityWavePath(densityPhase + agitation * 2.2));
    elements.densityBand.setAttribute("stroke-opacity", (0.22 + breath * 0.14 + agitation * 0.14).toFixed(3));
    elements.densityContour.setAttribute("d", densityWavePath(-densityPhase * 0.82, 15));
    elements.densityContour.setAttribute("stroke-opacity", (0.54 + breath * 0.22 + agitation * 0.18).toFixed(3));
  }

  function stopMotion() {
    if (frameId !== null) scheduler.cancelFrame(frameId);
    frameId = null;
  }

  function animate(now) {
    frameId = null;
    if (destroyed || !ACTIVE_STATES.has(safeState)) return;
    const elapsed = Math.max(0, now - lastFrameAt);
    motionTime += elapsed * (STATE_TEMPO[safeState] || 1);
    lastFrameAt = now;
    drawFrame(motionTime);
    frameId = scheduler.requestFrame(animate);
  }

  function startMotion() {
    stopMotion();
    const now = scheduler.now();
    motionTime = 0;
    lastFrameAt = now;
    phase = "dwell";
    phaseStartedAt = 0;
    dwellDuration = MOTION.dwellMax;
    nextOrbital = null;
    drawFrame(motionTime);
    if (!scheduler.reducedMotion()) frameId = scheduler.requestFrame(animate);
  }

  function render(nextState = "idle") {
    if (destroyed || nextState === renderedState) return;
    renderedState = nextState;

    const nextSafeState = VALID_STATES.has(nextState) ? nextState : "idle";
    const wasActive = ACTIVE_STATES.has(safeState);
    const isActive = ACTIVE_STATES.has(nextSafeState);
    safeState = nextSafeState;
    elements.svg.setAttribute(
      "class",
      `orbital-agent-indicator orbital-agent-indicator--${safeState} ${elements.className}`.trim(),
    );
    const nextTitle = String(titleForState(nextState));
    elements.svg.setAttribute("aria-label", nextTitle);
    elements.titleElement.textContent = nextTitle;

    if (isActive && !wasActive) {
      startMotion();
    } else if (!isActive && wasActive) {
      stopMotion();
      nextOrbital = null;
      phase = "dwell";
      setDisplayPath(ORBITALS[orbital]);
      elements.transitionGroup.setAttribute("transform", "rotate(0) scale(1)");
      setRipple(elements.ripplePaths[0], 0);
      setRipple(elements.ripplePaths[1], 0);
    }
  }

  function unmount() {
    if (destroyed) return;
    destroyed = true;
    stopMotion();
    elements.svg.remove();
  }

  target.replaceChildren(elements.svg);
  render(state);
  return { render, unmount };
}
