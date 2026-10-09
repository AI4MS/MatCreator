export const THEME_KEY = "mat_theme";
export const FONT_SCALE_KEY = "mat_fontScale";
export const FONT_SCALE_PRESETS = Object.freeze([90, 100, 110, 125, 150]);

export function normalizeFontScale(scale) {
  const numericScale = Number(scale);
  if (numericScale > 150) return 150;
  return FONT_SCALE_PRESETS.includes(numericScale) ? numericScale : 100;
}

export function createAppearanceController({
  state,
  textInput,
  themeToggle,
  storage = globalThis.localStorage,
  root = document.documentElement,
  body = document.body,
  windowRef = window,
}) {
  let initialized = false;
  let textInputResizeObserver = null;

  function storedValue(key) {
    try { return storage?.getItem(key); } catch (_) { return null; }
  }

  function persist(key, value) {
    try { storage?.setItem(key, value); } catch (_) { /* preference is non-critical */ }
  }

  function getFontScale() {
    return normalizeFontScale(storedValue(FONT_SCALE_KEY));
  }

  function applyFontScale(scale, { persist: shouldPersist = true } = {}) {
    const nextScale = normalizeFontScale(scale);
    root.style.setProperty("--font-scale", `${nextScale}%`);
    if (shouldPersist) persist(FONT_SCALE_KEY, String(nextScale));
    autoResizeTextInput();
    return nextScale;
  }

  function applyTheme(theme, { persist: shouldPersist = false } = {}) {
    const nextTheme = theme === "light" ? "light" : "dark";
    state.theme = nextTheme;
    body.dataset.theme = nextTheme;
    windowRef.dispatchEvent(new CustomEvent("matcreator-theme-change", { detail: nextTheme }));
    const toggleLabel = nextTheme === "light" ? "Toggle dark mode" : "Toggle light mode";
    themeToggle?.setAttribute("aria-pressed", String(nextTheme === "light"));
    themeToggle?.setAttribute("title", toggleLabel);
    themeToggle?.setAttribute("aria-label", toggleLabel);
    if (shouldPersist) persist(THEME_KEY, nextTheme);
    return nextTheme;
  }

  function autoResizeTextInput() {
    if (!textInput || !textInput.getClientRects().length) return;
    // Measure the full content at the current width; CSS owns the height cap.
    // Reset first so deleting text or clearing a sent message also shrinks it.
    textInput.style.height = "auto";
    textInput.style.overflowY = "hidden";
    textInput.style.height = `${textInput.scrollHeight}px`;
    textInput.style.overflowY = textInput.scrollHeight > textInput.clientHeight ? "auto" : "hidden";
  }

  const toggleTheme = () => {
    applyTheme(state.theme === "light" ? "dark" : "light", { persist: true });
  };

  function init() {
    if (initialized) return;
    initialized = true;
    applyTheme(state.theme);
    applyFontScale(getFontScale(), { persist: false });
    textInput?.addEventListener("input", autoResizeTextInput);
    windowRef.addEventListener("resize", autoResizeTextInput);
    if (textInput) {
      let previousWidth = -1;
      textInputResizeObserver = new windowRef.ResizeObserver(([entry]) => {
        const width = entry.contentRect.width;
        if (width === previousWidth) return;
        previousWidth = width;
        autoResizeTextInput();
      });
      // Covers sidebar resizing and returning to a previously hidden chat tab.
      // Ignore height-only changes to avoid reacting to our own resize.
      textInputResizeObserver.observe(textInput);
    }
    themeToggle?.addEventListener("click", toggleTheme);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    textInput?.removeEventListener("input", autoResizeTextInput);
    windowRef.removeEventListener("resize", autoResizeTextInput);
    textInputResizeObserver?.disconnect();
    textInputResizeObserver = null;
    themeToggle?.removeEventListener("click", toggleTheme);
  }

  return {
    init,
    destroy,
    getFontScale,
    applyFontScale,
    applyTheme,
    autoResizeTextInput,
  };
}
