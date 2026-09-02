let structureViewerModulePromise = null;
let svelteRuntimePromise = null;

function loadStructureViewerModules() {
  structureViewerModulePromise ||= import("../../structure/StructureViewer.svelte");
  svelteRuntimePromise ||= import("svelte");
  return Promise.all([structureViewerModulePromise, svelteRuntimePromise]);
}

export function structureTabId(path) {
  let hash = 0;
  const source = String(path || "");
  for (let index = 0; index < source.length; index += 1) {
    hash = ((hash << 5) - hash + source.charCodeAt(index)) | 0;
  }
  return `structure-${Math.abs(hash)}`;
}

export function structureTabTitle(path) {
  const filename = String(path || "Structure").split(/[\\/]/).filter(Boolean).pop();
  return filename || "Structure";
}

function loadingMessage(text) {
  const message = document.createElement("div");
  message.className = "viewer-loading-message";
  message.textContent = text;
  return message;
}

export function createWorkspaceViewerController({
  state,
  elements,
  skillGraphController,
  lightbox,
  classifyPath,
  pathToApiUrl,
  refreshSessionFiles,
  fetchImpl = globalThis.fetch,
}) {
  const {
    tabs,
    tabsScroll,
    panels,
    graphDetail,
    fileModal,
    fileContent,
    fileName,
    fileCloseButton,
  } = elements;
  const structureTabs = new Map();
  let initialized = false;
  let preloadHandle = null;
  let fileRequestController = null;

  function uniqueTabId(path) {
    const baseId = structureTabId(path);
    let tabId = baseId;
    let suffix = 1;
    while (structureTabs.has(tabId) && structureTabs.get(tabId).item.path !== path) {
      tabId = `${baseId}-${suffix}`;
      suffix += 1;
    }
    return tabId;
  }

  function activate(tabId) {
    state.activeCenterTabId = tabId;
    tabsScroll?.querySelectorAll(".center-tab")?.forEach((tab) => {
      const active = tab.dataset.tabId === tabId;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    panels?.querySelectorAll(".center-tab-panel")?.forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.tabId === tabId);
    });
    state.structure3dViewer = structureTabs.get(tabId)?.viewer || null;
    skillGraphController.activate(tabId);
  }

  async function destroyStructureTab(tab) {
    tab.loadController?.abort();
    tab.loadController = null;
    if (tab.destroyViewer) await tab.destroyViewer();
    tab.viewer = null;
    tab.destroyViewer = null;
  }

  function close(tabId) {
    if (skillGraphController.close(tabId)) {
      if (state.activeCenterTabId === tabId) activate("chat");
      return true;
    }
    const tab = structureTabs.get(tabId);
    if (!tab) return false;
    void destroyStructureTab(tab);
    tab.button.remove();
    tab.panel.remove();
    structureTabs.delete(tabId);
    if (state.activeCenterTabId === tabId) activate("chat");
    return true;
  }

  function createStructureTab(item) {
    const tabId = uniqueTabId(item.path);
    const existing = structureTabs.get(tabId);
    if (existing) {
      activate(tabId);
      return existing;
    }

    const button = document.createElement("button");
    button.className = "center-tab";
    button.type = "button";
    button.role = "tab";
    button.dataset.tabId = tabId;
    button.id = `tab-${tabId}`;
    button.setAttribute("aria-selected", "false");
    button.setAttribute("aria-controls", `${tabId}-panel`);
    button.title = item.path;

    const title = document.createElement("span");
    title.className = "center-tab-title";
    title.textContent = structureTabTitle(item.path);
    const closeButton = document.createElement("span");
    closeButton.className = "center-tab-close";
    closeButton.dataset.closeTabId = tabId;
    closeButton.setAttribute("aria-hidden", "true");
    closeButton.textContent = "×";
    button.append(title, closeButton);

    const panel = document.createElement("div");
    panel.className = "center-tab-panel structure-tab-panel";
    panel.id = `${tabId}-panel`;
    panel.role = "tabpanel";
    panel.dataset.tabId = tabId;
    panel.setAttribute("aria-labelledby", button.id);
    const header = document.createElement("div");
    header.className = "structure-tab-header";
    const labelWrap = document.createElement("div");
    const eyebrow = document.createElement("div");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "Structure";
    const meta = document.createElement("div");
    meta.className = "sv-meta";
    labelWrap.append(eyebrow, meta);
    header.appendChild(labelWrap);
    const canvas = document.createElement("div");
    canvas.className = "sv-canvas structure-tab-canvas";
    panel.append(header, canvas);
    tabsScroll?.appendChild(button);
    panels?.appendChild(panel);

    const tab = {
      id: tabId,
      item,
      button,
      panel,
      canvas,
      meta,
      viewer: null,
      destroyViewer: null,
      loadController: null,
    };
    structureTabs.set(tabId, tab);
    activate(tabId);
    return tab;
  }

  async function openStructure(item) {
    graphDetail?.classList.add("hidden");
    const tab = createStructureTab(item);
    if (tab.viewer || tab.loadController) return;
    await destroyStructureTab(tab);
    const controller = new AbortController();
    tab.loadController = controller;
    tab.canvas.replaceChildren(loadingMessage("Loading…"));
    tab.meta.textContent = "";
    const sessionId = state.sessionId || "";

    try {
      const [response, [structureViewer, svelte]] = await Promise.all([
        fetchImpl(
          `/api/structure/view?path=${encodeURIComponent(item.path)}`
            + `&session_id=${encodeURIComponent(sessionId)}`,
          { signal: controller.signal },
        ),
        loadStructureViewerModules(),
      ]);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (controller.signal.aborted || !structureTabs.has(tab.id)) return;

      tab.canvas.replaceChildren();
      const structureMeta = `${data.formula}  ·  ${data.n_atoms} atoms`
        + `${data.periodic ? "  ·  periodic" : ""}`;
      const viewer = svelte.mount(structureViewer.default, {
        target: tab.canvas,
        props: {
          structure_string: data.structure_string || data.xyz,
          source_path: item.path,
          session_id: sessionId,
          background_color: state.theme === "light" ? "#f8fbff" : "#06080f",
          performance_mode: data.n_atoms > 500 ? "speed" : "quality",
          on_modified: () => {
            tab.meta.textContent = `${structureMeta}  ·  unsaved atom edits`;
          },
          on_generated: (generated) => {
            const generatedMeta = `${generated.formula}  ·  ${generated.n_atoms} atoms`;
            tab.meta.textContent = `${generatedMeta}  ·  ${generated.operation}  ·  saved`;
            void refreshSessionFiles();
          },
        },
      });
      tab.viewer = viewer;
      tab.destroyViewer = () => svelte.unmount(viewer);
      if (state.activeCenterTabId === tab.id) state.structure3dViewer = viewer;
      tab.meta.textContent = `${structureMeta}  ·  Select an atom to edit it`;
    } catch (error) {
      if (error.name === "AbortError") return;
      const message = document.createElement("div");
      message.className = "viewer-load-error";
      message.textContent = `Failed to load structure: ${String(error?.message || error)}`;
      tab.canvas.replaceChildren(message);
    } finally {
      if (tab.loadController === controller) tab.loadController = null;
    }
  }

  function closeFile() {
    fileRequestController?.abort();
    fileRequestController = null;
    fileModal?.classList.add("hidden");
  }

  async function openFile(file) {
    if (!fileModal || !fileContent) return;
    fileRequestController?.abort();
    const controller = new AbortController();
    fileRequestController = controller;
    if (fileName) fileName.textContent = file.name;
    fileContent.replaceChildren(loadingMessage("Loading…"));
    fileModal.classList.remove("hidden");
    const type = classifyPath(file.path);
    const url = pathToApiUrl(file.path);

    if (type === "image") {
      const wrap = document.createElement("div");
      wrap.className = "fv-img-wrap";
      const image = document.createElement("img");
      image.src = url;
      image.alt = file.name;
      image.style.cursor = "zoom-in";
      image.addEventListener("click", () => lightbox.open(url));
      wrap.appendChild(image);
      fileContent.replaceChildren(wrap);
      fileRequestController = null;
      return;
    }

    try {
      const response = await fetchImpl(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const text = await response.text();
      if (controller.signal.aborted || fileRequestController !== controller) return;
      if (text.includes("\0")) {
        const binary = document.createElement("p");
        binary.className = "file-viewer-notice";
        binary.textContent = "Binary file — cannot preview.";
        fileContent.replaceChildren(binary);
        return;
      }
      const pre = document.createElement("pre");
      pre.className = "fv-pre";
      pre.textContent = text;
      fileContent.replaceChildren(pre);
    } catch (error) {
      if (error.name === "AbortError") return;
      const message = document.createElement("p");
      message.className = "file-viewer-error";
      message.textContent = `Failed to load: ${error.message}`;
      fileContent.replaceChildren(message);
    } finally {
      if (fileRequestController === controller) fileRequestController = null;
    }
  }

  const handleTabClick = (event) => {
    const closeElement = event.target.closest("[data-close-tab-id]");
    if (closeElement) {
      event.stopPropagation();
      close(closeElement.dataset.closeTabId);
      return;
    }
    const tab = event.target.closest(".center-tab");
    if (tab?.dataset.tabId) activate(tab.dataset.tabId);
  };
  const handleFileModalClick = (event) => {
    if (event.target === event.currentTarget) closeFile();
  };

  function init() {
    if (initialized) return;
    initialized = true;
    tabs?.addEventListener("click", handleTabClick);
    fileCloseButton?.addEventListener("click", closeFile);
    fileModal?.addEventListener("click", handleFileModalClick);
    preloadHandle = window.requestIdleCallback
      ? window.requestIdleCallback(() => void loadStructureViewerModules(), { timeout: 2000 })
      : window.setTimeout(() => void loadStructureViewerModules(), 1000);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    tabs?.removeEventListener("click", handleTabClick);
    fileCloseButton?.removeEventListener("click", closeFile);
    fileModal?.removeEventListener("click", handleFileModalClick);
    if (window.cancelIdleCallback && preloadHandle !== null) window.cancelIdleCallback(preloadHandle);
    else if (preloadHandle !== null) window.clearTimeout(preloadHandle);
    preloadHandle = null;
    closeFile();
    structureTabs.forEach((tab) => void destroyStructureTab(tab));
    structureTabs.clear();
  }

  return { init, destroy, activate, close, openStructure, openFile, closeFile };
}
