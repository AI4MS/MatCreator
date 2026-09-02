import assert from "node:assert/strict";
import test from "node:test";

import { BUILTIN_SKINS } from "../src/theme/builtinSkins.js";
import { validateSkinManifest } from "../src/theme/SkinContract.js";
import {
  LEGACY_THEME_STORAGE_KEY,
  SKIN_STORAGE_KEY,
  SKIN_VARIANT_STORAGE_KEY,
  createThemeManager,
} from "../src/theme/ThemeManager.js";
import { createThemeRegistry } from "../src/theme/ThemeRegistry.js";
import { STYLE_RECIPE_REGISTRY } from "../src/theme/StyleRecipeRegistry.js";

class FakeStyle {
  constructor() {
    this.properties = new Map();
    this.colorScheme = "";
    this.failure = null;
  }

  getPropertyValue(name) {
    return this.properties.get(name) || "";
  }

  removeProperty(name) {
    this.properties.delete(name);
  }

  setProperty(name, value) {
    if (this.failure?.(name, value)) {
      this.failure = null;
      throw new Error(`Unable to apply ${name}`);
    }
    this.properties.set(name, value);
  }
}

class FakeCustomEvent {
  constructor(type, { detail } = {}) {
    this.type = type;
    this.detail = detail;
  }
}

function createStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

function createEventTarget() {
  return {
    CustomEvent: FakeCustomEvent,
    events: [],
    dispatchEvent(event) {
      this.events.push(event);
      return true;
    },
  };
}

