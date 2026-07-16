import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

export function createWorkspaceTerminalController({ state, container, panel, toggleButton }) {
  let terminal = null;
  let fitAddon = null;
  let socket = null;

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
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
  }

  function stop() {
    socket?.close();
    socket = null;
    terminal?.dispose();
    terminal = null;
    fitAddon = null;
    if (container) container.innerHTML = "";
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
