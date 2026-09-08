import assert from "node:assert/strict";
import test from "node:test";

import { createAppearanceController } from "../src/features/settings/AppearanceController.js";
import { BUILTIN_SKINS, DEFAULT_SKIN } from "../src/theme/builtinSkins.js";

class FakeClassList {
  constructor(element) {
    this.element = element;
  }

  add(...names) {
    const classes = new Set(this.element.className.split(/\s+/).filter(Boolean));
    names.forEach((name) => classes.add(name));
    this.element.className = Array.from(classes).join(" ");
  }

  contains(name) {
    return this.element.className.split(/\s+/).includes(name);
  }
}

class FakeElement {
  constructor(ownerDocument, tagName) {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = "";
    this.classList = new FakeClassList(this);
    this.textContent = "";
    this.name = "";
    this.value = "";
    this.checked = false;
    this.type = "";
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    children.forEach((child) => this.appendChild(child));
  }

  replaceChildren(...children) {
    this.children.forEach((child) => {
      child.parentElement = null;
    });
    this.children = [];
    this.append(...children);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type, target = this) {
    for (const listener of this.listeners.get(type) || []) listener({ target });
  }

  closest(selector) {
    const inputName = selector.match(/^input\[name='([^']+)'\]$/)?.[1];
    if (inputName && this.tagName === "INPUT" && this.name === inputName) return this;
    return this.parentElement?.closest(selector) || null;
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    for (const id of [
      "settings-skin-options",
      "settings-skin-variant-options",
      "settings-appearance-status",
    ]) {
      this.elements.set(id, this.createElement("div"));
    }
  }

  createElement(tagName) {
    return new FakeElement(this, tagName);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }
}

function createThemeManager(skins = BUILTIN_SKINS, initialSelection = {
  skinId: "matcreator-default",
  skinName: "MatCreator Default",
  variant: "dark",
  colorScheme: "dark",
  version: "1.0.0",
}) {
  let selection = { ...initialSelection };
  const subscribers = new Set();
  const applyCalls = [];
  let nextFailure = null;

  return {
    applyCalls,
    apply(request) {
      applyCalls.push({ ...request });
      if (nextFailure) {
        const error = nextFailure;
        nextFailure = null;
        throw error;
      }
      const skin = skins.find(({ id }) => id === request.skinId);
      const variant = skin?.variants[request.variant];
      if (!skin || !variant) throw new Error("Unknown test skin selection");
      selection = {
        skinId: skin.id,
        skinName: skin.name,
        variant: request.variant,
        colorScheme: variant.colorScheme,
        version: skin.version,
      };
      subscribers.forEach((listener) => listener({ ...selection }));
      return { ...selection };
    },
    emit(nextSelection) {
      selection = { ...nextSelection };
      subscribers.forEach((listener) => listener({ ...selection }));
    },
    failNext(error) {
      nextFailure = error;
    },
    getSelection() {
      return { ...selection };
    },
    listSkins() {
      return skins;
    },
    subscribe(listener) {
      subscribers.add(listener);
      return () => subscribers.delete(listener);
    },
    subscriberCount() {
      return subscribers.size;
    },
  };
}

function descendants(root) {
  return root.children.flatMap((child) => [child, ...descendants(child)]);
}

function findInput(root, name, value) {
  return descendants(root).find((element) => (
    element.tagName === "INPUT" && element.name === name && element.value === value
  ));
}

function selectedInput(root, name) {
  return descendants(root).find((element) => (
    element.tagName === "INPUT" && element.name === name && element.checked
  ));
}

function createFixture({ skins, selection } = {}) {
  const document = new FakeDocument();
  const themeManager = createThemeManager(skins, selection);
  const controller = createAppearanceController({ document, themeManager });
  return {
    controller,
    document,
    skinOptions: document.getElementById("settings-skin-options"),
    status: document.getElementById("settings-appearance-status"),
    themeManager,
    variantOptions: document.getElementById("settings-skin-variant-options"),
  };
}

