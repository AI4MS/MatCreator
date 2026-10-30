// Control-panel dashboard: machine statistics (services, CPU / memory, GPUs,
// busy processes, dev-stack logs) rendered with theme tokens only, so it
// follows whichever skin/variant the user has selected.
//
// Two display modes share this controller:
//   - a center tab inside the main window (same pattern as the skill graph),
//   - a standalone page (dashboard.html) meant to be dragged to a second
//     monitor, mounted via renderStandalone().

const TAB_ID = "dashboard";
const STATUS_POLL_MS = 3000;
const LOG_POLL_MS = 10000;
const RING_RADIUS = 50;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;
const LOG_NAMES = ["api-server", "web-main", "vite"];
const LOG_STORAGE_KEY = "mat_dashboardLog";

function esc(value) {
  return String(value).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

// Donut gauge with the numeric value in the center. Ring color follows load:
// green below 60, amber below 85, red beyond.
function ringSvg(pct, label, sub) {
  const p = Math.max(0, Math.min(100, pct || 0));
  const level = p >= 85 ? "bad" : p >= 60 ? "warn" : "ok";
  return `<svg class="dash-ring" viewBox="0 0 120 120" role="img" aria-label="${esc(label)} ${Math.round(p)}%">
    <circle class="dash-ring-track" cx="60" cy="60" r="${RING_RADIUS}"></circle>
    <circle class="dash-ring-value ${level}" cx="60" cy="60" r="${RING_RADIUS}"
      stroke-dasharray="${RING_CIRCUMFERENCE.toFixed(1)}"
      stroke-dashoffset="${(RING_CIRCUMFERENCE * (1 - p / 100)).toFixed(1)}"></circle>
    <text class="dash-ring-number" x="60" y="50">${Math.round(p)}<tspan class="dash-ring-unit">%</tspan></text>
    <text class="dash-ring-label" x="60" y="80">${esc(label)}</text>
    <text class="dash-ring-sub" x="60" y="96">${esc(sub || "")}</text>
  </svg>`;
}

function serviceRowHtml(service) {
  const chip = service.running
    ? '<span class="dash-chip ok">Running</span>'
    : '<span class="dash-chip bad">Down</span>';
  return `<div class="dash-service">
    <span class="dash-dot ${service.running ? "ok" : "bad"}"></span>
    <span class="dash-service-name">${esc(service.label)}</span>
    ${chip}
    <span class="dash-port">:${service.port}</span>
  </div>`;
}

function processTableHtml(headers, rows) {
  if (!rows.length) return "";
  return `<table class="dash-table">
    <tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>
    ${rows.map((cells) => `<tr>${cells.map((c) => `<td${c.num ? ' class="num"' : ""}>${c.html}</td>`).join("")}</tr>`).join("")}
  </table>`;
}

function cpuCardHtml(data) {
  const cpu = data.cpu || { available: false };
  if (!cpu.available) {
    return `<section class="dash-card"><h2>CPU / Memory</h2>
      <p class="dash-empty">${esc(cpu.reason || "CPU statistics are not available on this machine.")}</p>
    </section>`;
  }
  const cores = cpu.cores.map((core) =>
    `<div class="dash-core" title="${esc(core.name)} ${core.usage}%"><i style="height:${Math.min(100, core.usage)}%"></i></div>`).join("");
  const load = Array.isArray(cpu.load)
    ? `<p class="dash-meta">Load ${cpu.load.join(" · ")} <span class="dash-dim">(1/5/15 min)</span></p>`
    : "";
  const memory = data.memory
    ? ringSvg(data.memory.percent, "Memory", `${data.memory.used_gib} / ${data.memory.total_gib} GiB`)
    : `<p class="dash-empty">Memory statistics are not available on this machine.</p>`;
  const procs = processTableHtml(
    ["User", "PID", "CPU%", "Process"],
    (data.top_processes || []).map((p) => [
      { html: esc(p.user) },
      { html: String(p.pid) },
      { html: String(p.cpu), num: true },
      { html: esc(p.name) },
    ]),
  ) || '<p class="dash-empty">No process above 0.5% CPU right now.</p>';
  return `<section class="dash-card">
    <h2>CPU / Memory</h2>
    <div class="dash-gauges">
      ${ringSvg(cpu.total, "CPU", `${cpu.count} cores`)}
      ${memory}
    </div>
    ${load}
    <div class="dash-cores">${cores}</div>
    <h3>Busy processes</h3>
    ${procs}
  </section>`;
}

function gpuCardsHtml(data) {
  const gpus = data.gpus || { available: false };
  if (!gpus.available || !gpus.gpus?.length) {
    const reason = gpus.reason || "No NVIDIA GPUs detected on this machine.";
    return `<section class="dash-card"><h2>GPU</h2>
      <p class="dash-empty">${esc(reason)}</p>
    </section>`;
  }
  return gpus.gpus.map((gpu) => {
    const memPct = gpu.mem_total_mib ? (100 * gpu.mem_used_mib) / gpu.mem_total_mib : 0;
    const procs = processTableHtml(
      ["User", "PID", "Process", "VRAM"],
      gpu.processes.map((p) => [
        { html: esc(p.user || "-") },
        { html: String(p.pid) },
        { html: esc(p.name) },
        { html: `${p.mem_mib} MiB`, num: true },
      ]),
    ) || '<p class="dash-empty">Idle — no compute processes on this GPU.</p>';
    const badge = gpu.processes.length
      ? '<span class="dash-chip ok">Busy</span>'
      : '<span class="dash-chip">Idle</span>';
    return `<section class="dash-card">
      <h2>GPU ${gpu.index} ${badge}<span class="dash-dim">${Math.round(gpu.temp)}°C</span></h2>
      <p class="dash-meta">${esc(gpu.name)}</p>
      <div class="dash-gauges">
        ${ringSvg(gpu.util, "Utilization", "")}
        ${ringSvg(memPct, "VRAM", `${gpu.mem_used_gib} / ${gpu.mem_total_gib} GiB`)}
      </div>
      <h3>Compute processes</h3>
      ${procs}
    </section>`;
  }).join("");
}

function servicesCardHtml(data) {
  const rows = (data.services || []).map(serviceRowHtml).join("")
    || '<p class="dash-empty">No services reported.</p>';
  return `<section class="dash-card">
    <h2>Services</h2>
    <div class="dash-services">${rows}</div>
  </section>`;
}

function logsCardHtml(activeLog) {
  const tabs = LOG_NAMES.map((name) =>
    `<button class="dash-tab${name === activeLog ? " active" : ""}" type="button" data-log="${name}">${name}</button>`).join("");
  return `<section class="dash-card dash-card-logs">
    <h2>Logs
      <span class="dash-tabs">${tabs}</span>
      <span class="dash-updated" data-ref="log-updated"></span>
    </h2>
    <pre class="dash-logview" data-ref="logview">Loading…</pre>
  </section>`;
}

export function createDashboardController({
  state,
  centerTabs,
  centerTabPanels,
  activateCenterTab,
} = {}) {
  let tab = null;
  let root = null; // current mount: the center-tab panel or the standalone body
  let statusTimer = null;
  let logTimer = null;
  let storedLog = localStorage.getItem(LOG_STORAGE_KEY);
  let logName = LOG_NAMES.includes(storedLog) ? storedLog : "api-server";

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------

  async function refreshStatus() {
    const stamp = root?.querySelector('[data-ref="updated"]');
    try {
      const res = await fetch("/api/system/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const cards = root?.querySelector('[data-ref="cards"]');
      if (cards) {
        cards.innerHTML = servicesCardHtml(data) + cpuCardHtml(data) + gpuCardsHtml(data);
      }
      if (stamp) stamp.textContent = "Updated " + new Date().toLocaleTimeString();
    } catch {
      if (stamp) stamp.textContent = "Refresh failed — is the backend running?";
    }
  }

  async function refreshLog() {
    const view = root?.querySelector('[data-ref="logview"]');
    const stamp = root?.querySelector('[data-ref="log-updated"]');
    if (!view) return;
    try {
      const res = await fetch(`/api/system/logs?name=${encodeURIComponent(logName)}&tail=300`);
      const data = await res.json();
      // Stay pinned to the bottom unless the user scrolled up into history.
      const stick = view.scrollTop + view.clientHeight >= view.scrollHeight - 24;
      view.textContent = (data.lines || []).join("\n") || (data.exists ? "" : `Log file not found: ${logName}.log`);
      if (stick) view.scrollTop = view.scrollHeight;
      if (stamp) stamp.textContent = data.exists ? `Updated ${new Date().toLocaleTimeString()}` : "";
    } catch {
      if (stamp) stamp.textContent = "Log refresh failed";
    }
  }

  function setLogName(name) {
    if (!LOG_NAMES.includes(name)) return;
    logName = name;
    localStorage.setItem(LOG_STORAGE_KEY, name);
    root?.querySelectorAll("[data-log]").forEach((b) => b.classList.toggle("active", b.dataset.log === name));
    const view = root?.querySelector('[data-ref="logview"]');
    if (view) view.textContent = "Loading…";
    void refreshLog();
  }

  function wireLogs(mount) {
    mount?.addEventListener("click", (event) => {
      const target = event.target.closest("[data-log]");
      if (target) setLogName(target.dataset.log);
    });
  }

  function startPolling() {
    stopPolling();
    statusTimer = window.setInterval(() => { void refreshStatus(); }, STATUS_POLL_MS);
    logTimer = window.setInterval(() => { void refreshLog(); }, LOG_POLL_MS);
    void refreshStatus();
    void refreshLog();
  }

  function stopPolling() {
    if (statusTimer !== null) window.clearInterval(statusTimer);
    if (logTimer !== null) window.clearInterval(logTimer);
    statusTimer = null;
    logTimer = null;
  }

  // ------------------------------------------------------------------
  // Center-tab mode (same pattern as the skill graph)
  // ------------------------------------------------------------------

  function ensureDashboardTab() {
    if (tab) {
      activateCenterTab?.(TAB_ID);
      return tab;
    }

    const button = document.createElement("button");
    button.className = "center-tab";
    button.type = "button";
    button.role = "tab";
    button.dataset.tabId = TAB_ID;
    button.id = `tab-${TAB_ID}`;
    button.setAttribute("aria-selected", "false");
    button.setAttribute("aria-controls", `${TAB_ID}-panel`);
    button.title = "Control Panel";

    const title = document.createElement("span");
    title.className = "center-tab-title";
    title.textContent = "Control Panel";
    button.appendChild(title);

    const close = document.createElement("span");
    close.className = "center-tab-close";
    close.dataset.closeTabId = TAB_ID;
    close.setAttribute("aria-hidden", "true");
    close.textContent = "×";
    button.appendChild(close);

    const panel = document.createElement("div");
    panel.className = "center-tab-panel dash-tab-panel";
    panel.id = `${TAB_ID}-panel`;
    panel.role = "tabpanel";
    panel.dataset.tabId = TAB_ID;
    panel.innerHTML = `
      <div class="dash-header">
        <h1>Control Panel</h1>
        <span class="dash-updated" data-ref="updated">Loading…</span>
        <button class="ghost dash-mini-btn" type="button" data-ref="popout" title="Open the standalone dashboard page (for a second monitor)">Pop out</button>
      </div>
      <div class="dash-content">
        <div class="dash-row" data-ref="cards"><p class="dash-empty">Loading…</p></div>
        ${logsCardHtml(logName)}
      </div>`;

    centerTabs?.appendChild(button);
    centerTabPanels?.appendChild(panel);
    tab = { button, panel };
    root = panel;
    wireLogs(panel);
    panel.querySelector('[data-ref="popout"]')?.addEventListener("click", () => {
      window.open("dashboard.html", "_blank", "width=1440,height=900");
    });
    activateCenterTab?.(TAB_ID);
    return tab;
  }

  function open() {
    ensureDashboardTab();
    startPolling();
  }

  // Called on every center-tab switch (see WorkspaceViewerController).
  function activate(activeTabId) {
    if (!tab) return;
    if (activeTabId === TAB_ID) startPolling();
    else stopPolling();
  }

  function close(closedTabId) {
    if (closedTabId !== TAB_ID || !tab) return false;
    stopPolling();
    tab.button.remove();
    tab.panel.remove();
    tab = null;
    root = null;
    return true;
  }

  // ------------------------------------------------------------------
  // Standalone mode (dashboard.html, for a second monitor)
  // ------------------------------------------------------------------

  function renderStandalone(container) {
    root = container;
    container.classList.add("dash-standalone");
    container.innerHTML = `
      <div class="dash-header">
        <h1>MatCreator · Control Panel</h1>
        <span class="dash-updated" data-ref="updated">Loading…</span>
        <button class="ghost dash-mini-btn" type="button" data-ref="reload">Reload</button>
      </div>
      <div class="dash-content">
        <div class="dash-row" data-ref="cards"><p class="dash-empty">Loading…</p></div>
        ${logsCardHtml(logName)}
      </div>`;
    wireLogs(container);
    container.querySelector('[data-ref="reload"]')?.addEventListener("click", () => {
      window.location.reload();
    });
    startPolling();
  }

  return { open, activate, close, renderStandalone };
}
