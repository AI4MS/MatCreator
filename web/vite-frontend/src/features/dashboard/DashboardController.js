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

// Donut gauge: value in the ring center (one decimal below 10% so idle
// machines do not read as flat 0%), label + sub below the ring so long text
// never collides with the stroke. Ring color follows load: green below 60,
// amber below 85, red beyond.
function ringSvg(pct, label, sub) {
  const p = Math.max(0, Math.min(100, pct || 0));
  const level = p >= 85 ? "bad" : p >= 60 ? "warn" : "ok";
  const num = p >= 10 ? String(Math.round(p)) : p.toFixed(1);
  return `<figure class="dash-gauge">
    <svg class="dash-ring" viewBox="0 0 120 120" role="img" aria-label="${esc(label)} ${num}%">
      <circle class="dash-ring-track" cx="60" cy="60" r="${RING_RADIUS}"></circle>
      <circle class="dash-ring-value ${level}" cx="60" cy="60" r="${RING_RADIUS}"
        stroke-dasharray="${RING_CIRCUMFERENCE.toFixed(1)}"
        stroke-dashoffset="${(RING_CIRCUMFERENCE * (1 - p / 100)).toFixed(1)}"></circle>
      <text class="dash-ring-number" x="60" y="60">${num}<tspan class="dash-ring-unit">%</tspan></text>
    </svg>
    <figcaption class="dash-gauge-label">${esc(label)}${sub ? `<span>${esc(sub)}</span>` : ""}</figcaption>
  </figure>`;
}

// Fixed column widths keep rows on one line regardless of content; long
// names truncate with an ellipsis (hover shows the full text via title).
function processTableHtml(headers, widths, rows) {
  if (!rows.length) return "";
  const cols = widths.map((w) => `<col style="width:${w}%">`).join("");
  return `<table class="dash-table">
    <colgroup>${cols}</colgroup>
    <tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>
    ${rows.map((cells) => `<tr>${cells.map((c) => `<td${c.num ? ' class="num"' : ""}${c.title ? ` title="${esc(c.title)}"` : ""}>${c.html}</td>`).join("")}</tr>`).join("")}
  </table>`;
}

// Per-core bars on a balanced grid (e.g. 32 cores -> 16 x 2) so there is no
// ragged half-row; bar count per row adapts to any core count.
function coresGridHtml(cpu) {
  const n = cpu.cores.length;
  const rows = n >= 64 ? 4 : n > 16 ? 2 : 1;
  const cols = Math.max(1, Math.ceil(n / rows));
  const bars = cpu.cores.map((core) => {
    const height = core.usage > 0 ? Math.max(4, Math.min(100, core.usage)) : 0;
    return `<div class="dash-core" title="${esc(core.name)} ${core.usage}%"><i style="height:${height}%"></i></div>`;
  }).join("");
  return `<div class="dash-cores" style="grid-template-columns:repeat(${cols}, 1fr)">${bars}</div>`;
}

