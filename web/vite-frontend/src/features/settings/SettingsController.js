import { createDialogController } from "../../shared/ui/dialog.js";
import { buildSettingsSavePlan } from "./settingsChanges.js";

export function createSettingsController({ state, applyLogin, getFontScale, applyFontScale }) {

  const settingsModal = document.getElementById("settings-modal");
  const settingsBtn = document.getElementById("settings-btn");
  const settingsClose = document.getElementById("settings-close");
  const settingsCancel = document.getElementById("settings-cancel");
  const settingsSave = document.getElementById("settings-save");
  const settingsStatus = document.getElementById("settings-status");
  const settingsUsername = document.getElementById("settings-username");
  const settingsUuid = document.getElementById("settings-uuid");
  const skillsChecklist = document.getElementById("skills-checklist");
  const settingsRestartBtn = document.getElementById("settings-restart-btn");
  const settingsEnvPairs = document.getElementById("settings-env-pairs");
  const settingsEnvAdd = document.getElementById("settings-env-add");
  const settingsLlmExecutorDefault = document.getElementById("settings-llm-executor-default");
  const settingsLlmCards = document.getElementById("settings-llm-cards");
  const settingsLlmCardAdd = document.getElementById("settings-llm-card-add");
  const fontScaleOptions = document.getElementById("settings-font-scale-options");
  const CUSTOM_ENV_CONFIG_KEY = "CUSTOM_ENV";
  const REQUEST_TIMEOUT_MS = 15_000;
  let loadedSettings = null;
  let loadController = null;
  let saveController = null;
  let statusTimer = null;
  const settingsDialog = createDialogController({
    element: settingsModal,
    labelledBy: "settings-dialog-title",
    initialFocus: settingsClose,
    onClose: cleanupSettingsOperations,
  });

  // Env config input refs
  const envInputs = {
    LLM_MODEL:              () => document.getElementById("settings-llm-model"),
    LLM_API_KEY:            () => document.getElementById("settings-llm-apikey"),
    LLM_BASE_URL:           () => document.getElementById("settings-llm-baseurl"),
    EMBEDDING_MODEL:        () => document.getElementById("settings-llm-embed"),
    GRAPH_AGENT_MODEL:      () => document.getElementById("settings-llm-graph-model"),
    REVIEW_AGENT_MODEL:     () => document.getElementById("settings-llm-review-model"),
    MATCREATOR_EXEC_TIMEOUT_SECONDS: () => document.getElementById("settings-execution-timeout"),
    MATCREATOR_MEMORIZATION_FREQUENCY: () => document.getElementById("settings-memorization-frequency"),
    MATCREATOR_REVIEW_FREQUENCY: () => document.getElementById("settings-review-frequency"),
    MAT_BENCH_SERVER_URL:   () => document.getElementById("settings-benchmark-server-url"),
    MAT_BENCH_TOKEN:        () => document.getElementById("settings-benchmark-token"),
    MAT_BENCH_QUESTION_BANK_ROOT: () => document.getElementById("settings-benchmark-question-bank-root"),
    BOHRIUM_USERNAME:       () => document.getElementById("settings-bohrium-username"),
    BOHRIUM_PASSWORD:       () => document.getElementById("settings-bohrium-password"),
    BOHRIUM_ACCESS_KEY:     () => document.getElementById("settings-bohrium-access-key"),
    BOHRIUM_API_URL:        () => document.getElementById("settings-bohrium-api-url"),
    BOHRIUM_PROJECT_ID:     () => document.getElementById("settings-bohrium-project-id"),
    BOHRIUM_VASP_IMAGE:     () => document.getElementById("settings-bohrium-vasp-image"),
    BOHRIUM_VASP_MACHINE:   () => document.getElementById("settings-bohrium-vasp-machine"),
    BOHRIUM_DEEPMD_IMAGE:   () => document.getElementById("settings-bohrium-deepmd-image"),
    BOHRIUM_DEEPMD_MACHINE: () => document.getElementById("settings-bohrium-deepmd-machine"),
    DEEPMD_MODEL_PATH:      () => document.getElementById("settings-deepmd-model-path"),
  };

  function settingsQueryString() {
    return state.deploymentMode === "server" && state.userId
      ? `?user_id=${encodeURIComponent(state.userId)}`
      : "";
  }

  function settingsApiUrl(path) {
    return `${path}${settingsQueryString()}`;
  }

  function createEnvPairRow(key = "", value = "") {
    const row = document.createElement("div");
    row.className = "settings-env-pair-row";

    const keyInput = document.createElement("input");
    keyInput.className = "text-input settings-env-input settings-env-key";
    keyInput.placeholder = "KEY";
    keyInput.value = key;
    keyInput.autocomplete = "off";

    const valueInput = document.createElement("input");
    valueInput.className = "text-input settings-env-input settings-env-value";
    valueInput.placeholder = "value";
    valueInput.value = value;
    valueInput.autocomplete = "new-password";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost settings-env-remove";
    remove.title = "Remove variable";
    remove.textContent = "×";
    remove.addEventListener("click", () => row.remove());

    row.append(keyInput, valueInput, remove);
    return row;
  }

  function renderEnvPairs(envCfg = {}) {
    if (!settingsEnvPairs) return;
    settingsEnvPairs.innerHTML = "";
    const entries = Object.entries(envCfg || {}).sort(([a], [b]) => a.localeCompare(b));
    entries.forEach(([key, value]) => settingsEnvPairs.appendChild(createEnvPairRow(key, value)));
  }

  function collectEnvPairs() {
    const values = {};
    settingsEnvPairs?.querySelectorAll(".settings-env-pair-row")?.forEach((row) => {
      const key = row.querySelector(".settings-env-key")?.value.trim();
      const value = row.querySelector(".settings-env-value")?.value || "";
      if (key && value) values[key] = value;
    });
    return values;
  }

  function formatJsonConfig(value) {
    if (!value || (typeof value === "object" && !Object.keys(value).length)) return "";
    return JSON.stringify(value, null, 2);
  }

  function parseJsonConfig(text, label) {
    const trimmed = (text || "").trim();
    if (!trimmed) return undefined;
    try {
      const parsed = JSON.parse(trimmed);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error(`${label} must be a JSON object.`);
      }
      return parsed;
    } catch (err) {
      throw new Error(`${label} is not valid JSON: ${err.message}`);
    }
  }

  function csvFieldToList(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function listFieldToCsv(value) {
    return Array.isArray(value) ? value.join(", ") : String(value || "");
  }

  function executorCardKnownFields() {
    return new Set([
      "model",
      "description",
      "skills",
      "tags",
      "routing_keywords",
      "cost_tier",
      "latency_tier",
      "priority",
    ]);
  }

  function createExecutorCardEditor(name = "", data = {}) {
    const card = document.createElement("section");
    card.className = "settings-llm-card-editor";
    const customData = { ...(data || {}) };
    executorCardKnownFields().forEach((key) => delete customData[key]);

    card.innerHTML = `
      <div class="settings-llm-card-title">Executor Card</div>
      <label class="settings-label">name</label>
      <div class="settings-llm-card-header">
        <input data-card-field="name" class="text-input settings-env-input settings-llm-card-name" placeholder="card name" autocomplete="off" />
        <button type="button" class="ghost settings-llm-card-remove" title="Remove card">×</button>
      </div>
      <label class="settings-label">Model</label>
      <input data-card-field="model" class="text-input settings-env-input" placeholder="openai/qwen3-plus" />
      <label class="settings-label">Description</label>
      <textarea data-card-field="description" class="text-input settings-env-input settings-llm-card-description" spellcheck="false" placeholder="When this card should be used"></textarea>
      <div class="settings-llm-card-grid">
        <label class="settings-llm-field">Skills
          <input data-card-field="skills" class="text-input settings-env-input" placeholder="filesystem, python" />
        </label>
        <label class="settings-llm-field">Tags
          <input data-card-field="tags" class="text-input settings-env-input" placeholder="vision, cheap" />
        </label>
        <label class="settings-llm-field">Routing Keywords
          <input data-card-field="routing_keywords" class="text-input settings-env-input" placeholder="debug, analyze" />
        </label>
        <label class="settings-llm-field">Cost Tier
          <input data-card-field="cost_tier" class="text-input settings-env-input" placeholder="low / medium / high" />
        </label>
        <label class="settings-llm-field">Latency Tier
          <input data-card-field="latency_tier" class="text-input settings-env-input" placeholder="low / medium / high" />
        </label>
        <label class="settings-llm-field">Priority
          <input data-card-field="priority" type="number" class="text-input settings-env-input" placeholder="0" />
        </label>
      </div>
      <label class="settings-label">Custom Fields JSON</label>
      <textarea data-card-field="custom" class="text-input settings-env-input settings-json-textarea settings-llm-card-custom" spellcheck="false" placeholder='{"api_key":"...","base_url":"https://..."}'></textarea>
    `;

    card.querySelector("[data-card-field='name']").value = name;
    card.querySelector("[data-card-field='model']").value = data?.model || "";
    card.querySelector("[data-card-field='description']").value = data?.description || "";
    card.querySelector("[data-card-field='skills']").value = listFieldToCsv(data?.skills);
    card.querySelector("[data-card-field='tags']").value = listFieldToCsv(data?.tags);
    card.querySelector("[data-card-field='routing_keywords']").value = listFieldToCsv(data?.routing_keywords || data?.keywords);
    card.querySelector("[data-card-field='cost_tier']").value = data?.cost_tier || "";
    card.querySelector("[data-card-field='latency_tier']").value = data?.latency_tier || "";
    card.querySelector("[data-card-field='priority']").value = data?.priority ?? "";
    card.querySelector("[data-card-field='custom']").value = formatJsonConfig(customData);
    card.querySelector(".settings-llm-card-remove")?.addEventListener("click", () => card.remove());
    return card;
  }

  function renderExecutorCards(executorCards = {}) {
    if (!settingsLlmCards) return;
    settingsLlmCards.innerHTML = "";
    if (settingsLlmExecutorDefault) settingsLlmExecutorDefault.value = executorCards?.default || "";
    const cards = executorCards?.cards && typeof executorCards.cards === "object" ? executorCards.cards : {};
    Object.entries(cards)
      .sort(([left], [right]) => left.localeCompare(right))
      .forEach(([name, data]) => {
        settingsLlmCards.appendChild(createExecutorCardEditor(name, data || {}));
      });
  }

  function collectExecutorCards() {
    const defaultName = settingsLlmExecutorDefault?.value.trim() || "";
    const cards = {};
    settingsLlmCards?.querySelectorAll(".settings-llm-card-editor")?.forEach((card) => {
      const name = card.querySelector("[data-card-field='name']")?.value.trim();
      if (!name) return;
      const data = {};
      const setText = (field) => {
        const value = card.querySelector(`[data-card-field='${field}']`)?.value.trim();
        if (value) data[field] = value;
      };
      setText("model");
      setText("description");
      ["skills", "tags", "routing_keywords"].forEach((field) => {
        const value = csvFieldToList(card.querySelector(`[data-card-field='${field}']`)?.value || "");
        if (value.length) data[field] = value;
      });
      setText("cost_tier");
      setText("latency_tier");
      const priorityRaw = card.querySelector("[data-card-field='priority']")?.value;
      if (priorityRaw !== undefined && priorityRaw !== "") data.priority = Number(priorityRaw);
      const custom = parseJsonConfig(card.querySelector("[data-card-field='custom']")?.value || "", `Custom Fields for ${name}`);
      cards[name] = { ...(custom || {}), ...data };
    });
    return { default: defaultName, cards };
  }

  settingsLlmCardAdd?.addEventListener("click", () => {
    const nextIndex = (settingsLlmCards?.querySelectorAll(".settings-llm-card-editor")?.length || 0) + 1;
    const card = createExecutorCardEditor(`card_${nextIndex}`, {});
    settingsLlmCards?.appendChild(card);
    card.querySelector("[data-card-field='name']")?.focus();
  });

  settingsEnvAdd?.addEventListener("click", () => {
    const row = createEnvPairRow();
    settingsEnvPairs?.appendChild(row);
    row.querySelector(".settings-env-key")?.focus();
  });

  fontScaleOptions?.addEventListener("click", (event) => {
    const option = event.target.closest("[data-font-scale]");
    if (!option) return;
    applyFontScale?.(Number(option.dataset.fontScale));
    updateFontScaleOptions();
  });

  function openSettingsModal() {
    if (!settingsDialog.open()) return;
    settingsUsername.value = state.displayName || "";
    settingsUuid.value = state.userId || "";
    loadSettingsData();
    updateFontScaleOptions();
  }

  function updateFontScaleOptions() {
    const currentScale = getFontScale?.() ?? 100;
    fontScaleOptions?.querySelectorAll("[data-font-scale]").forEach((option) => {
      const selected = Number(option.dataset.fontScale) === currentScale;
      option.classList.toggle("active", selected);
      option.setAttribute("aria-pressed", String(selected));
    });
  }

  function closeSettingsModal() {
    settingsDialog.close();
  }

  function cleanupSettingsOperations() {
    loadController?.abort();
    saveController?.abort();
    loadController = null;
    saveController = null;
    loadedSettings = null;
    clearStatusTimer();
    settingsStatus.textContent = "";
    settingsStatus.classList.remove("is-error", "is-success");
    settingsSave.disabled = false;
    settingsSave.textContent = "Save";
  }

  function clearStatusTimer() {
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = null;
  }

  function setSettingsStatus(message = "", kind = "") {
    clearStatusTimer();
    settingsStatus.textContent = message;
    settingsStatus.classList.toggle("is-error", kind === "error");
    settingsStatus.classList.toggle("is-success", kind === "success");
  }

  function clearSettingsStatusLater(delayMs) {
    clearStatusTimer();
    statusTimer = setTimeout(() => {
      settingsStatus.textContent = "";
      settingsStatus.classList.remove("is-error", "is-success");
      statusTimer = null;
    }, delayMs);
  }

  async function fetchWithTimeout(url, options = {}, signal = null, timeoutMs = REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const abortFromCaller = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener("abort", abortFromCaller, { once: true });
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (err) {
      if (controller.signal.aborted && !signal?.aborted) {
        throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortFromCaller);
    }
  }

  function isCancelled(error, signal) {
    return signal?.aborted || error?.name === "AbortError";
  }

  function waitFor(delayMs, signal) {
    return new Promise((resolve, reject) => {
      const finish = () => {
        signal?.removeEventListener("abort", cancel);
        resolve();
      };
      const cancel = () => {
        clearTimeout(timer);
        reject(new DOMException("Cancelled", "AbortError"));
      };
      const timer = setTimeout(finish, delayMs);
      if (signal?.aborted) cancel();
      else signal?.addEventListener("abort", cancel, { once: true });
    });
  }

  // ---- tree helpers ----------------------------------------------------------

  function _buildSkillTree(skills) {
    const byName = new Map(skills.map((s) => [s.name, { ...s, children: [] }]));
    const roots = [];
    for (const s of skills) {
      const node = byName.get(s.name);
      if (s.parent && byName.has(s.parent)) {
        byName.get(s.parent).children.push(node);
      } else {
        roots.push(node);
      }
    }
    const cmp = (a, b) => {
      const ak = a.children.length > 0 ? 0 : 1;
      const bk = b.children.length > 0 ? 0 : 1;
      return ak !== bk ? ak - bk : a.name.localeCompare(b.name);
    };
    roots.sort(cmp);
    roots.forEach((r) => r.children.sort(cmp));
    return roots;
  }

  function _syncParent(parentCb, childWrap) {
    const childCbs = Array.from(
      childWrap.querySelectorAll(":scope > .st-item > .st-row > .skill-checkbox")
    ).filter((c) => !c.disabled);
    if (!childCbs.length) return;
    const checkedCount = childCbs.filter((c) => c.checked).length;
    if (checkedCount === childCbs.length) {
      parentCb.indeterminate = false;
      parentCb.checked = true;
    } else if (checkedCount === 0) {
      parentCb.indeterminate = false;
      parentCb.checked = false;
    } else {
      parentCb.indeterminate = true;
    }
  }

  function _renderSkillNode(node, extraSkills, depth) {
    const hasChildren = node.children.length > 0;

    const item = document.createElement("div");
    item.className = "st-item";

    const row = document.createElement("div");
    row.className = "st-row";
    if (depth > 0) row.style.paddingLeft = `${depth * 18}px`;

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "st-toggle";
    toggle.innerHTML = hasChildren ? "&#9654;" : "";
    toggle.disabled = !hasChildren;
    if (!hasChildren) toggle.style.visibility = "hidden";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "skill-checkbox";
    cb.dataset.name = node.name;
    cb.checked = node.planning_enabled || extraSkills.has(node.name);

    const enabledCb = document.createElement("input");
    enabledCb.type = "checkbox";
    enabledCb.className = "skill-enabled-checkbox";
    enabledCb.dataset.name = node.name;
    enabledCb.checked = node.enabled !== false;
    enabledCb.title = "Enable skill for graph search";

    const nameEl = document.createElement("span");
    nameEl.className = "st-name";
    nameEl.textContent = node.name;

    if (node.source === "builtin") {
      const badge = document.createElement("span");
      badge.className = "skill-badge-custom";
      badge.textContent = "Builtin";
      nameEl.appendChild(badge);
    }

    if (node.source === "official") {
      const badge = document.createElement("span");
      badge.className = "skill-badge-custom";
      badge.textContent = "Official";
      nameEl.appendChild(badge);
    }

    if (node.is_custom) {
      const badge = document.createElement("span");
      badge.className = "skill-badge-custom";
      badge.textContent = "Custom";
      nameEl.appendChild(badge);
    }

    const descEl = document.createElement("span");
    descEl.className = "st-desc";
    descEl.textContent = node.description;

    row.append(toggle, enabledCb, cb, nameEl, descEl);

    if (node.is_custom) {
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "skill-delete-btn";
      delBtn.title = "Delete custom skill";
      delBtn.textContent = "✕";
      delBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete custom skill "${node.name}"?`)) return;
        await deleteCustomSkill(node.name);
      });
      row.appendChild(delBtn);
    }

    item.appendChild(row);

    if (hasChildren) {
      const childWrap = document.createElement("div");
      childWrap.className = "st-children st-collapsed";

      for (const child of node.children) {
        childWrap.appendChild(_renderSkillNode(child, extraSkills, depth + 1));
      }
      item.appendChild(childWrap);

      // Expand / collapse
      toggle.addEventListener("click", () => {
        const collapsed = childWrap.classList.toggle("st-collapsed");
        toggle.innerHTML = collapsed ? "&#9654;" : "&#9660;";
      });

      // Parent → children propagation
      cb.addEventListener("change", () => {
        childWrap
          .querySelectorAll(".skill-checkbox:not(:disabled)")
          .forEach((c) => {
            c.checked = cb.checked;
            c.indeterminate = false;
          });
      });

      // Children → parent tri-state
      childWrap.addEventListener("change", () => _syncParent(cb, childWrap));

      // Initial tri-state
      queueMicrotask(() => _syncParent(cb, childWrap));
    }

    return item;
  }

  // ---- custom skill upload / delete ------------------------------------------

  async function uploadCustomSkill() {
    const nameInput = document.getElementById("custom-skill-name");
    const mdInput = document.getElementById("custom-skill-md");
    const refsInput = document.getElementById("custom-skill-refs");
    const scriptsInput = document.getElementById("custom-skill-scripts");
    const errorEl = document.getElementById("custom-skill-error");
    const uploadBtn = document.getElementById("custom-skill-upload-btn");

    if (!nameInput || !mdInput) return;
    errorEl.textContent = "";

    const name = nameInput.value.trim();
    if (!name) { errorEl.textContent = "Skill name is required."; return; }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
      errorEl.textContent = "Name must be lowercase alphanumeric with hyphens/underscores.";
      return;
    }
    if (!mdInput.files.length) { errorEl.textContent = "SKILL.md file is required."; return; }

    const formData = new FormData();
    formData.append("name", name);
    formData.append("skill_md", mdInput.files[0]);
    for (const ref of Array.from(refsInput?.files || [])) {
      formData.append("references", ref);
    }
    for (const script of Array.from(scriptsInput?.files || [])) {
      formData.append("scripts", script);
    }

    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";
    try {
      const resp = await fetch("/api/skills/custom", { method: "POST", body: formData });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        errorEl.textContent = body.detail || `Upload failed (${resp.status})`;
        return;
      }
      nameInput.value = "";
      mdInput.value = "";
      if (refsInput) refsInput.value = "";
      if (scriptsInput) scriptsInput.value = "";
      settingsStatus.textContent = `Custom skill "${name}" uploaded ✓`;
      setTimeout(() => { settingsStatus.textContent = ""; }, 3000);
      await loadSettingsData();
    } catch (err) {
      errorEl.textContent = `Upload failed: ${err.message}`;
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload Skill";
    }
  }

  async function deleteCustomSkill(skillName) {
    try {
      const resp = await fetch(`/api/skills/custom/${encodeURIComponent(skillName)}`, { method: "DELETE" });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        settingsStatus.textContent = body.detail || `Delete failed (${resp.status})`;
        return;
      }
      settingsStatus.textContent = `Custom skill "${skillName}" deleted ✓`;
      setTimeout(() => { settingsStatus.textContent = ""; }, 3000);
      await loadSettingsData();
    } catch (err) {
      settingsStatus.textContent = `Delete failed: ${err.message}`;
    }
  }

  // ---- load / save -----------------------------------------------------------

  async function loadSettingsData() {
    loadController?.abort();
    const controller = new AbortController();
    loadController = controller;
    loadedSettings = null;
    settingsSave.disabled = true;
    settingsSave.textContent = "Loading…";
    setSettingsStatus("Loading settings…");
    skillsChecklist.innerHTML =
      '<p class="settings-hint" style="opacity:0.6">Loading…</p>';
    try {
      const [skillsRes, settingsRes, envRes] = await Promise.all([
        fetchWithTimeout(settingsApiUrl("/api/skills"), {}, controller.signal),
        fetchWithTimeout(settingsApiUrl("/api/settings"), {}, controller.signal),
        fetchWithTimeout(settingsApiUrl("/api/env-config"), {}, controller.signal),
      ]);
      for (const response of [skillsRes, settingsRes, envRes]) {
        if (!response.ok) throw new Error(await response.text() || `Request failed (${response.status})`);
      }
      const skills = await skillsRes.json();
      const cfg = await settingsRes.json();
      const envCfg = await envRes.json();
      const llmCfg = cfg.llm || {};
      const extraSkills = new Set((cfg.planning || {}).extra_skills || []);

      // Populate default workdir from config
      state.defaultWorkdir = (cfg.workspace || {}).default_workdir || "";
      const wdInput = document.getElementById("settings-default-workdir");
      if (wdInput) wdInput.value = state.defaultWorkdir;

      const roots = _buildSkillTree(skills);
      skillsChecklist.innerHTML = "";

      const header = document.createElement("div");
      header.className = "st-header";
      header.innerHTML = '<span></span><span class="st-col-label" title="Skill is visible to graph search">Enabled</span><span class="st-col-label" title="Full SKILL.md available to planning agent">Planning</span><span></span>';
      skillsChecklist.appendChild(header);

      for (const node of roots) {
        skillsChecklist.appendChild(_renderSkillNode(node, extraSkills, 0));
      }

      // Custom skill upload section
      const uploadSection = document.createElement("div");
      uploadSection.className = "skill-upload-section";
      uploadSection.innerHTML = `
        <details class="skill-upload-details">
          <summary class="skill-upload-summary">+ Add Custom Skill</summary>
          <div class="skill-upload-form">
            <label class="settings-label">Skill Name <span style="font-weight:400;text-transform:none;letter-spacing:0">(lowercase, hyphens/underscores only)</span></label>
            <input id="custom-skill-name" class="text-input settings-env-input" placeholder="e.g. my-custom-skill" autocomplete="off" />
            <label class="settings-label" style="margin-top:8px">SKILL.md file <span style="color:#f87171">*</span></label>
            <input id="custom-skill-md" type="file" accept=".md,text/markdown,text/plain" class="skill-file-input" />
            <label class="settings-label" style="margin-top:8px">Reference files <span style="font-weight:400;text-transform:none;letter-spacing:0">(optional, multiple)</span></label>
            <input id="custom-skill-refs" type="file" multiple class="skill-file-input" />
            <label class="settings-label" style="margin-top:8px">Script files <span style="font-weight:400;text-transform:none;letter-spacing:0">(optional, .py/.sh/.js)</span></label>
            <input id="custom-skill-scripts" type="file" multiple accept=".py,.sh,.bash,.js" class="skill-file-input" />
            <p id="custom-skill-error" class="skill-upload-error"></p>
            <button id="custom-skill-upload-btn" type="button" class="ghost" style="margin-top:8px;width:100%;justify-content:center">Upload Skill</button>
          </div>
        </details>
      `;
      skillsChecklist.appendChild(uploadSection);

      document.getElementById("custom-skill-upload-btn")?.addEventListener("click", uploadCustomSkill);

      // Populate env config inputs
      for (const [key, getEl] of Object.entries(envInputs)) {
        const el = getEl();
        if (el) el.value = envCfg[key] ?? "";
      }
      renderExecutorCards(llmCfg.executor_cards || {});
      renderEnvPairs(envCfg[CUSTOM_ENV_CONFIG_KEY] || {});
      loadedSettings = collectSettingsDraft();
      setSettingsStatus("Settings loaded", "success");
      clearSettingsStatusLater(1000);
    } catch (err) {
      if (isCancelled(err, controller.signal)) return;
      const error = document.createElement("p");
      error.className = "settings-hint settings-error";
      error.textContent = `Failed to load: ${err.message}`;
      skillsChecklist.replaceChildren(error);
      setSettingsStatus(`Could not load settings: ${err.message}`, "error");
    } finally {
      if (loadController === controller) loadController = null;
      if (!controller.signal.aborted) updateSaveButton();
    }
  }

  function collectSettingsDraft() {
    const username = settingsUsername.value.trim();
    const defaultWorkdir = document.getElementById("settings-default-workdir")?.value?.trim() || "";
    const extraSkills = Array.from(
      skillsChecklist.querySelectorAll(".skill-checkbox:not(:disabled)"),
    )
      .filter((cb) => cb.checked && !cb.indeterminate)
      .map((cb) => cb.dataset.name)
      .sort();
    const disabledSkills = Array.from(
      skillsChecklist.querySelectorAll(".skill-enabled-checkbox"),
    )
      .filter((cb) => !cb.checked)
      .map((cb) => cb.dataset.name)
      .sort();

    const envValues = {};
    const sensitiveKeys = new Set([
      "LLM_API_KEY", "MAT_BENCH_TOKEN", "BOHRIUM_PASSWORD", "BOHRIUM_ACCESS_KEY",
    ]);
    for (const [key, getEl] of Object.entries(envInputs)) {
      const el = getEl();
      if (!el) continue;
      const value = el.value;
      if (sensitiveKeys.has(key) && (!value || value === "***")) continue;
      envValues[key] = value;
    }
    envValues[CUSTOM_ENV_CONFIG_KEY] = collectEnvPairs();

    return {
      username,
      defaultWorkdir,
      extraSkills,
      disabledSkills,
      envValues,
      llm: { executor_cards: collectExecutorCards() },
    };
  }

  function updateSaveButton() {
    if (saveController) return;
    if (!loadedSettings) {
      settingsSave.disabled = true;
      settingsSave.textContent = "Save";
      return;
    }
    try {
      const plan = buildSettingsSavePlan(loadedSettings, collectSettingsDraft());
      settingsSave.disabled = !plan.changed;
      settingsSave.textContent = plan.changed ? "Save changes" : "Saved";
    } catch (_) {
      settingsSave.disabled = false;
      settingsSave.textContent = "Save changes";
    }
  }

  async function saveSettings() {
    if (!loadedSettings || saveController) return;
    let nextSettings;
    let plan;
    try {
      nextSettings = collectSettingsDraft();
      plan = buildSettingsSavePlan(loadedSettings, nextSettings);
    } catch (err) {
      setSettingsStatus(`Check the highlighted values: ${err.message}`, "error");
      return;
    }
    if (!plan.changed) {
      setSettingsStatus("Everything is already saved", "success");
      clearSettingsStatusLater(1500);
      updateSaveButton();
      return;
    }

    const controller = new AbortController();
    saveController = controller;
    try {
      settingsSave.disabled = true;
      settingsSave.textContent = "Saving…";
      setSettingsStatus("Saving…");
      if (plan.settingsBody) {
        const response = await fetchWithTimeout(settingsApiUrl("/api/settings"), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(plan.settingsBody),
        }, controller.signal);
        if (!response.ok) throw new Error(await response.text());
      }
      if (plan.envValues) {
        const response = await fetchWithTimeout(settingsApiUrl("/api/env-config"), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: plan.envValues }),
        }, controller.signal);
        if (!response.ok) throw new Error(await response.text());
      }

      const usernameChanged = nextSettings.username !== loadedSettings.username;
      loadedSettings = nextSettings;
      state.defaultWorkdir = nextSettings.defaultWorkdir;
      if (plan.restartBackend) {
        settingsSave.textContent = "Restarting…";
        setSettingsStatus("Saved. Restarting the agent backend…", "success");
        await restartBackend(controller.signal);
      } else {
        setSettingsStatus("Saved", "success");
        clearSettingsStatusLater(2000);
      }
      if (state.deploymentMode === "server" && usernameChanged && nextSettings.username) {
        saveController = null;
        settingsDialog.close();
        await applyLogin(nextSettings.username, null);
      }
    } catch (err) {
      if (!isCancelled(err, controller.signal)) {
        setSettingsStatus(`Could not save: ${err.message}`, "error");
      }
    } finally {
      if (saveController === controller) saveController = null;
      if (!controller.signal.aborted) updateSaveButton();
    }
  }

  // ---- restart backend -------------------------------------------------------

  async function _pollBackendReady(signal, maxAttempts = 30, intervalMs = 2000) {
    for (let i = 0; i < maxAttempts; i++) {
      await waitFor(intervalMs, signal);
      try {
        const userQuery = state.userId ? `?user_id=${encodeURIComponent(state.userId)}` : "";
        const res = await fetchWithTimeout(`/api/backend-status${userQuery}`, {}, signal, 5000);
        if (res.ok) {
          const data = await res.json();
          if (data.ready) return;
        }
      } catch (err) {
        if (isCancelled(err, signal)) throw err;
      }
    }
    throw new Error("Backend did not come back online in time");
  }

  async function restartBackend(signal) {
    if (settingsRestartBtn) {
      settingsRestartBtn.disabled = true;
      settingsRestartBtn.textContent = "Restarting…";
    }
    try {
      const userQuery = state.userId ? `?user_id=${encodeURIComponent(state.userId)}` : "";
      const res = await fetchWithTimeout(`/api/restart-backend${userQuery}`, { method: "POST" }, signal, 30_000);
      if (!res.ok) throw new Error(await res.text());
      await _pollBackendReady(signal);
      setSettingsStatus("Saved and backend restarted", "success");
      clearSettingsStatusLater(3000);
    } catch (err) {
      if (isCancelled(err, signal)) throw err;
      setSettingsStatus(`Settings were saved, but restart failed: ${err.message}`, "error");
    } finally {
      if (settingsRestartBtn) {
        settingsRestartBtn.disabled = false;
        settingsRestartBtn.textContent = "↺ Restart Backend";
      }
    }
  }

  // Tab switching
  document.querySelectorAll(".settings-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".settings-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".settings-pane").forEach((p) => p.classList.add("hidden"));
      tab.classList.add("active");
      const pane = document.getElementById(`tab-${tab.dataset.tab}`);
      if (pane) pane.classList.remove("hidden");
    });
  });

  if (settingsBtn) settingsBtn.addEventListener("click", openSettingsModal);
  if (settingsClose) settingsClose.addEventListener("click", closeSettingsModal);
  if (settingsCancel) settingsCancel.addEventListener("click", closeSettingsModal);
  if (settingsSave) settingsSave.addEventListener("click", saveSettings);
  settingsModal.addEventListener("input", updateSaveButton);
  settingsModal.addEventListener("change", updateSaveButton);
  settingsModal.addEventListener("click", () => queueMicrotask(updateSaveButton));
  document.getElementById("settings-workdir-reset")?.addEventListener("click", () => {
    const wdInput = document.getElementById("settings-default-workdir");
    if (wdInput) wdInput.value = "";
    updateSaveButton();
  });
  // Settings contain a multi-field draft. Do not dismiss it when the user
  // clicks the backdrop: an imprecise click should never force them to
  // re-enter values. Closing remains an explicit action (close button or
  // Escape), and saving is handled by the Save button.

  return { open: openSettingsModal, close: closeSettingsModal, reload: loadSettingsData };
}
