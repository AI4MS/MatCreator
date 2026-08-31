let terminalRuntimePromise = null;

const TERMINAL_THEME_FALLBACK = Object.freeze({
  background: "#030712",
  foreground: "#d1fae5",
  cursor: "#7dd3fc",
  selectionBackground: "#1e40af88",
});

export function createWorkspaceTerminalTheme(
  body = document.body,
  readComputedStyle = (element) => window.getComputedStyle(element),
) {
  const styles = readComputedStyle(body);
  const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  return {
    background: token("--terminal-bg", TERMINAL_THEME_FALLBACK.background),
    foreground: token("--terminal-text", TERMINAL_THEME_FALLBACK.foreground),
    cursor: token("--accent-primary", TERMINAL_THEME_FALLBACK.cursor),
    selectionBackground: token("--selection", TERMINAL_THEME_FALLBACK.selectionBackground),
  };
}

function loadTerminalRuntime() {
  terminalRuntimePromise ||= Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-fit"),
    import("@xterm/xterm/css/xterm.css"),
  ])
    .then(([terminalModule, fitModule]) => ({
      Terminal: terminalModule.Terminal,
      FitAddon: fitModule.FitAddon,
    }))
    .catch((error) => {
      terminalRuntimePromise = null;
      throw error;
    });
  return terminalRuntimePromise;
}

export function createWorkspaceTerminalController({ state, container, panel, toggleButton }) {
  let terminal = null;
  let fitAddon = null;
  let socket = null;
  let pointerDown = false;
  let selectionReleasedAt = 0;
  let ctrlCKeyAt = 0;
  let lifecycleVersion = 0;

  function socketUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const params = new URLSearchParams();
    if (state.deploymentMode === "server" && state.userId) params.set("user_id", state.userId);
    const query = params.toString();
    return `${protocol}//${window.location.host}/api/workspace/terminal${query ? `?${query}` : ""}`;
  }

  function resize() {
    if (!terminal || !fitAddon || !socket) return;
    try {
      fitAddon.fit();
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", rows: terminal.rows, cols: terminal.cols }));
      }
    } catch (_) {
      // The terminal can be hidden while the browser computes its dimensions.
    }
  }

  function copySelection(event) {
    if (!terminal?.hasSelection?.()) return;
    const selectedText = terminal.getSelection();
    if (!selectedText) return;
    event.preventDefault();
    event.clipboardData?.setData("text/plain", selectedText);
    navigator.clipboard?.writeText(selectedText).catch(() => {});
  }

  function writeSelectionToClipboard() {
    if (!terminal?.hasSelection?.()) return false;
    const selectedText = terminal.getSelection();
    if (!selectedText) return false;
    navigator.clipboard?.writeText(selectedText).catch(() => {});
    return true;
  }

  function handleKeydown(event) {
    const isCopyKey = (event.ctrlKey || event.metaKey) && event.key?.toLowerCase?.() === "c";
    if (!isCopyKey) return;
    ctrlCKeyAt = Date.now();
    if (!terminal?.hasSelection?.()) return;
    event.preventDefault();
    event.stopPropagation();
    writeSelectionToClipboard();
  }

  function handlePointerDown() {
    pointerDown = true;
  }

  function handlePointerUp() {
    if (pointerDown && terminal?.hasSelection?.()) selectionReleasedAt = Date.now();
    pointerDown = false;
  }

  function shouldSuppressInput(data) {
    if (data !== "\x03") return false;
    const now = Date.now();
    return now - selectionReleasedAt < 500 && now - ctrlCKeyAt >= 500;
  }

  function applyTerminalTheme() {
    if (!terminal) return;
    terminal.options.theme = createWorkspaceTerminalTheme();
  }

  async function start() {
    if (!container) return;
    if (socket?.readyState === WebSocket.OPEN) {
      terminal?.focus();
      resize();
      return;
    }
    const version = ++lifecycleVersion;
    let runtime;
    try {
      runtime = await loadTerminalRuntime();
    } catch (error) {
      if (version === lifecycleVersion) {
        container.textContent = `Unable to load the workspace terminal: ${String(error?.message || error)}`;
      }
      return;
    }
    if (version !== lifecycleVersion) return;
    const { Terminal, FitAddon } = runtime;
    socket?.close();
    socket = null;
    terminal?.dispose();
    terminal = null;
    fitAddon = null;
    container.innerHTML = "";
    const terminalInstance = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      theme: createWorkspaceTerminalTheme(),
    });
    const fitAddonInstance = new FitAddon();
    terminal = terminalInstance;
    fitAddon = fitAddonInstance;
    terminalInstance.loadAddon(fitAddonInstance);
    terminalInstance.open(container);
    container.removeEventListener("copy", copySelection);
    container.addEventListener("copy", copySelection);
    container.removeEventListener("keydown", handleKeydown, true);
    container.addEventListener("keydown", handleKeydown, true);
    container.removeEventListener("pointerdown", handlePointerDown);
    container.addEventListener("pointerdown", handlePointerDown);
    container.removeEventListener("pointerup", handlePointerUp);
    container.addEventListener("pointerup", handlePointerUp);
    terminalInstance.write("\r\nStarting workspace terminal...\r\n");
    fitAddonInstance.fit();
    terminalInstance.focus();

    const socketInstance = new WebSocket(socketUrl());
    socket = socketInstance;
    socketInstance.addEventListener("open", () => {
      if (socket === socketInstance) resize();
    });
    socketInstance.addEventListener("message", (event) => {
      if (socket !== socketInstance) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === "output") terminalInstance.write(message.data || "");
      } catch (_) {
        terminalInstance.write(String(event.data || ""));
      }
    });
    socketInstance.addEventListener("close", () => {
      if (socket === socketInstance) terminalInstance.write("\r\n[terminal closed]\r\n");
    });
    socketInstance.addEventListener("error", () => {
      if (socket === socketInstance) terminalInstance.write("\r\n[terminal connection error]\r\n");
    });
    terminalInstance.onData((data) => {
      if (shouldSuppressInput(data)) return;
      if (socket === socketInstance && socketInstance.readyState === WebSocket.OPEN) {
        socketInstance.send(JSON.stringify({ type: "input", data }));
      }
    });
  }

  function stop() {
    lifecycleVersion += 1;
    socket?.close();
    socket = null;
    terminal?.dispose();
    terminal = null;
    fitAddon = null;
    if (container) {
      container.removeEventListener("copy", copySelection);
      container.removeEventListener("keydown", handleKeydown, true);
      container.removeEventListener("pointerdown", handlePointerDown);
      container.removeEventListener("pointerup", handlePointerUp);
      container.innerHTML = "";
    }
  }

  function setOpen(open) {
    panel?.classList.toggle("hidden", !open);
    toggleButton?.classList.toggle("is-active", open);
    toggleButton?.setAttribute("aria-expanded", String(open));
    if (open) void start();
    else stop();
  }

  function destroy() {
    stop();
    window.removeEventListener("resize", resize);
    window.removeEventListener("matcreator-skin-change", applyTerminalTheme);
  }

  window.addEventListener("resize", resize);
  window.addEventListener("matcreator-skin-change", applyTerminalTheme);
  return { setOpen, resize, destroy };
}
