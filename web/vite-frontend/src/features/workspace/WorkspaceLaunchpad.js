const icons = {
  chat: '<path d="M5 4h14v12H9l-4 4z"/><path d="M9 8h6M9 12h4"/>',
  graph: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="9" r="3"/><circle cx="9" cy="19" r="3"/><path d="m9 7 6 1M7 9l1 7m3 1 5-6"/>',
  jobs: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m7 10 3 2-3 2m6 0h4"/>',
  settings: '<path d="M5 4v16M12 4v16M19 4v16M2 9h6m1 6h6m1-8h6"/>',
  xrd: '<path d="M3 3v18h19M5 17h3l2-8 2 8h2l2-13 2 13h3"/>',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
}
export function installWorkspaceLaunchpad({ controller, newTabButton, newSession, openGraph, openSettings, openJobs }) {
  let sequence = 0;
  let xrdSequence = 0;
  function openXrd() {
    const number = ++xrdSequence;
    controller.openToolTab(`xrd-${number}`, `XRD ${number}`, (panel) => {
      let dispose; let closed = false;
      panel.innerHTML = '<div class="viewer-loading-message">正在载入 XRD 工作台…</div>';
      import('../xrd/mount.jsx').then(({ mountXrd }) => {
        if (!closed) { panel.replaceChildren(); dispose = mountXrd(panel, { createNew: true, openChat: () => controller.activate('chat') }); }
      }).catch(error => { panel.textContent = `XRD 加载失败：${error.message}`; });
      return () => { closed = true; dispose?.(); };
    });
  }
  function openNewTab() {
    controller.openToolTab(`start-${++sequence}`, "新标签页", (panel) => {
      panel.innerHTML = `<div class="workspace-launchpad">
        <div class="launchpad-eyebrow">MATCREATOR / WORKSPACE</div>
        <h1>从这里开始。</h1><p class="launchpad-intro">一个工作台，连接对话、知识与材料分析。</p>
        <div class="launchpad-section-label">工作空间 <span>QUICK ACCESS</span></div>
        <div class="launchpad-grid"></div>
        <div class="launchpad-section-label">材料分析 <span>ANALYSIS</span></div>
        <button type="button" class="launchpad-xrd" data-launch-action="xrd">
          <span class="launchpad-icon">${icon("xrd")}</span>
          <span class="launchpad-xrd-copy"><strong>XRD 工作台</strong><span>衍射谱 · 物相检索 · 精修分析</span></span>
          <span class="launchpad-status">打开</span><span aria-hidden="true">↗</span>
        </button>
        <p class="launchpad-footnote">打开工具标签页，随时切回当前对话。</p>
      </div>`;
      const entries = [
        ["chat", "新建对话", "开始一个新的研究任务", newSession],
        ["graph", "技能图谱", "浏览知识关联与可用技能", openGraph],
        ["jobs", "远程任务", "查看任务状态与执行进度", openJobs],
        ["settings", "设置", "模型、工具与工作台偏好", openSettings],
      ];
      for (const [name, title, description, action] of entries) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "launchpad-card";
        button.dataset.launchAction = name;
        button.innerHTML = `<span class="launchpad-icon">${icon(name)}</span><strong>${title}</strong><span class="launchpad-card-description">${description}</span><span class="launchpad-arrow" aria-hidden="true">↗</span>`;
        button.addEventListener("click", action);
        panel.querySelector(".launchpad-grid").appendChild(button);
      }
      panel.querySelector('[data-launch-action="xrd"]').addEventListener("click", openXrd);
    });
  }
  newTabButton?.addEventListener("click", openNewTab);
  return { openNewTab, openXrd };
}