function cpuCardHtml(data) {
  const cpu = data.cpu || { available: false };
  if (!cpu.available) {
    return `<section class="dash-card"><h2>CPU / Memory</h2>
      <p class="dash-empty">${esc(cpu.reason || "CPU statistics are not available on this machine.")}</p>
    </section>`;
  }
  const load = Array.isArray(cpu.load)
    ? `<p class="dash-meta">Load ${cpu.load.join(" · ")} <span class="dash-dim">(1/5/15 min)</span></p>`
    : "";
  const memory = data.memory
    ? ringSvg(data.memory.percent, "Memory", `${data.memory.used_gib} / ${data.memory.total_gib} GiB`)
    : `<p class="dash-empty">Memory statistics are not available on this machine.</p>`;
  const procs = processTableHtml(
    ["User", "PID", "CPU%", "Process"],
    [26, 22, 15, 37],
    (data.top_processes || []).map((p) => [
      { html: esc(p.user), title: p.user },
      { html: String(p.pid) },
      { html: String(p.cpu), num: true },
      { html: esc(p.name), title: p.name },
    ]),
  ) || '<p class="dash-empty">No process above 0.5% CPU right now.</p>';
  return `<section class="dash-card">
    <h2>CPU / Memory</h2>
    <div class="dash-gauges">
      ${ringSvg(cpu.total, "CPU", `${cpu.count} cores`)}
      ${memory}
    </div>
    ${load}
    ${coresGridHtml(cpu)}
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
      [24, 20, 34, 22],
      gpu.processes.map((p) => [
        { html: esc(p.user || "-"), title: p.user },
        { html: String(p.pid) },
        { html: esc(p.name), title: p.name },
        { html: `${p.mem_mib} MiB`, num: true },
      ]),
    ) || '<p class="dash-empty">Idle — no compute processes on this GPU.</p>';
    const badge = gpu.processes.length
      ? '<span class="dash-chip ok">Busy</span>'
      : '<span class="dash-chip">Idle</span>';
    const power = gpu.power_w != null
      ? ` · ${Math.round(gpu.power_w)}W${gpu.power_limit_w != null ? ` / ${Math.round(gpu.power_limit_w)}W` : ""}`
      : "";
    return `<section class="dash-card">
      <h2>GPU ${gpu.index} ${badge}<span class="dash-dim">${Math.round(gpu.temp)}°C${power}</span></h2>
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

// Single-line services strip at the top of the page; each cell keeps the
// status chip and port right-aligned so they line up across services.
function servicesStripHtml(data) {
  const items = (data.services || []).map((service) => {
    const chip = service.running
      ? '<span class="dash-chip ok">Running</span>'
      : '<span class="dash-chip bad">Down</span>';
    return `<div class="dash-service">
      <span class="dash-dot ${service.running ? "ok" : "bad"}"></span>
      <span class="dash-service-name" title="${esc(service.label)}">${esc(service.label)}</span>
      ${chip}
      <span class="dash-port">:${service.port}</span>
    </div>`;
  }).join("") || '<p class="dash-empty">No services reported.</p>';
  return `<section class="dash-card dash-card-services">
    <div class="dash-services-row">${items}</div>
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
  let gitBusy = false;
  let note = null; // transient header note (git ops); suppresses the periodic stamp
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
      const services = root?.querySelector('[data-ref="services"]');
      if (services) {
        services.innerHTML = (data.services || []).map((service) => {
          const chip = service.running
            ? '<span class="dash-chip ok">Running</span>'
            : '<span class="dash-chip bad">Down</span>';
          return `<div class="dash-service">
            <span class="dash-dot ${service.running ? "ok" : "bad"}"></span>
            <span class="dash-service-name" title="${esc(service.label)}">${esc(service.label)}</span>
            ${chip}
            <span class="dash-port">:${service.port}</span>
          </div>`;
        }).join("");
      }
      const cards = root?.querySelector('[data-ref="cards"]');
      if (cards) {
        cards.innerHTML = cpuCardHtml(data) + gpuCardsHtml(data);
      }
      if (stamp && !note) stamp.textContent = "Updated " + new Date().toLocaleTimeString();
    } catch {
      if (stamp && !note) stamp.textContent = "Refresh failed — is the backend running?";
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
  // Git: branch switch + upstream update (fetch/pull/deps resync)
  // ------------------------------------------------------------------

  function setStamp(text) {
    note = text;
    const stamp = root?.querySelector('[data-ref="updated"]');
    if (stamp) stamp.textContent = text;
  }

  function clearNoteLater(ms = 8000) {
    window.setTimeout(() => { note = null; }, ms);
  }

  async function loadGitBranches() {
    const sel = root?.querySelector('[data-ref="git-branch"]');
    if (!sel || gitBusy) return;
    try {
      const d = await fetch("/api/system/git").then((r) => r.json());
      if (!d.ok) {
        sel.innerHTML = '<option>git unavailable</option>';
        sel.disabled = true;
        sel.title = d.error || "";
        return;
      }
      sel.innerHTML = d.branches.map((b) =>
        `<option value="${esc(b)}"${b === d.current ? " selected" : ""}>${esc(b)}</option>`).join("");
      sel.disabled = false;
      sel.title = "Current branch: " + d.current;
    } catch {
      sel.innerHTML = '<option>connection failed</option>';
      sel.disabled = true;
    }
  }

  // After an update/checkout the dev stack restarts; wait for the backend
  // then reload so the page runs the fresh code too. Vite HMR may already
  // have refreshed the page mid-pull — the operation itself completes
  // server-side regardless.
  async function waitForBackendThenReload() {
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      try {
        const res = await fetch("/api/health");
        if (res.ok) {
          setStamp("Backend is back — reloading…");
          window.setTimeout(() => window.location.reload(), 800);
          return;
        }
      } catch {}
      await new Promise((r) => window.setTimeout(r, 1500));
    }
    setStamp("Backend did not come back within 90s — check the dev-stack panel.");
    gitBusy = false;
    root?.querySelectorAll('[data-ref="popout"], [data-ref="reload"]').forEach((el) => { el.disabled = false; });
  }

  async function gitCheckout(branch) {
    if (!branch || gitBusy) return;
    gitBusy = true;
    setStamp("Switching to " + branch + " — resyncing python/node deps…");
    root?.querySelectorAll('[data-ref="git-update"], [data-ref="git-branch"], [data-ref="popout"], [data-ref="reload"]')
      .forEach((el) => { el.disabled = true; });
    try {
      const d = await fetch("/api/system/git/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch }),
      }).then((r) => r.json());
      if (d.ok && d.restart) {
        await waitForBackendThenReload(); // reloads the page
        return;
      }
      setStamp(d.ok
        ? "Switched to " + branch + " — deps resynced"
        : "Checkout failed: " + (d.output || "").slice(-160));
    } catch (e) {
      setStamp("Network error: " + e);
    }
    gitBusy = false;
    root?.querySelectorAll('[data-ref="popout"], [data-ref="reload"]').forEach((el) => { el.disabled = false; });
    await loadGitBranches();
    clearNoteLater();
    void refreshStatus();
  }

  async function gitUpdate() {
    if (gitBusy) return;
    gitBusy = true;
    const btn = root?.querySelector('[data-ref="git-update"]');
    const sel = root?.querySelector('[data-ref="git-branch"]');
    const oldLabel = btn ? btn.textContent : "";
    if (btn) btn.disabled = true;
    if (sel) sel.disabled = true;
    setStamp("Updating branch — fetch / pull / uv pip install -e . / npm install, this can take a few minutes…");
    try {
      const d = await fetch("/api/system/git/update", { method: "POST" }).then((r) => r.json());
      if (d.ok && d.restart) {
        await waitForBackendThenReload(); // reloads the page
        return;
      }
      if (d.ok) {
        setStamp("Branch updated (" + (d.source || d.current || "") + ") — deps resynced");
      } else {
        setStamp("Update failed (" + (d.stage || "") + "): " + (d.output || "").slice(-160));
      }
    } catch (e) {
      setStamp("Network error: " + e);
    }
    gitBusy = false;
    if (btn) { btn.disabled = false; btn.textContent = oldLabel; }
    if (sel) sel.disabled = false;
    await loadGitBranches();
    clearNoteLater();
    void refreshStatus();
  }

  function wireGitControls(mount) {
    mount?.querySelector('[data-ref="git-branch"]')?.addEventListener("change", (e) => {
      void gitCheckout(e.target.value);
    });
    mount?.querySelector('[data-ref="git-update"]')?.addEventListener("click", () => {
      void gitUpdate();
    });
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
        <span class="dash-git">
          <select class="dash-git-branch" data-ref="git-branch" title="Switch branch (git checkout)" disabled><option>…</option></select>
          <button class="ghost dash-mini-btn" type="button" data-ref="git-update" title="git fetch upstream + pull + reinstall python/node deps">Update branch</button>
        </span>
        <span class="dash-updated" data-ref="updated">Loading…</span>
        <button class="ghost dash-mini-btn" type="button" data-ref="popout" title="Open the standalone dashboard page (for a second monitor)">Pop out</button>
      </div>
      <div class="dash-content">
        <section class="dash-card dash-card-services">
          <div class="dash-services-row" data-ref="services"><p class="dash-empty">Loading…</p></div>
        </section>
        <div class="dash-row" data-ref="cards"><p class="dash-empty">Loading…</p></div>
        ${logsCardHtml(logName)}
      </div>`;

    centerTabs?.appendChild(button);
    centerTabPanels?.appendChild(panel);
    tab = { button, panel };
    root = panel;
    wireLogs(panel);
    wireGitControls(panel);
    loadGitBranches();
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
        <span class="dash-git">
          <select class="dash-git-branch" data-ref="git-branch" title="Switch branch (git checkout)" disabled><option>…</option></select>
          <button class="ghost dash-mini-btn" type="button" data-ref="git-update" title="git fetch upstream + pull + reinstall python/node deps">Update branch</button>
        </span>
        <span class="dash-updated" data-ref="updated">Loading…</span>
        <button class="ghost dash-mini-btn" type="button" data-ref="reload">Reload</button>
      </div>
      <div class="dash-content">
        <section class="dash-card dash-card-services">
          <div class="dash-services-row" data-ref="services"><p class="dash-empty">Loading…</p></div>
        </section>
        <div class="dash-row" data-ref="cards"><p class="dash-empty">Loading…</p></div>
        ${logsCardHtml(logName)}
      </div>`;
    wireLogs(container);
    wireGitControls(container);
    container.querySelector('[data-ref="reload"]')?.addEventListener("click", () => {
      window.location.reload();
    });
    loadGitBranches();
    startPolling();
  }

  return { open, activate, close, renderStandalone };
}
