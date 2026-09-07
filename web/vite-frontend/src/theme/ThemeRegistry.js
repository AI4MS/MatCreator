import { resolveStyleRecipe, validateSkinManifest } from "./SkinContract.js";
import { STYLE_RECIPE_REGISTRY } from "./StyleRecipeRegistry.js";

function cloneAndFreeze(value) {
  if (Array.isArray(value)) return Object.freeze(value.map(cloneAndFreeze));
  if (!value || typeof value !== "object") return value;
  return Object.freeze(Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, cloneAndFreeze(child)]),
  ));
}

export function createThemeRegistry(initialSkins = [], { styleRecipeRegistry = STYLE_RECIPE_REGISTRY } = {}) {
  const skins = new Map();

  function register(manifest) {
    validateSkinManifest(manifest);
    if (skins.has(manifest.id)) throw new Error(`Skin already registered: ${manifest.id}`);
    const styleRecipe = resolveStyleRecipe(manifest);
    if (!styleRecipeRegistry?.has?.(styleRecipe.id, styleRecipe.apiVersion)) {
      throw new Error(`Skin ${manifest.id} requires unavailable style recipe ${styleRecipe.id}@${styleRecipe.apiVersion}`);
    }
    // Registry entries are immutable snapshots. A future downloaded skin cannot
    // mutate itself after passing the one-time contract validation boundary.
    // Legacy schema-v1 skins without a recipe normalize to standard@1.
    const registered = cloneAndFreeze({ ...manifest, styleRecipe });
    skins.set(registered.id, registered);
    return registered;
  }

  function get(id) {
    return skins.get(id) || null;
  }

  function list() {
    return Array.from(skins.values());
  }

  initialSkins.forEach(register);
  return { register, get, list };
}
