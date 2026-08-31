import assert from "node:assert/strict";
import test from "node:test";

import { createOrbitalAgentIndicator } from "../src/components/OrbitalAgentIndicator.js";

class FakeElement {
  constructor(name, ownerDocument) {
    this.name = name;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.attributes = new Map();
    this.attributeWrites = new Map();
    this.style = { setProperty: (name, value) => this.style[name] = value };
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    this.attributeWrites.set(name, (this.attributeWrites.get(name) || 0) + 1);
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  append(...children) {
    children.forEach((child) => this.appendChild(child));
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
  }

  insertBefore(child, reference) {
    const index = this.children.indexOf(reference);
    child.parentNode = this;
    this.children.splice(index, 0, child);
  }

  replaceChildren(...children) {
    this.children.forEach((child) => child.parentNode = null);
    this.children = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
}

function createFakeDom() {
  let timerId = 0;
  let currentTime = 0;
  const timers = new Map();
  const view = {
    performance: { now: () => currentTime },
    setTimeout(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    requestAnimationFrame(callback) {
      const id = ++timerId;
      timers.set(id, { callback, frame: true });
      return id;
    },
    cancelAnimationFrame(id) {
      timers.delete(id);
    },
  };
  const document = {
    defaultView: view,
    createElementNS(_namespace, name) {
      return new FakeElement(name, document);
    },
  };
  function runFrame(at) {
    currentTime = at;
    const entry = [...timers.entries()].find(([, timer]) => timer.frame);
    assert.ok(entry, `expected a queued animation frame at ${at}ms`);
    const [id, timer] = entry;
    timers.delete(id);
    timer.callback(at);
  }
  return { document, timers, runFrame };
}

function findByClass(element, className) {
  if ((element.getAttribute?.("class") || "").split(" ").includes(className)) return element;
  for (const child of element.children) {
    const match = findByClass(child, className);
    if (match) return match;
  }
  return null;
}

test("renders once per state and cleans up its active timer", () => {
  const { document, timers } = createFakeDom();
  const target = new FakeElement("div", document);
  const indicator = createOrbitalAgentIndicator(target, {
    size: 18,
    color: "var(--accent)",
    title: (state) => `MatCreator is ${state}`,
  });
  const svg = target.children[0];

  assert.equal(svg.name, "svg");
  assert.equal(svg.getAttribute("width"), "18");
  assert.equal(svg.getAttribute("class"), "orbital-agent-indicator orbital-agent-indicator--idle");
  assert.equal(svg.getAttribute("aria-label"), "MatCreator is idle");
  assert.equal(timers.size, 0);

  indicator.render("thinking");
  const ariaWrites = svg.attributeWrites.get("aria-label");
  assert.equal(svg.getAttribute("class"), "orbital-agent-indicator orbital-agent-indicator--thinking");
  assert.equal(timers.size, 1);

  indicator.render("thinking");
  assert.equal(svg.attributeWrites.get("aria-label"), ariaWrites);
  assert.equal(timers.size, 1);

  indicator.unmount();
  assert.equal(target.children.length, 0);
  assert.equal(timers.size, 0);
});

test("runs dwell, coupled ripple, and continuous orbital morph on one frame clock", () => {
  const { document, timers, runFrame } = createFakeDom();
  const target = new FakeElement("div", document);
  const indicator = createOrbitalAgentIndicator(target, { state: "thinking" });
  const svg = target.children[0];
  const outline = findByClass(svg, "orbital-agent-indicator__outline");
  const ripple = findByClass(svg, "orbital-agent-indicator__ripple--1");
  const density = findByClass(svg, "orbital-agent-indicator__density");
  const initialPath = outline.getAttribute("d");

  assert.ok(outline);
  assert.ok(density, "the animated density is clipped inside the orbital");
  assert.equal(timers.size, 1, "the choreography uses one animation-frame loop");

  runFrame(4200); // dwell -> pre-transition
  runFrame(4560); // strongest part of the pre-transition disturbance
  assert.notEqual(outline.getAttribute("d"), initialPath);
  assert.ok(Number(ripple.getAttribute("stroke-opacity")) > 0);

  runFrame(4920); // pre-transition -> morph
  runFrame(5140); // midpoint of the fast continuous morph
  assert.notEqual(outline.getAttribute("d"), initialPath);

  runFrame(5360); // settle on p or d; either must differ from initial s
  assert.notEqual(outline.getAttribute("d"), initialPath);
  assert.equal(timers.size, 1);

  indicator.unmount();
  assert.equal(timers.size, 0);
});

test("keeps the centre-crossing d orbital symmetric across both axes", () => {
  const originalRandom = Math.random;
  Math.random = () => 0.999;
  const { document, runFrame } = createFakeDom();
  const target = new FakeElement("div", document);
  const indicator = createOrbitalAgentIndicator(target, { state: "thinking" });

  try {
    runFrame(4200); // select d and enter ripple
    runFrame(4920); // enter morph
    runFrame(5360); // settle on d

    const outline = findByClass(target.children[0], "orbital-agent-indicator__outline");
    const values = outline.getAttribute("d").match(/-?\d*\.?\d+/g).map(Number);
    const endpoints = [[values[0], values[1]]];
    for (let index = 6; index < values.length; index += 6) {
      endpoints.push([values[index], values[index + 1]]);
    }
    const radii = endpoints.map(([x, y]) => Math.hypot(x - 50, y - 50));

    assert.ok(Math.min(...radii) < 0.01, "the four lobes meet at the centre");
    assert.ok(Math.max(...radii) > 41, "the lobes still reach the outer orbit");
    endpoints.forEach(([x, y]) => {
      const hasHorizontalReflection = endpoints.some(([otherX, otherY]) => (
        Math.abs(otherX - x) < 0.01 && Math.abs(otherY - (100 - y)) < 0.01
      ));
      const hasVerticalReflection = endpoints.some(([otherX, otherY]) => (
        Math.abs(otherX - (100 - x)) < 0.01 && Math.abs(otherY - y) < 0.01
      ));
      assert.ok(hasHorizontalReflection, `missing horizontal reflection of ${x},${y}`);
      assert.ok(hasVerticalReflection, `missing vertical reflection of ${x},${y}`);
    });
  } finally {
    indicator.unmount();
    Math.random = originalRandom;
  }
});