test("initial sync renders every built-in skin and the active variants", () => {
  const fixture = createFixture();

  fixture.controller.sync();

  assert.equal(fixture.skinOptions.children.length, 2);
  assert.equal(findInput(fixture.skinOptions, "settings-skin", "matcreator-default")?.checked, true);
  assert.equal(findInput(fixture.skinOptions, "settings-skin", "rack-lab")?.checked, false);
  assert.equal(fixture.variantOptions.children.length, 2);
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "dark");
  assert.equal(
    descendants(fixture.skinOptions).filter(({ classList }) => classList.contains("settings-skin-swatch")).length,
    6,
  );
});

test("skin selection preserves a compatible variant", () => {
  const fixture = createFixture();
  fixture.controller.sync();

  const rackInput = findInput(fixture.skinOptions, "settings-skin", "rack-lab");
  fixture.skinOptions.dispatch("change", rackInput);

  assert.deepEqual(fixture.themeManager.applyCalls, [{ skinId: "rack-lab", variant: "dark" }]);
  assert.equal(selectedInput(fixture.skinOptions, "settings-skin")?.value, "rack-lab");
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "dark");
  assert.equal(fixture.status.dataset.tone, "success");
  assert.match(fixture.status.textContent, /Rack Lab applied/i);
});

test("skin selection falls back to the destination skin default variant", () => {
  const creamOnly = {
    id: "cream-only",
    name: "Cream Only",
    description: "One compatible appearance",
    version: "1.0.0",
    defaultVariant: "cream",
    preview: { swatches: ["#fff", "#eee", "#111"] },
    variants: { cream: { label: "Cream", colorScheme: "light", tokens: {} } },
  };
  const fixture = createFixture({ skins: [DEFAULT_SKIN, creamOnly] });
  fixture.controller.sync();

  fixture.skinOptions.dispatch(
    "change",
    findInput(fixture.skinOptions, "settings-skin", "cream-only"),
  );

  assert.deepEqual(fixture.themeManager.applyCalls, [{ skinId: "cream-only", variant: "cream" }]);
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "cream");
});

test("variant selection applies against the active skin", () => {
  const fixture = createFixture();
  fixture.controller.sync();

  fixture.variantOptions.dispatch(
    "change",
    findInput(fixture.variantOptions, "settings-skin-variant", "light"),
  );

  assert.deepEqual(fixture.themeManager.applyCalls, [{ skinId: "matcreator-default", variant: "light" }]);
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "light");
  assert.equal(fixture.status.dataset.tone, "success");
  assert.match(fixture.status.textContent, /MatCreator Default · light applied/i);
});

test("an external theme subscription refreshes the rendered selection", () => {
  const fixture = createFixture();
  fixture.controller.sync();

  fixture.themeManager.emit({
    skinId: "rack-lab",
    skinName: "Rack Lab",
    variant: "light",
    colorScheme: "light",
    version: "0.1.0",
  });

  assert.equal(selectedInput(fixture.skinOptions, "settings-skin")?.value, "rack-lab");
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "light");
  assert.equal(fixture.themeManager.applyCalls.length, 0);
});

test("an apply failure restores selection and exposes an error status", () => {
  const fixture = createFixture();
  fixture.controller.sync();
  fixture.themeManager.failNext(new Error("storage is unavailable"));

  fixture.variantOptions.dispatch(
    "change",
    findInput(fixture.variantOptions, "settings-skin-variant", "light"),
  );

  assert.equal(fixture.status.dataset.tone, "error");
  assert.match(fixture.status.textContent, /Variant could not be applied: storage is unavailable/i);
  assert.equal(selectedInput(fixture.variantOptions, "settings-skin-variant")?.value, "dark");
});

test("destroy removes both change handlers and the manager subscription", () => {
  const fixture = createFixture();
  fixture.controller.sync();
  const previousSkin = selectedInput(fixture.skinOptions, "settings-skin")?.value;
  fixture.controller.destroy();

  assert.equal(fixture.themeManager.subscriberCount(), 0);
  fixture.skinOptions.dispatch(
    "change",
    findInput(fixture.skinOptions, "settings-skin", "rack-lab"),
  );
  fixture.themeManager.emit({
    skinId: "rack-lab",
    skinName: "Rack Lab",
    variant: "light",
    colorScheme: "light",
    version: "0.1.0",
  });

  assert.equal(fixture.themeManager.applyCalls.length, 0);
  assert.equal(selectedInput(fixture.skinOptions, "settings-skin")?.value, previousSkin);
});
