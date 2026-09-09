function normalizedUploads(files) {
  return Array.isArray(files) ? files.filter(Boolean) : [];
}

export function mergeUploadedFiles(existingFiles = [], newFiles = []) {
  const merged = [...normalizedUploads(existingFiles)];
  const seenPaths = new Set(merged.map((file) => file.path).filter(Boolean));

  normalizedUploads(newFiles).forEach((file) => {
    if (file.path && seenPaths.has(file.path)) return;
    if (file.path) seenPaths.add(file.path);
    merged.push(file);
  });

  return merged;
}

export function sessionRelativeUploadPath(file, sessionId) {
  if (file?.relative_path) return file.relative_path;
  const normalized = String(file?.path || "").replaceAll("\\", "/");
  const marker = sessionId ? `/${sessionId}/` : "";
  const markerIndex = marker ? normalized.indexOf(marker) : -1;
  if (markerIndex >= 0) return normalized.slice(markerIndex + marker.length);
  return file?.name ? `uploads/${file.name}` : normalized;
}

export function buildUploadTree(files = [], sessionId = "") {
  const root = { folders: new Map(), files: [] };
  for (const file of normalizedUploads(files)) {
    const path = sessionRelativeUploadPath(file, sessionId).replace(/^uploads\//, "");
    const parts = path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] });
      node = node.folders.get(part);
    }
    node.files.push(file);
  }
  return root;
}

