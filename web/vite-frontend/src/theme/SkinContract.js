const SKIN_ID_PATTERN = /^[a-z0-9][a-z0-9.-]{1,63}$/;
const VARIANT_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;
const STYLE_RECIPE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;
const FORBIDDEN_VALUE_PATTERN = /(?:url\s*\(|expression\s*\(|@import|javascript:|data:|[;{}<>])/i;

const SKIN_MANIFEST_KEYS = new Set([
  "id",
  "name",
  "description",
  "version",
  "skinSchemaVersion",
  "styleRecipe",
  "defaultVariant",
  "preview",
  "variants",
]);
const STYLE_RECIPE_KEYS = new Set(["id", "apiVersion"]);

export const DEFAULT_STYLE_RECIPE = Object.freeze({ id: "standard", apiVersion: 1 });

const COLOR_TOKENS = [
  "--bg-canvas", "--bg-surface", "--bg-elevated", "--bg-hover", "--bg-active",
  "--text-primary", "--text-secondary", "--text-muted", "--text-disabled",
  "--border-subtle", "--border-default", "--border-strong",
  "--accent-primary", "--accent-primary-hover", "--accent-primary-active",
  "--accent-primary-soft", "--accent-on-primary", "--accent-secondary",
  "--accent-secondary-soft", "--selection", "--focus-ring",
  "--success", "--warning", "--danger", "--info",
  "--step-feed-idle-bg", "--step-feed-idle-border", "--step-feed-success-bg",
  "--step-feed-success-border", "--step-feed-warning-bg", "--step-feed-warning-border",
  "--step-feed-danger-bg", "--step-feed-danger-border",
  "--timeline-badge-in-bg", "--timeline-badge-in-text", "--timeline-badge-out-bg",
  "--timeline-badge-out-text", "--terminal-bg", "--terminal-border", "--terminal-text",
  "--terminal-muted", "--session-item-hover-bg", "--session-item-hover-border",
  "--session-item-active-bg", "--session-item-active-border", "--inline-code-bg",
  "--modal-overlay", "--subtle-surface", "--scrollbar-track", "--scrollbar-thumb",
  "--scrollbar-thumb-hover", "--agent-box-shadow-color", "--user-box-shadow-color",
  "--agent-bg-1", "--agent-bg-2", "--agent-border", "--user-bg-1", "--user-bg-2",
  "--user-border", "--skin-body-surface", "--skin-shell-border-color",
  "--skin-shell-background", "--skin-graph-surface", "--skin-chat-surface",
  "--skin-side-surface", "--skin-composer-surface", "--skin-screw-color",
  "--skin-control-background", "--skin-control-border-color",
];

const LENGTH_TOKENS = [
  "--radius", "--radius-sm", "--radius-md", "--radius-lg",
  "--skin-shell-border-width", "--skin-shell-radius", "--skin-screw-size",
  "--skin-display-letter-spacing", "--skin-graph-title-size",
  "--skin-graph-title-height", "--skin-graph-title-clearance",
];

const NUMBER_TOKENS = ["--skin-screw-opacity"];
const RGB_TRIPLET_TOKENS = ["--panel-rgb", "--accent-rgb"];
const SHADOW_TOKENS = [
  "--panel-shadow", "--popup-shadow", "--step-feed-message-box-shadow",
  "--step-feed-highlight-box-shadow", "--skin-shell-shadow", "--skin-control-shadow",
  "--skin-control-pressed-shadow",
];
const FONT_TOKENS = ["--skin-display-font", "--skin-label-font"];
const IDENT_TOKENS = ["--skin-label-transform"];
const TONE_TOKENS = ["--skin-graph-surface-tone"];

export const SKIN_TOKEN_SCHEMA = Object.freeze(Object.fromEntries([
  ...COLOR_TOKENS.map((name) => [name, "color"]),
  ...LENGTH_TOKENS.map((name) => [name, "length"]),
  ...NUMBER_TOKENS.map((name) => [name, "number"]),
  ...RGB_TRIPLET_TOKENS.map((name) => [name, "rgb-triplet"]),
  ...SHADOW_TOKENS.map((name) => [name, "shadow"]),
  ...FONT_TOKENS.map((name) => [name, "font"]),
  ...IDENT_TOKENS.map((name) => [name, "ident"]),
  ...TONE_TOKENS.map((name) => [name, "tone"]),
]));

export const REQUIRED_SKIN_TOKENS = Object.freeze([
  "--bg-canvas",
  "--bg-surface",
  "--bg-elevated",
  "--text-primary",
  "--text-secondary",
  "--border-default",
  "--accent-primary",
  "--focus-ring",
  "--success",
  "--warning",
  "--danger",
]);

function assert(condition, message) {
  if (!condition) throw new TypeError(message);
}

function isColor(value) {
  return /^(?:#[0-9a-f]{3,8}|(?:rgb|hsl)a?\([0-9a-z%.,+\-\s/]+\)|oklch\([0-9a-z%.,+\-\s/]+\)|transparent|currentColor)$/i.test(value);
}

function isLength(value) {
  return /^(?:0|-?\d+(?:\.\d+)?(?:px|rem|em|ch|%))$/i.test(value);
}

function isRgbTriplet(value) {
  const channels = value.split(",").map((channel) => Number(channel.trim()));
  return channels.length === 3 && channels.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255);
}

function isSafeShadow(value) {
  if (value === "none") return true;
  return /^[a-z0-9#(),.%+\-\s]+$/i.test(value);
}

function isSafeFont(value) {
  return /^[a-z0-9,'" _-]+$/i.test(value);
}

function isValidTokenValue(type, value) {
  if (typeof value !== "string" || !value || value.length > 200 || FORBIDDEN_VALUE_PATTERN.test(value)) return false;
  if (type === "color") return isColor(value);
  if (type === "length") return isLength(value);
  if (type === "number") return Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 1;
  if (type === "rgb-triplet") return isRgbTriplet(value);
  if (type === "shadow") return isSafeShadow(value);
  if (type === "font") return isSafeFont(value);
  if (type === "ident") return /^(?:none|uppercase|lowercase|capitalize)$/i.test(value);
  if (type === "tone") return /^(?:light|dark)$/i.test(value);
  return false;
}

export function resolveStyleRecipe(manifest) {
  const recipe = manifest?.styleRecipe ?? DEFAULT_STYLE_RECIPE;
  assert(recipe && typeof recipe === "object" && !Array.isArray(recipe), `Skin ${manifest?.id || "(missing)"} styleRecipe must be an object`);

  const unexpectedKey = Object.keys(recipe).find((key) => !STYLE_RECIPE_KEYS.has(key));
  assert(!unexpectedKey, `Skin ${manifest?.id || "(missing)"} styleRecipe uses unsupported field ${unexpectedKey}`);
  assert(STYLE_RECIPE_ID_PATTERN.test(recipe.id || ""), `Skin ${manifest?.id || "(missing)"} has an invalid styleRecipe id`);
  assert(Number.isInteger(recipe.apiVersion) && recipe.apiVersion > 0, `Skin ${manifest?.id || "(missing)"} has an invalid styleRecipe apiVersion`);

  return { id: recipe.id, apiVersion: recipe.apiVersion };
}

export function validateSkinManifest(manifest) {
  assert(manifest && typeof manifest === "object" && !Array.isArray(manifest), "Skin manifest must be an object");
  const unexpectedKey = Object.keys(manifest).find((key) => !SKIN_MANIFEST_KEYS.has(key));
  assert(!unexpectedKey, `Skin manifest uses unsupported field ${unexpectedKey}`);
  assert(SKIN_ID_PATTERN.test(manifest.id || ""), `Invalid skin id: ${manifest.id || "(missing)"}`);
  assert(typeof manifest.name === "string" && manifest.name.trim(), `Skin ${manifest.id} requires a name`);
  assert(Number.isInteger(manifest.skinSchemaVersion) && manifest.skinSchemaVersion === 1, `Skin ${manifest.id} uses an unsupported schema version`);
  assert(typeof manifest.version === "string" && /^\d+\.\d+\.\d+(?:[-+][a-z0-9.-]+)?$/i.test(manifest.version), `Skin ${manifest.id} requires a semver version`);
  assert(manifest.variants && typeof manifest.variants === "object" && !Array.isArray(manifest.variants), `Skin ${manifest.id} requires variants`);
  resolveStyleRecipe(manifest);

  const variantIds = Object.keys(manifest.variants);
  assert(variantIds.length > 0, `Skin ${manifest.id} requires at least one variant`);
  assert(variantIds.every((id) => VARIANT_ID_PATTERN.test(id)), `Skin ${manifest.id} has an invalid variant id`);
  assert(variantIds.includes(manifest.defaultVariant), `Skin ${manifest.id} defaultVariant is unavailable`);

  for (const variantId of variantIds) {
    const variant = manifest.variants[variantId];
    assert(variant && typeof variant === "object" && !Array.isArray(variant), `Skin ${manifest.id}/${variantId} must be an object`);
    assert(variant.colorScheme === "light" || variant.colorScheme === "dark", `Skin ${manifest.id}/${variantId} requires a light or dark colorScheme`);
    const tokens = variant.tokens;
    assert(tokens && typeof tokens === "object" && !Array.isArray(tokens), `Skin ${manifest.id}/${variantId} requires tokens`);
    for (const required of REQUIRED_SKIN_TOKENS) {
      assert(Object.hasOwn(tokens, required), `Skin ${manifest.id}/${variantId} is missing ${required}`);
    }
    for (const [tokenName, value] of Object.entries(tokens)) {
      const type = SKIN_TOKEN_SCHEMA[tokenName];
      assert(type, `Skin ${manifest.id}/${variantId} uses unknown token ${tokenName}`);
      assert(isValidTokenValue(type, value), `Skin ${manifest.id}/${variantId} has an invalid value for ${tokenName}`);
    }
  }

  if (manifest.preview !== undefined) {
    assert(Array.isArray(manifest.preview?.swatches) && manifest.preview.swatches.length >= 3, `Skin ${manifest.id} preview requires at least three swatches`);
    assert(manifest.preview.swatches.every((value) => isValidTokenValue("color", value)), `Skin ${manifest.id} preview has an invalid swatch`);
  }

  return manifest;
}