function createFixture(storageValues = {}) {
  const target = { dataset: {}, style: new FakeStyle() };
  const storage = createStorage(storageValues);
  const eventTarget = createEventTarget();
  const registry = createThemeRegistry(BUILTIN_SKINS);
  const manager = createThemeManager({ registry, target, storage, eventTarget });
  return { eventTarget, manager, registry, storage, target };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

test("validates and registers every built-in skin", () => {
  const registry = createThemeRegistry();

  for (const manifest of BUILTIN_SKINS) {
    assert.equal(validateSkinManifest(manifest), manifest);
    const registered = registry.register(manifest);
    assert.deepEqual(registered, manifest);
    assert.notEqual(registered, manifest);
    assert.equal(Object.isFrozen(registered), true);
    assert.equal(Object.isFrozen(registered.styleRecipe), true);
    assert.equal(Object.isFrozen(registered.variants[registered.defaultVariant].tokens), true);
  }

  assert.deepEqual(
    registry.list().map(({ id }) => id),
    ["matcreator-default", "rack-lab"],
  );
  assert.equal(registry.get("rack-lab")?.defaultVariant, "light");
  assert.throws(() => registry.register(BUILTIN_SKINS[0]), /already registered/i);
});

test("keeps style recipes local, immutable, and allow-listed", () => {
  assert.deepEqual(
    STYLE_RECIPE_REGISTRY.list().map(({ id, apiVersion }) => `${id}@${apiVersion}`),
    ["standard@1", "rack-lab@1"],
  );
  assert.equal(Object.isFrozen(STYLE_RECIPE_REGISTRY), true);
  assert.equal(Object.isFrozen(STYLE_RECIPE_REGISTRY.get("rack-lab", 1)), true);
  assert.equal(STYLE_RECIPE_REGISTRY.get("missing", 1), null);
  assert.equal("register" in STYLE_RECIPE_REGISTRY, false);

  const legacyManifest = clone(BUILTIN_SKINS[0]);
  legacyManifest.id = "legacy-default";
  delete legacyManifest.styleRecipe;
  const registeredLegacy = createThemeRegistry().register(legacyManifest);
  assert.deepEqual(registeredLegacy.styleRecipe, { id: "standard", apiVersion: 1 });

  const unavailableRecipe = clone(BUILTIN_SKINS[0]);
  unavailableRecipe.id = "unavailable-recipe";
  unavailableRecipe.styleRecipe = { id: "downloaded-css", apiVersion: 1 };
  assert.throws(
    () => createThemeRegistry().register(unavailableRecipe),
    /unavailable style recipe downloaded-css@1/i,
  );
});

test("rejects missing, unknown, and unsafe skin tokens", () => {
  const missingRequired = clone(BUILTIN_SKINS[0]);
  delete missingRequired.variants.dark["tokens"]["--focus-ring"];
  assert.throws(() => validateSkinManifest(missingRequired), /missing --focus-ring/i);

  const unknownToken = clone(BUILTIN_SKINS[0]);
  unknownToken.variants.dark.tokens["--untrusted-extension"] = "#fff";
  assert.throws(() => validateSkinManifest(unknownToken), /unknown token --untrusted-extension/i);

  const unsafeValue = clone(BUILTIN_SKINS[0]);
  unsafeValue.variants.dark.tokens["--bg-canvas"] = "url(javascript:alert(1))";
  assert.throws(() => validateSkinManifest(unsafeValue), /invalid value for --bg-canvas/i);

  const invalidSurfaceTone = clone(BUILTIN_SKINS[0]);
  invalidSurfaceTone.variants.dark.tokens["--skin-graph-surface-tone"] = "auto";
  assert.throws(
    () => validateSkinManifest(invalidSurfaceTone),
    /invalid value for --skin-graph-surface-tone/i,
  );

  const unsupportedSchema = clone(BUILTIN_SKINS[0]);
  unsupportedSchema.skinSchemaVersion = 2;
  assert.throws(() => validateSkinManifest(unsupportedSchema), /unsupported schema version/i);

  const cssInjection = clone(BUILTIN_SKINS[0]);
  cssInjection.styleRecipe.css = "body { display: none }";
  assert.throws(() => validateSkinManifest(cssInjection), /styleRecipe uses unsupported field css/i);

  const remoteStylesheet = clone(BUILTIN_SKINS[0]);
  remoteStylesheet.stylesheet = "https://example.invalid/skin.css";
  assert.throws(() => validateSkinManifest(remoteStylesheet), /unsupported field stylesheet/i);

  const invalidRecipeId = clone(BUILTIN_SKINS[0]);
  invalidRecipeId.styleRecipe.id = "../rack-lab";
  assert.throws(() => validateSkinManifest(invalidRecipeId), /invalid styleRecipe id/i);

  const invalidRecipeVersion = clone(BUILTIN_SKINS[0]);
  invalidRecipeVersion.styleRecipe.apiVersion = 0;
  assert.throws(() => validateSkinManifest(invalidRecipeVersion), /invalid styleRecipe apiVersion/i);
});

test("initializes the default skin from the legacy mat_theme preference", () => {
  const fixture = createFixture({ [LEGACY_THEME_STORAGE_KEY]: "light" });
  const observed = [];
  fixture.manager.subscribe((selection) => observed.push(selection));

  const selection = fixture.manager.initialize();

  assert.deepEqual(selection, {
    skinId: "matcreator-default",
    skinName: "MatCreator Default",
    styleRecipeId: "standard",
    styleRecipeApiVersion: 1,
    variant: "light",
    colorScheme: "light",
    version: "1.0.0",
  });
  assert.deepEqual(fixture.target.dataset, {
    skin: "matcreator-default",
    styleRecipe: "standard",
    styleRecipeVersion: "1",
    theme: "light",
    variant: "light",
  });
  assert.equal(fixture.target.style.colorScheme, "light");
  assert.equal(fixture.target.style.getPropertyValue("--bg-canvas"), "#ECEDEF");
  assert.equal(fixture.target.style.getPropertyValue("--skin-graph-surface-tone"), "light");
  assert.equal(fixture.storage.values.has(SKIN_STORAGE_KEY), false, "initialization must not rewrite storage");
  assert.equal(fixture.storage.values.has(SKIN_VARIANT_STORAGE_KEY), false);
  assert.deepEqual(observed, [selection]);
  assert.equal(fixture.eventTarget.events[0].type, "matcreator-theme-change");
  assert.equal(fixture.eventTarget.events[0].detail, "light", "the legacy event keeps its string detail");
  assert.equal(fixture.eventTarget.events[1].type, "matcreator-skin-change");
  assert.deepEqual(fixture.eventTarget.events[1].detail, selection);
});

test("prefers the new skin keys over the legacy theme preference", () => {
  const fixture = createFixture({
    [SKIN_STORAGE_KEY]: "rack-lab",
    [SKIN_VARIANT_STORAGE_KEY]: "dark",
    [LEGACY_THEME_STORAGE_KEY]: "light",
  });

  assert.deepEqual(fixture.manager.initialize(), {
    skinId: "rack-lab",
    skinName: "Rack Lab",
    styleRecipeId: "rack-lab",
    styleRecipeApiVersion: 1,
    variant: "dark",
    colorScheme: "dark",
    version: "0.1.0",
  });
  assert.equal(fixture.target.dataset.theme, "dark");
  assert.equal(fixture.target.style.getPropertyValue("--skin-composer-surface"), "#FFC400");
});

test("applies, persists, and toggles the Rack Lab variants with compatible events", () => {
  const fixture = createFixture();
  fixture.manager.initialize();
  fixture.eventTarget.events.length = 0;

  const cream = fixture.manager.apply({ skinId: "rack-lab", variant: "light" });

  assert.equal(cream.variant, "light");
  assert.equal(fixture.target.dataset.skin, "rack-lab");
  assert.equal(fixture.target.dataset.styleRecipe, "rack-lab");
  assert.equal(fixture.target.dataset.styleRecipeVersion, "1");
  assert.equal(fixture.target.dataset.theme, "light");
  assert.equal(fixture.target.style.getPropertyValue("--skin-shell-border-width"), "2px");
  assert.equal(fixture.target.style.getPropertyValue("--skin-graph-surface-tone"), "light");
  assert.equal(fixture.target.style.getPropertyValue("--skin-graph-droplet-fill-alpha"), "0.52");
  assert.equal(fixture.target.style.getPropertyValue("--accent-primary"), "#8A6500");
  assert.equal(fixture.storage.values.get(SKIN_STORAGE_KEY), "rack-lab");
  assert.equal(fixture.storage.values.get(SKIN_VARIANT_STORAGE_KEY), "light");
  assert.equal(fixture.storage.values.get(LEGACY_THEME_STORAGE_KEY), "light");
  assert.deepEqual(
    fixture.eventTarget.events.map(({ type, detail }) => [type, typeof detail === "string" ? detail : detail.variant]),
    [["matcreator-theme-change", "light"], ["matcreator-skin-change", "light"]],
  );

  fixture.eventTarget.events.length = 0;
  const graphite = fixture.manager.toggleVariant();

  assert.equal(graphite.skinId, "rack-lab");
  assert.equal(graphite.variant, "dark");
  assert.equal(graphite.colorScheme, "dark");
  assert.equal(fixture.target.dataset.theme, "dark");
  assert.equal(fixture.target.style.getPropertyValue("--bg-canvas"), "#20222A");
  assert.equal(fixture.target.style.getPropertyValue("--skin-graph-surface-tone"), "dark");
  assert.equal(fixture.target.style.getPropertyValue("--skin-graph-droplet-fill-alpha"), "0.56");
  assert.equal(fixture.storage.values.get(SKIN_VARIANT_STORAGE_KEY), "dark");
  assert.equal(fixture.storage.values.get(LEGACY_THEME_STORAGE_KEY), "dark");
  assert.equal(fixture.eventTarget.events[0].detail, "dark");
  assert.equal(fixture.eventTarget.events[1].detail.variant, "dark");
});

test("falls back to the registered default for an unavailable skin", () => {
  const fixture = createFixture({
    [SKIN_STORAGE_KEY]: "skin-no-longer-installed",
    [SKIN_VARIANT_STORAGE_KEY]: "light",
  });

  const selection = fixture.manager.initialize();

  assert.equal(selection.skinId, "matcreator-default");
  assert.equal(selection.variant, "light");
  assert.equal(fixture.target.dataset.skin, "matcreator-default");
});

test("restores the previous DOM state when applying a skin token fails", () => {
  const fixture = createFixture();
  const previousSelection = fixture.manager.initialize();
  const previousProperties = new Map(fixture.target.style.properties);
  const previousDataset = { ...fixture.target.dataset };
  const previousColorScheme = fixture.target.style.colorScheme;
  const previousEvents = fixture.eventTarget.events.length;
  fixture.target.style.failure = (name, value) => name === "--bg-elevated" && value === "#FFFDF8";

  assert.throws(
    () => fixture.manager.apply({ skinId: "rack-lab", variant: "light" }),
    /Unable to apply --bg-elevated/,
  );

  assert.deepEqual(fixture.manager.getSelection(), previousSelection);
  assert.deepEqual(fixture.target.dataset, previousDataset);
  assert.equal(fixture.target.style.colorScheme, previousColorScheme);
  assert.deepEqual(fixture.target.style.properties, previousProperties);
  assert.equal(fixture.eventTarget.events.length, previousEvents, "a failed apply must not publish events");
  assert.equal(fixture.storage.values.has(SKIN_STORAGE_KEY), false, "a failed apply must not persist selection");
});