export function formatUploadNames(uploadNames = []) {
  const names = uploadNames.filter(Boolean);
  if (!names.length) return "";
  return `Attached: ${names.map((name) => `\`${name}\``).join(", ")}`;
}

export function messageWithUploadContext(message, uploads = [], sessionId = "") {
  const files = normalizedUploads(uploads);
  if (!files.length) return message;
  const fileLines = files.map((file) => {
    const relativePath = sessionRelativeUploadPath(file, sessionId);
    return `- ${file.name}: ${relativePath} (absolute path: ${file.path})`;
  });
  return [
    message,
    "",
    "The user uploaded the following file(s) for this message. They are saved in the current session workspace. Use these paths when inspecting or processing the files:",
    ...fileLines,
  ].join("\n");
}

export function messageWithUploadNames(message, uploads = []) {
  const suffix = formatUploadNames(normalizedUploads(uploads).map((file) => file.name));
  return suffix ? `${message}\n\n${suffix}` : message;
}

export function displayMessageFromStoredUserText(message) {
  const marker = "\n\nThe user uploaded the following file(s) for this message.";
  const rawMessage = String(message || "");
  const markerIndex = rawMessage.indexOf(marker);
  if (markerIndex < 0) return rawMessage;

  const visibleMessage = rawMessage.slice(0, markerIndex);
  const hiddenContext = rawMessage.slice(markerIndex);
  const uploadNames = hiddenContext
    .split("\n")
    .map((line) => line.match(/^-\s+([^:]+):/)?.[1]?.trim())
    .filter(Boolean);
  const suffix = formatUploadNames(uploadNames);
  return suffix ? `${visibleMessage}\n\n${suffix}` : visibleMessage;
}

export function createSessionUploadsController({
  state,
  elements = {},
  ensureSession,
  refreshFiles,
  showLogin,
  canWrite,
  showReadOnlyMessage,
  fetchImpl = globalThis.fetch,
}) {
  const { button, input, folderButton, folderInput, menu, filesChoice, status } = elements;
  let initialized = false;
  let activeController = null;

  function setStatus(message, tone = "idle") {
    if (!status) return;
    status.textContent = message || "";
    status.className = `upload-status upload-status-${tone}`;
  }

  function render() {
    if (!status) return;
    status.replaceChildren();
    status.className = "upload-status upload-file-list";

    function renderNode(node, container) {
      for (const [folderName, child] of node.folders) {
        const folder = document.createElement("details");
        folder.className = "upload-folder";
        const summary = document.createElement("summary");
        summary.textContent = `📁 ${folderName}`;
        const children = document.createElement("div");
        children.className = "upload-folder-children";
        renderNode(child, children);
        folder.append(summary, children);
        container.appendChild(folder);
      }
      node.files.forEach((file) => {
      const chip = document.createElement("span");
      chip.className = "upload-file-chip";

      const name = document.createElement("span");
      name.className = "upload-file-name";
      name.textContent = file.name;
      name.title = file.path;

      const removeButton = document.createElement("button");
      removeButton.className = "upload-file-remove";
      removeButton.type = "button";
      removeButton.title = "Delete uploaded file";
      removeButton.textContent = "×";
      removeButton.addEventListener("click", () => remove(file));

      chip.append(name, removeButton);
      container.appendChild(chip);
      });
    }
    renderNode(buildUploadTree(state.currentUploads, state.sessionId), status);
  }

  function clear() {
    state.currentUploads = [];
    render();
  }

  async function remove(file) {
    const sessionId = state.sessionId;
    if (!file?.path || !sessionId) return false;
    try {
      const response = await fetchImpl(
        `/api/sessions/${encodeURIComponent(sessionId)}/files?path=${encodeURIComponent(file.path)}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `HTTP ${response.status}`);
      }
      if (state.sessionId === sessionId) {
        state.currentUploads = normalizedUploads(state.currentUploads)
          .filter((item) => item.path !== file.path);
        render();
        await refreshFiles?.(sessionId);
      }
      return true;
    } catch (error) {
      setStatus(`Delete failed: ${error.message || error}`, "error");
      return false;
    }
  }

  async function upload(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return [];
    if (!state.userId) {
      showLogin?.();
      return [];
    }
    if (!canWrite?.()) {
      showReadOnlyMessage?.();
      return [];
    }

    if (!state.sessionReady) await ensureSession?.();
    if (!state.sessionReady) {
      setStatus("Could not create session.", "error");
      return [];
    }

    const sessionId = state.sessionId;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    if (button) button.disabled = true;
    if (folderButton) folderButton.disabled = true;
    const uploaded = [];

    try {
      for (const file of files) {
        setStatus(`Uploading ${file.webkitRelativePath || file.name} (${uploaded.length + 1}/${files.length})...`, "busy");
        const formData = new FormData();
        formData.append("file", file);
        if (file.webkitRelativePath) formData.append("relative_path", file.webkitRelativePath);
        const response = await fetchImpl(`/api/sessions/${encodeURIComponent(sessionId)}/files`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail || `HTTP ${response.status}`);
        }
        uploaded.push(await response.json());
      }

      if (state.sessionId === sessionId) {
        await refreshFiles?.(sessionId);
        state.currentUploads = mergeUploadedFiles(state.currentUploads, uploaded);
        render();
      }
      return uploaded;
    } catch (error) {
      if (state.sessionId === sessionId) {
        state.currentUploads = mergeUploadedFiles(state.currentUploads, uploaded);
        await refreshFiles?.(sessionId);
      }
      if (error.name !== "AbortError") {
        setStatus(`Upload failed: ${error.message || error}`, "error");
      }
      return uploaded;
    } finally {
      if (activeController === controller) {
        activeController = null;
        if (button) button.disabled = false;
        if (folderButton) folderButton.disabled = false;
        if (input) input.value = "";
        if (folderInput) folderInput.value = "";
      }
    }
  }

  function closeMenu() {
    if (menu) menu.hidden = true;
    button?.setAttribute("aria-expanded", "false");
  }
  const handleButtonClick = () => {
    if (!menu) return input?.click();
    menu.hidden = !menu.hidden;
    button?.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) filesChoice?.focus();
  };
  const handleFilesClick = () => { closeMenu(); input?.click(); };
  const handleFolderClick = () => { closeMenu(); folderInput?.click(); };
  const handleOutsideClick = (event) => {
    if (!menu?.contains(event.target) && !button?.contains(event.target)) closeMenu();
  };
  const handleEscape = (event) => {
    if (event.key === "Escape" && menu && !menu.hidden) {
      closeMenu();
      button?.focus();
    }
  };
  const handleInputChange = (event) => upload(event.target.files);

  function init() {
    if (initialized) return;
    initialized = true;
    button?.addEventListener("click", handleButtonClick);
    filesChoice?.addEventListener("click", handleFilesClick);
    if (menu) {
      document.addEventListener("click", handleOutsideClick);
      document.addEventListener("keydown", handleEscape);
    }
    input?.addEventListener("change", handleInputChange);
    folderButton?.addEventListener("click", handleFolderClick);
    folderInput?.addEventListener("change", handleInputChange);
    render();
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    activeController?.abort();
    activeController = null;
    closeMenu();
    button?.removeEventListener("click", handleButtonClick);
    filesChoice?.removeEventListener("click", handleFilesClick);
    if (menu) {
      document.removeEventListener("click", handleOutsideClick);
      document.removeEventListener("keydown", handleEscape);
    }
    input?.removeEventListener("change", handleInputChange);
    folderButton?.removeEventListener("click", handleFolderClick);
    folderInput?.removeEventListener("change", handleInputChange);
  }

  return {
    init,
    destroy,
    clear,
    render,
    remove,
    upload,
    setStatus,
    messageWithUploadContext: (message, uploads) => (
      messageWithUploadContext(message, uploads, state.sessionId)
    ),
    messageWithUploadNames,
    displayMessageFromStoredUserText,
  };
}
