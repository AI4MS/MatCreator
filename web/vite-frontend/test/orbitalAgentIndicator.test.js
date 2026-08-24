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
  const timers = new Map();
  const view = {
    performance: { now: () => 0 },
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
  return { document, timers };
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
