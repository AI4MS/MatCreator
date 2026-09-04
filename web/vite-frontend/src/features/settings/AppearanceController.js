function appendText(parent, tagName, className, text) {
  const element = parent.ownerDocument.createElement(tagName);
  element.className = className;
  element.textContent = text;
  parent.appendChild(element);
  return element;
}

export function createAppearanceController({ themeManager, document: documentRef = document } = {}) {
  if (!themeManager?.listSkins || !themeManager?.apply || !themeManager?.getSelection) {
    throw new TypeError("AppearanceController requires a ThemeManager");
  }

  const skinOptions = documentRef.getElementById("settings-skin-options");
  const variantOptions = documentRef.getElementById("settings-skin-variant-options");
  const status = documentRef.getElementById("settings-appearance-status");
  if (!skinOptions || !variantOptions) throw new Error("Appearance settings containers are missing");

  function setStatus(message, tone = "idle") {
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone;
  }

  function createSkinOption(skin, selection) {
    const label = documentRef.createElement("label");
    label.className = "settings-skin-option";
    if (skin.id === selection?.skinId) label.classList.add("is-selected");

    const input = documentRef.createElement("input");
    input.type = "radio";
    input.name = "settings-skin";
    input.value = skin.id;
    input.checked = skin.id === selection?.skinId;
    input.className = "settings-choice-input";

    const content = documentRef.createElement("span");
    content.className = "settings-skin-copy";
    appendText(content, "strong", "settings-skin-name", skin.name);
    appendText(content, "span", "settings-skin-description", skin.description || "Visual skin");

    const swatches = documentRef.createElement("span");
    swatches.className = "settings-skin-swatches";
    swatches.setAttribute("aria-hidden", "true");
    for (const color of skin.preview?.swatches || []) {
      const swatch = documentRef.createElement("span");
      swatch.className = "settings-skin-swatch";
      swatch.style.backgroundColor = color;
      swatches.appendChild(swatch);
    }

    const marker = documentRef.createElement("span");
    marker.className = "settings-skin-marker";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = input.checked ? "✓" : "";
    label.append(input, swatches, content, marker);
    return label;
  }

  function createVariantOption(skin, variantId, selection) {
    const variant = skin.variants[variantId];
    const label = documentRef.createElement("label");
    label.className = "settings-variant-option";
    if (variantId === selection?.variant) label.classList.add("is-selected");

    const input = documentRef.createElement("input");
    input.type = "radio";
    input.name = "settings-skin-variant";
    input.value = variantId;
    input.checked = variantId === selection?.variant;
    input.className = "settings-choice-input";
    label.appendChild(input);
    appendText(label, "span", "settings-variant-label", variant.label || variantId);
    return label;
  }

  function sync(nextSelection = themeManager.getSelection()) {
    const skins = themeManager.listSkins();
    const selection = nextSelection || {
      skinId: skins[0]?.id,
      variant: skins[0]?.defaultVariant,
    };
    const activeSkin = skins.find((skin) => skin.id === selection.skinId) || skins[0];

    skinOptions.replaceChildren(...skins.map((skin) => createSkinOption(skin, selection)));
    variantOptions.replaceChildren(
      ...Object.keys(activeSkin?.variants || {}).map((variantId) => createVariantOption(activeSkin, variantId, selection)),
    );
    return selection;
  }

  function onSkinChange(event) {
    const input = event.target.closest?.("input[name='settings-skin']");
    if (!input) return;
    const skin = themeManager.listSkins().find((candidate) => candidate.id === input.value);
    if (!skin) return;
    const current = themeManager.getSelection();
    const variant = skin.variants[current?.variant] ? current.variant : skin.defaultVariant;
    try {
      const applied = themeManager.apply({ skinId: skin.id, variant });
      sync(applied);
      setStatus(`${skin.name} applied and saved on this device.`, "success");
    } catch (error) {
      sync();
      setStatus(`Skin could not be applied: ${error.message}`, "error");
    }
  }

  function onVariantChange(event) {
    const input = event.target.closest?.("input[name='settings-skin-variant']");
    if (!input) return;
    const current = themeManager.getSelection();
    try {
      const applied = themeManager.apply({ skinId: current?.skinId, variant: input.value });
      sync(applied);
      setStatus(`${applied.skinName} · ${input.value} applied.`, "success");
    } catch (error) {
      sync();
      setStatus(`Variant could not be applied: ${error.message}`, "error");
    }
  }

  skinOptions.addEventListener("change", onSkinChange);
  variantOptions.addEventListener("change", onVariantChange);
  const unsubscribe = themeManager.subscribe((nextSelection) => sync(nextSelection));

  function destroy() {
    skinOptions.removeEventListener("change", onSkinChange);
    variantOptions.removeEventListener("change", onVariantChange);
    unsubscribe?.();
  }

  return { destroy, sync };
}
