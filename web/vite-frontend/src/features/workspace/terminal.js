import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

export function createWorkspaceTerminalController({ state, container, panel, toggleButton }) {
  let terminal = null;
  let fitAddon = null;
  let socket = null;
  let pointerDown = false;
  let selectionReleasedAt = 0;
  let ctrlCKeyAt = 0;

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

  function start() {
    if (!container) return;
    if (socket?.readyState === WebSocket.OPEN) {
      terminal?.focus();
      resize();
      return;
    }
    container.innerHTML = "";
    terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      theme: { background: "#030712", foreground: "#d1fae5", cursor: "#7dd3fc", selectionBackground: "#1e40af88" },
    });
    fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(container);
    container.removeEventListener("copy", copySelection);
    container.addEventListener("copy", copySelection);
    container.removeEventListener("keydown", handleKeydown, true);
    container.addEventListener("keydown", handleKeydown, true);
    container.removeEventListener("pointerdown", handlePointerDown);
    container.addEventListener("pointerdown", handlePointerDown);
    container.removeEventListener("pointerup", handlePointerUp);
    container.addEventListener("pointerup", handlePointerUp);
    terminal.write("\r\nStarting workspace terminal...\r\n");
    fitAddon.fit();
    terminal.focus();

    socket = new WebSocket(socketUrl());
    socket.addEventListener("open", resize);
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "output") terminal?.write(message.data || "");
      } catch (_) {
        terminal?.write(String(event.data || ""));
      }
    });
    socket.addEventListener("close", () => terminal?.write("\r\n[terminal closed]\r\n"));
    socket.addEventListener("error", () => terminal?.write("\r\n[terminal connection error]\r\n"));
    terminal.onData((data) => {
      if (shouldSuppressInput(data)) return;
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
  }

  function stop() {
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
    if (open) start();
    else stop();
  }

  window.addEventListener("resize", resize);
  return { setOpen, resize };
}
