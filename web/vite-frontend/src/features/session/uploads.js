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
  const normalized = String(file?.path || "").replaceAll("\\", "/");
  const marker = sessionId ? `/${sessionId}/` : "";
  const markerIndex = marker ? normalized.indexOf(marker) : -1;
  if (markerIndex >= 0) return normalized.slice(markerIndex + marker.length);
  return file?.name ? `uploads/${file.name}` : normalized;
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
  const { button, input, status } = elements;
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

    normalizedUploads(state.currentUploads).forEach((file) => {
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
      status.appendChild(chip);
    });
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
    const uploaded = [];

    try {
      for (const file of files) {
        setStatus(`Uploading ${file.name}...`, "busy");
        const formData = new FormData();
        formData.append("file", file);
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
      if (error.name !== "AbortError") {
        setStatus(`Upload failed: ${error.message || error}`, "error");
      }
      return uploaded;
    } finally {
      if (activeController === controller) {
        activeController = null;
        if (button) button.disabled = false;
        if (input) input.value = "";
      }
    }
  }

  const handleButtonClick = () => input?.click();
  const handleInputChange = (event) => upload(event.target.files);

  function init() {
    if (initialized) return;
    initialized = true;
    button?.addEventListener("click", handleButtonClick);
    input?.addEventListener("change", handleInputChange);
    render();
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    activeController?.abort();
    activeController = null;
    button?.removeEventListener("click", handleButtonClick);
    input?.removeEventListener("change", handleInputChange);
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
