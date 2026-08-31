export const SKIN_STORAGE_KEY = "mat_skin";
export const SKIN_VARIANT_STORAGE_KEY = "mat_skin_variant";
export const LEGACY_THEME_STORAGE_KEY = "mat_theme";

function createCustomEvent(eventTarget, type, detail) {
  const CustomEventConstructor = eventTarget?.CustomEvent || globalThis.CustomEvent;
  if (typeof CustomEventConstructor === "function") return new CustomEventConstructor(type, { detail });
  return { type, detail };
}

function storageValue(storage, key) {
  try {
    return storage?.getItem?.(key) || "";
  } catch (_) {
    return "";
  }
}

function snapshotStorage(storage, keys) {
  return new Map(keys.map((key) => {
    try {
      const value = storage?.getItem?.(key);
      return [key, { readable: true, value }];
    } catch (_) {
      return [key, { readable: false, value: null }];
    }
  }));
}

function restoreStorage(storage, snapshot) {
  for (const [key, entry] of snapshot) {
    if (!entry.readable) continue;
    try {
      if (entry.value === null || entry.value === undefined) storage?.removeItem?.(key);
      else storage?.setItem?.(key, entry.value);
    } catch (_) {}
  }
}

export function createThemeManager({
  registry,
  target,
  eventTarget,
  storage,
  defaultSkinId = "matcreator-default",
} = {}) {
  if (!registry?.get || !registry?.list) throw new TypeError("ThemeManager requires a registry");
  if (!target?.style || !target?.dataset) throw new TypeError("ThemeManager requires a styled target");

  let selection = null;
  let appliedTokenNames = new Set();
  const subscribers = new Set();

  function resolveSelection(skinId, variantId) {
    const skin = registry.get(skinId) || registry.get(defaultSkinId);
    if (!skin) throw new Error(`Default skin is not registered: ${defaultSkinId}`);
    const variant = skin.variants[variantId] ? variantId : skin.defaultVariant;
    return {
      skin,
      variant,
      colorScheme: skin.variants[variant].colorScheme,
      tokens: skin.variants[variant].tokens,
      styleRecipe: skin.styleRecipe,
    };
  }

  function dispatch(nextSelection) {
    // Local subscribers update shared UI state before legacy renderers receive
    // their window event. Those renderers still read state.theme synchronously.
    for (const subscriber of subscribers) {
      try {
        subscriber({ ...nextSelection });
      } catch (_) {}
    }
    eventTarget?.dispatchEvent?.(createCustomEvent(eventTarget, "matcreator-theme-change", nextSelection.colorScheme));
    eventTarget?.dispatchEvent?.(createCustomEvent(eventTarget, "matcreator-skin-change", nextSelection));
  }

  function apply({ skinId, variant, persist = true } = {}) {
    const resolved = resolveSelection(skinId || selection?.skinId || defaultSkinId, variant || selection?.variant);
    const previousSelection = selection ? { ...selection } : null;
    const previousDataset = {
      skin: target.dataset.skin,
      styleRecipe: target.dataset.styleRecipe,
      styleRecipeVersion: target.dataset.styleRecipeVersion,
      theme: target.dataset.theme,
      variant: target.dataset.variant,
    };
    const previousColorScheme = target.style.colorScheme;
    const previousStorage = persist
      ? snapshotStorage(storage, [SKIN_STORAGE_KEY, SKIN_VARIANT_STORAGE_KEY, LEGACY_THEME_STORAGE_KEY])
      : new Map();
    const touchedNames = new Set([...appliedTokenNames, ...Object.keys(resolved.tokens)]);
    const previousTokenValues = new Map(
      Array.from(touchedNames, (name) => [name, target.style.getPropertyValue?.(name) || ""]),
    );

    try {
      for (const tokenName of appliedTokenNames) {
        if (!Object.hasOwn(resolved.tokens, tokenName)) target.style.removeProperty?.(tokenName);
      }
      for (const [tokenName, value] of Object.entries(resolved.tokens)) {
        target.style.setProperty(tokenName, value);
      }
      target.dataset.skin = resolved.skin.id;
      target.dataset.styleRecipe = resolved.styleRecipe.id;
      target.dataset.styleRecipeVersion = String(resolved.styleRecipe.apiVersion);
      target.dataset.theme = resolved.colorScheme;
      target.dataset.variant = resolved.variant;
      target.style.colorScheme = resolved.colorScheme;

      const nextSelection = {
        skinId: resolved.skin.id,
        skinName: resolved.skin.name,
        styleRecipeId: resolved.styleRecipe.id,
        styleRecipeApiVersion: resolved.styleRecipe.apiVersion,
        variant: resolved.variant,
        colorScheme: resolved.colorScheme,
        version: resolved.skin.version,
      };

      if (persist) {
        storage?.setItem?.(SKIN_STORAGE_KEY, nextSelection.skinId);
        storage?.setItem?.(SKIN_VARIANT_STORAGE_KEY, nextSelection.variant);
        storage?.setItem?.(LEGACY_THEME_STORAGE_KEY, nextSelection.colorScheme);
      }

      appliedTokenNames = new Set(Object.keys(resolved.tokens));
      selection = nextSelection;
      dispatch(nextSelection);
      return { ...nextSelection };
    } catch (error) {
      for (const [tokenName, value] of previousTokenValues) {
        if (value) target.style.setProperty(tokenName, value);
        else target.style.removeProperty?.(tokenName);
      }
      for (const [key, value] of Object.entries(previousDataset)) {
        if (value === undefined) delete target.dataset[key];
        else target.dataset[key] = value;
      }
      target.style.colorScheme = previousColorScheme;
      selection = previousSelection;
      restoreStorage(storage, previousStorage);
      throw error;
    }
  }

  function initialize() {
    const skinId = storageValue(storage, SKIN_STORAGE_KEY) || defaultSkinId;
    const variant = storageValue(storage, SKIN_VARIANT_STORAGE_KEY)
      || storageValue(storage, LEGACY_THEME_STORAGE_KEY)
      || undefined;
    return apply({ skinId, variant, persist: false });
  }

  function toggleVariant() {
    const skin = registry.get(selection?.skinId || defaultSkinId);
    const variants = Object.keys(skin?.variants || {});
    if (!variants.length) return initialize();
    const oppositeScheme = selection?.colorScheme === "light" ? "dark" : "light";
    const preferred = variants.find((variantId) => skin.variants[variantId].colorScheme === oppositeScheme)
      || variants[(Math.max(0, variants.indexOf(selection?.variant)) + 1) % variants.length];
    return apply({ skinId: skin.id, variant: preferred });
  }

  function getSelection() {
    return selection ? { ...selection } : null;
  }

  function subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("Theme subscriber must be a function");
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  }

  return {
    apply,
    getSelection,
    initialize,
    listSkins: () => registry.list(),
    subscribe,
    toggleVariant,
  };
}
