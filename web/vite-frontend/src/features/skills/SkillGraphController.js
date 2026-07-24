import { Network, DataSet } from "vis-network/standalone";

export function createSkillGraphController({
  state,
  centerTabs,
  centerTabPanels,
  activateCenterTab,
  renderMarkdown,
  knowledgeReviewBanner,
}) {
  let skillGraphTab = null;

  const SKILL_GRAPH_COLORS = {
    basic: { background: "#3B82F6", border: "#2563EB", highlight: { background: "#60A5FA", border: "#93C5FD" } },
    capability: { background: "#3B82F6", border: "#2563EB", highlight: { background: "#60A5FA", border: "#93C5FD" } },
    workflow: { background: "#14B8A6", border: "#0F766E", highlight: { background: "#2DD4BF", border: "#5EEAD4" } },
    procedure: { background: "#14B8A6", border: "#0F766E", highlight: { background: "#2DD4BF", border: "#5EEAD4" } },
    heuristic: { background: "#F59E0B", border: "#D97706", highlight: { background: "#FBBF24", border: "#FDE68A" } },
    limitation: { background: "#EF4444", border: "#DC2626", highlight: { background: "#F87171", border: "#FCA5A5" } },
    constraint: { background: "#EF4444", border: "#DC2626", highlight: { background: "#F87171", border: "#FCA5A5" } },
    tool: { background: "#06B6D4", border: "#0891B2", highlight: { background: "#22D3EE", border: "#67E8F9" } },
    generic: { background: "#475569", border: "#64748B", highlight: { background: "#64748B", border: "#94A3B8" } },
  };
  const VIRTUAL_NODE_COLOR = {
    background: "rgba(239, 68, 68, 0.10)",
    border: "rgba(248, 113, 113, 0.78)",
    highlight: {
      background: "rgba(239, 68, 68, 0.18)",
      border: "rgba(252, 165, 165, 0.95)",
    },
  };

  function hexToRgb(hex) {
    const value = hex.replace("#", "");
    return [
      parseInt(value.slice(0, 2), 16),
      parseInt(value.slice(2, 4), 16),
      parseInt(value.slice(4, 6), 16),
    ];
  }

  function rgba(hex, alpha) {
    const [r, g, b] = hexToRgb(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function skillGraphNodeColor(type, enabled = true, skillLevel = null, virtual = false) {
    if (virtual) return VIRTUAL_NODE_COLOR;
    const kind = skillGraphNodeKindFor(type, skillLevel).value;
    const color = SKILL_GRAPH_COLORS[kind] || SKILL_GRAPH_COLORS[type] || SKILL_GRAPH_COLORS.generic;
    if (enabled !== false) return color;
    return {
      background: rgba(color.background, 0.28),
      border: rgba(color.border, 0.34),
      highlight: {
        background: rgba(color.highlight.background, 0.42),
        border: rgba(color.highlight.border, 0.55),
      },
    };
  }

  function skillGraphEdgeColor(disabled = false) {
    return disabled
      ? { color: "rgba(140, 160, 194, 0.16)", highlight: "rgba(125, 211, 252, 0.38)" }
      : { color: "rgba(140, 160, 194, 0.45)", highlight: "#7dd3fc" };
  }

  function isEmptyDetailValue(value) {
    if (value === undefined || value === null || value === "") return true;
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === "object") return Object.keys(value).length === 0;
    return false;
  }

  function formatDetailValue(value) {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return JSON.stringify(value, null, 2);
  }

  function createSkillGraphSection(title, children) {
    const visibleChildren = children.filter(Boolean);
    if (!visibleChildren.length) return null;
    const section = document.createElement("section");
    section.className = "skill-graph-detail-section";
    const heading = document.createElement("h4");
    heading.textContent = title;
    section.append(heading, ...visibleChildren);
    return section;
  }

  function createSkillGraphMarkdown(value, className = "skill-graph-markdown") {
    const formatted = formatDetailValue(value);
    if (!formatted) return null;
    const div = document.createElement("div");
    div.className = className;
    div.innerHTML = renderMarkdown(formatted);
    return div;
  }

  function createSkillGraphFacts(items) {
    const facts = document.createElement("dl");
    facts.className = "skill-graph-facts";
    for (const [key, value, options = {}] of items) {
      if (isEmptyDetailValue(value)) continue;
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      const formatted = formatDetailValue(value);
      if (options.markdown) {
        dd.appendChild(createSkillGraphMarkdown(value));
      } else if (formatted.includes("\n")) {
        const pre = document.createElement("pre");
        pre.textContent = formatted;
        dd.appendChild(pre);
      } else {
        dd.textContent = formatted;
      }
      facts.append(dt, dd);
    }
    return facts.children.length ? facts : null;
  }

  function createSkillGraphList(values) {
    if (!Array.isArray(values) || !values.length) return null;
    const list = document.createElement("ul");
    list.className = "skill-graph-list";
    values.forEach((value) => {
      const item = document.createElement("li");
      const formatted = formatDetailValue(value);
      if (typeof value === "string") {
        item.appendChild(createSkillGraphMarkdown(formatted, "skill-graph-inline-markdown"));
      } else {
        item.textContent = formatted;
      }
      list.appendChild(item);
    });
    return list;
  }

  function createSkillGraphObjectList(values, titleKey = "filename") {
    if (!Array.isArray(values) || !values.length) return null;
    const list = document.createElement("div");
    list.className = "skill-graph-object-list";
    values.forEach((value, index) => {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = value?.[titleKey] || value?.name || value?.id || `Item ${index + 1}`;
      details.append(summary, createSkillGraphMarkdown(value));
      list.appendChild(details);
    });
    return list;
  }

  function skillGraphAttachmentPath(item) {
    const folder = String(item?.folder || "").replace(/^\/+|\/+$/g, "");
    const filename = item?.filename || item?.name || item?.id || "";
    return folder ? `${folder}/${filename}` : filename;
  }

  function skillGraphAttachmentKind(item) {
    return String(item?.kind || item?.metadata?.kind || "").toLowerCase();
  }

  function dedupeSkillGraphAttachments(values) {
    const seen = new Set();
    return (values || []).filter((item) => {
      const key = skillGraphAttachmentPath(item) || JSON.stringify(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function createSkillGraphAttachmentList(values, folder = "") {
    const items = dedupeSkillGraphAttachments(values);
    if (!items.length) return null;
    const list = document.createElement("div");
    list.className = "skill-graph-object-list";
    items.forEach((value, index) => {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const fullPath = skillGraphAttachmentPath(value);
      const path = attachmentDisplayName(fullPath, folder || value?.folder) || `File ${index + 1}`;
      const kind = skillGraphAttachmentKind(value);
      summary.textContent = kind ? `${path} [${kind}]` : path;
      details.append(summary, createSkillGraphMarkdown(value));
      list.appendChild(details);
    });
    return list;
  }

  function groupSkillGraphAttachments(node) {
    const grouped = new Map();
    const add = (item, fallbackFolder = "") => {
      const folder = item?.folder || fallbackFolder || "files";
      if (!grouped.has(folder)) grouped.set(folder, []);
      grouped.get(folder).push(item);
    };
    (node.assets || []).forEach((asset) => add(asset));
    (node.scripts || []).forEach((script) => add(script, "scripts"));
    return Array.from(grouped.entries())
      .map(([folder, items]) => [folder, dedupeSkillGraphAttachments(items)])
      .filter(([, items]) => items.length)
      .sort(([a], [b]) => a.localeCompare(b));
  }

  function createSkillGraphLinks(nodeId) {
    const edges = skillGraphTab?.edges || [];
    const nodeData = skillGraphTab?.nodeData || new Map();
    const related = [
      ...edges
        .filter((edge) => edge.from === nodeId)
        .map((edge) => ({ direction: "Outgoing", relation: edge.relation, node: nodeData.get(edge.to), nodeId: edge.to })),
      ...edges
        .filter((edge) => edge.to === nodeId)
        .map((edge) => ({ direction: "Incoming", relation: edge.relation, node: nodeData.get(edge.from), nodeId: edge.from })),
    ];
    if (!related.length) return null;

    const list = document.createElement("div");
    list.className = "skill-graph-links";
    related.forEach((link) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "skill-graph-link-row";
      const label = link.node?.title || link.node?.label || link.nodeId;
      row.innerHTML = `
        <span class="skill-graph-link-direction"></span>
        <span class="skill-graph-link-main"></span>
        <span class="skill-graph-link-relation"></span>
      `;
      row.querySelector(".skill-graph-link-direction").textContent = link.direction;
      row.querySelector(".skill-graph-link-main").textContent = label;
      row.querySelector(".skill-graph-link-relation").textContent = link.relation || "related";
      row.addEventListener("click", () => {
        if (!skillGraphTab?.network) return;
        skillGraphTab.network.selectNodes([link.nodeId]);
        skillGraphTab.network.focus(link.nodeId, { scale: 1.05, animation: { duration: 280, easingFunction: "easeInOutQuad" } });
        renderSkillGraphDetail(link.node);
      });
      list.appendChild(row);
    });
    return list;
  }

  function skillGraphAttachedContextFacts(nodeId) {
    const edges = skillGraphTab?.edges || [];
    const heuristics = edges.filter((edge) => edge.to === nodeId && edge.relation === "heuristic_for").length;
    const limitations = edges.filter((edge) => (
      edge.to === nodeId && (edge.relation === "constraint_on" || edge.relation === "warning_about")
    )).length;
    return createSkillGraphFacts([
      ["Heuristics", heuristics],
      ["Limitations", limitations],
    ]);
  }

  function csvToList(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function listToCsv(values) {
    return Array.isArray(values) ? values.join(", ") : "";
  }

  function skillGraphAvailableSkillNames(exclude = "") {
    return Array.from(skillGraphTab?.nodeData?.values?.() || [])
      .map((node) => node.skill_name)
      .filter((name) => name && name !== exclude)
      .sort((a, b) => a.localeCompare(b));
  }

  function skillGraphEntryTypes() {
    return ["capability", "procedure", "workflow", "tool", "repository", "environment", "dependency", "data", "analytical", "heuristic", "constraint", "generic"];
  }

  const SKILL_GRAPH_NODE_KINDS = [
    {
      value: "basic",
      label: "Basic",
      entry_type: "capability",
      skill_level: "L1",
      hint: "General skill or concept node.",
    },
    {
      value: "workflow",
      label: "Workflow",
      entry_type: "workflow",
      skill_level: "L2",
      hint: "A multi-step procedure or task flow.",
    },
    {
      value: "tool",
      label: "Tool",
      entry_type: "tool",
      skill_level: "L1",
      hint: "A reusable tool, command, or external capability.",
    },
    {
      value: "repository",
      label: "Repository",
      entry_type: "repository",
      skill_level: "L1",
      hint: "A codebase or other source repository.",
    },
    {
      value: "environment",
      label: "Environment",
      entry_type: "environment",
      skill_level: "L1",
      hint: "A runtime, platform, or execution environment.",
    },
    {
      value: "dependency",
      label: "Dependency",
      entry_type: "dependency",
      skill_level: "L1",
      hint: "A required package, service, or prerequisite resource.",
    },
    {
      value: "data",
      label: "Data",
      entry_type: "data",
      skill_level: "L1",
      hint: "A dataset, data source, or data-handling capability.",
    },
    {
      value: "analytical",
      label: "Analytical",
      entry_type: "analytical",
      skill_level: "L1",
      hint: "An analysis method, model, or interpretation capability.",
    },
    {
      value: "heuristic",
      label: "Heuristic",
      entry_type: "heuristic",
      skill_level: "L3",
      hint: "A practical rule, preference, or decision guide.",
    },
    {
      value: "limitation",
      label: "Limitation",
      entry_type: "constraint",
      skill_level: "L4",
      hint: "A constraint, caveat, warning, or known failure mode.",
    },
    {
      value: "generic",
      label: "Generic",
      entry_type: "generic",
      skill_level: "L1",
      hint: "An uncategorized durable graph entry.",
    },
  ];

  function skillGraphNodeKindFor(entryType, skillLevel) {
    if (entryType === "procedure") return SKILL_GRAPH_NODE_KINDS.find((kind) => kind.value === "workflow");
    if (entryType === "constraint") return SKILL_GRAPH_NODE_KINDS.find((kind) => kind.value === "limitation");
    if (entryType === "heuristic") return SKILL_GRAPH_NODE_KINDS.find((kind) => kind.value === "heuristic");
    return SKILL_GRAPH_NODE_KINDS.find((kind) => (
      kind.entry_type === entryType || kind.skill_level === skillLevel
    )) || SKILL_GRAPH_NODE_KINDS[0];
  }

  function populateSkillGraphNodeKindSelect(select, value = "basic") {
    select.innerHTML = "";
    SKILL_GRAPH_NODE_KINDS.forEach((kind) => {
      const option = document.createElement("option");
      option.value = kind.value;
      option.textContent = kind.label;
      select.appendChild(option);
    });
    select.value = value;
  }

  function updateSkillGraphNodeKindHint(host, value) {
    const hint = host.querySelector("[data-node-kind-hint]");
    const kind = SKILL_GRAPH_NODE_KINDS.find((item) => item.value === value) || SKILL_GRAPH_NODE_KINDS[0];
    if (hint) hint.textContent = kind.hint;
    const relationLabel = host.querySelector("[data-relation-label]");
    const relationHint = host.querySelector("[data-relation-hint]");
    if (relationLabel) {
      relationLabel.textContent = kind.value === "heuristic" || kind.value === "limitation"
        ? "Attached to"
        : "Dependencies";
    }
    if (relationHint) {
      relationHint.textContent = kind.value === "heuristic"
        ? "These parent nodes will show this heuristic during progressive retrieval."
        : kind.value === "limitation"
          ? "These parent nodes will show this limitation during progressive retrieval."
          : "These nodes are required or related prerequisites.";
    }
  }

  function selectedSkillGraphNodeKind(host, selectorPrefix = "data-edit-field") {
    const value = host.querySelector(`[${selectorPrefix}='node_kind']`)?.value || "basic";
    return SKILL_GRAPH_NODE_KINDS.find((kind) => kind.value === value) || SKILL_GRAPH_NODE_KINDS[0];
  }

  function skillGraphDisplayNodeType(entryType, skillLevel) {
    return skillGraphNodeKindFor(entryType, skillLevel).label.toLowerCase();
  }

  function createSkillGraphEditToggle(node) {
    if (!node.skill_name && !node.graph_editable) return null;
    const host = document.createElement("section");
    host.className = "skill-graph-edit-toggle skill-graph-detail-section";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost mini-btn";
    button.textContent = node.skill_name ? "Edit" : "Edit node";
    button.addEventListener("click", () => {
      skillGraphTab.detail.innerHTML = "";
      const editorHost = document.createElement("section");
      editorHost.className = "skill-graph-editor";
      editorHost.innerHTML = `<h4>${node.skill_name ? "Edit Skill" : "Edit Node"}</h4><div class="skill-graph-editor-status">Loading editor...</div>`;
      skillGraphTab.detail.appendChild(editorHost);
      if (node.skill_name) loadSkillGraphEditor(node, editorHost);
      else loadSkillGraphNodeEditor(node, editorHost);
    });
    host.appendChild(button);
    if (node.skill_name) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost mini-btn danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeSkillGraphNode(node));
      host.appendChild(remove);
    } else if (node.graph_editable) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost mini-btn danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeSkillGraphNode(node));
      host.appendChild(remove);
    }
    return host;
  }

  function skillGraphConnectedSkillNames(node) {
    const editableRelations = new Set(["dependency", "heuristic_for", "constraint_on"]);
    return (skillGraphTab?.edges || [])
      .filter((edge) => edge.from === node.id && editableRelations.has(edge.relation))
      .map((edge) => skillGraphTab?.nodeData?.get(edge.to)?.skill_name)
      .filter(Boolean);
  }

  function skillGraphEditorSnapshot(host) {
    const kind = selectedSkillGraphNodeKind(host);
    return {
      description: host.querySelector("[data-edit-field='description']")?.value || "",
      entry_type: kind.entry_type,
      skill_level: kind.skill_level,
      node_kind: kind.value,
      tags: csvToList(host.querySelector("[data-edit-field='tags']")?.value),
      dependent_skills: Array.from(host._skillGraphDependencySelected || []),
      content: host.querySelector("[data-edit-field='content']")?.value || "",
      pendingRemovals: Array.from(host.querySelectorAll("[data-attachment-path][aria-pressed='true']"))
        .map((button) => button.dataset.attachmentPath),
      uploadFolders: Array.from(host.querySelectorAll("[data-upload-folder]"))
        .map((folder) => folder.dataset.uploadFolder)
        .filter(Boolean),
    };
  }

  function applySkillGraphEditorSnapshot(host, snapshot) {
    const setValue = (field, value) => {
      const el = host.querySelector(`[data-edit-field='${field}']`);
      if (el) el.value = value;
    };
    setValue("description", snapshot.description || "");
    setValue("node_kind", snapshot.node_kind || skillGraphNodeKindFor(snapshot.entry_type, snapshot.skill_level).value);
    updateSkillGraphNodeKindHint(host, host.querySelector("[data-edit-field='node_kind']")?.value || "basic");
    setValue("tags", listToCsv(snapshot.tags));
    setValue("content", snapshot.content || "");
    const selectedDeps = new Set(snapshot.dependent_skills || []);
    host._skillGraphDependencySelected = selectedDeps;
    renderSkillGraphDependencyPicker(host, host._skillGraphDependencySkills || [], selectedDeps);
    host.querySelectorAll("[data-attachment-path]").forEach((button) => {
      const remove = (snapshot.pendingRemovals || []).includes(button.dataset.attachmentPath);
      button.setAttribute("aria-pressed", String(remove));
      button.closest(".skill-graph-attachment-row")?.classList.toggle("pending-remove", remove);
    });
    syncSkillGraphUploadFolders(host, snapshot.uploadFolders || host._skillGraphUploadFolders || []);
  }

  function renderSkillGraphDependencyPicker(host, skills, selected) {
    const list = host.querySelector(".skill-graph-dependency-list");
    const filter = host.querySelector("[data-dependency-filter]");
    const selectedSet = selected instanceof Set ? selected : new Set(selected || []);
    host._skillGraphDependencySkills = skills;
    host._skillGraphDependencySelected = selectedSet;
    const render = () => {
      const query = (filter.value || "").trim().toLowerCase();
      list.innerHTML = "";
      skills
        .filter((skill) => !query || skill.toLowerCase().includes(query) || selectedSet.has(skill))
        .forEach((skill) => {
          const label = document.createElement("label");
          label.className = "skill-graph-dependency-row";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = skill;
          checkbox.dataset.dependencySkill = skill;
          checkbox.checked = selectedSet.has(skill);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedSet.add(skill);
            else selectedSet.delete(skill);
            host._skillGraphDependencySelected = selectedSet;
            pushSkillGraphEditorHistory(host);
            render();
          });
          const span = document.createElement("span");
          span.textContent = skill;
          label.append(checkbox, span);
          list.appendChild(label);
        });
      if (!list.children.length) {
        const empty = document.createElement("div");
        empty.className = "skill-graph-editor-empty";
        empty.textContent = "No matching skills.";
        list.appendChild(empty);
      }
    };
    if (!filter.dataset.boundDependencyFilter) {
      filter.dataset.boundDependencyFilter = "true";
      filter.addEventListener("input", render);
    }
    render();
  }

  function attachmentDisplayName(path, folder = "") {
    const normalizedPath = String(path || "");
    const normalizedFolder = String(folder || "").replace(/^\/+|\/+$/g, "");
    const prefix = normalizedFolder ? `${normalizedFolder}/` : "";
    return normalizedPath.startsWith(prefix) ? normalizedPath.slice(prefix.length) : normalizedPath.split("/").pop();
  }

  function syncSkillGraphUploadFolders(host, folders) {
    const normalized = Array.from(new Set((folders || []).map((folder) => String(folder || "").trim()).filter(Boolean)));
    host._skillGraphUploadFolders = normalized;
    host.querySelectorAll("[data-upload-folder]").forEach((folderEl) => {
      if (!normalized.includes(folderEl.dataset.uploadFolder)) folderEl.remove();
    });
    normalized.forEach((folder) => ensureSkillGraphFolderCard(host, folder));
  }

  function ensureSkillGraphFolderCard(host, folder) {
    host._skillGraphPendingUploads ||= [];
    const list = host.querySelector(".skill-graph-folder-list");
    let card = Array.from(list.querySelectorAll("[data-upload-folder]"))
      .find((item) => item.dataset.uploadFolder === folder);
    if (card) return card;
    card = document.createElement("div");
    card.className = "skill-graph-folder-card";
    card.dataset.uploadFolder = folder;
    card.innerHTML = `
      <div class="skill-graph-folder-header">
        <span></span>
        <label class="ghost mini-btn skill-graph-folder-add" title="Add files">
          +
          <input data-upload-files type="file" multiple />
        </label>
      </div>
      <div class="skill-graph-folder-files"></div>
      <div class="skill-graph-folder-pending"></div>
    `;
    card.querySelector(".skill-graph-folder-header span").textContent = folder;
    card.querySelector("[data-upload-files]").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;
      host._skillGraphPendingUploads.push({ folder, files });
      event.target.value = "";
      renderSkillGraphPendingUploads(host, folder);
    });
    list.appendChild(card);
    return card;
  }

  function renderSkillGraphPendingUploads(host, folder) {
    const card = Array.from(host.querySelectorAll("[data-upload-folder]"))
      .find((item) => item.dataset.uploadFolder === folder);
    if (!card) return;
    const pendingEl = card.querySelector(".skill-graph-folder-pending");
    const pending = (host._skillGraphPendingUploads || [])
      .filter((item) => item.folder === folder)
      .flatMap((item) => item.files);
    pendingEl.innerHTML = "";
    pending.forEach((file) => {
      const item = document.createElement("div");
      item.className = "skill-graph-pending-file";
      item.textContent = file.name;
      pendingEl.appendChild(item);
    });
  }

  function addSkillGraphFolder(host) {
    const input = host.querySelector("[data-new-folder-name]");
    const folder = (input.value || "").trim().replace(/^\/+|\/+$/g, "");
    if (!folder) return;
    ensureSkillGraphFolderCard(host, folder);
    host._skillGraphUploadFolders = Array.from(new Set([...(host._skillGraphUploadFolders || []), folder]));
    input.value = "";
    pushSkillGraphEditorHistory(host);
  }

  function renderSkillGraphCreatePanel() {
    if (!skillGraphTab?.detail) return;
    skillGraphTab.panel.classList.add("has-selection");
    skillGraphTab.network?.unselectAll();
    const host = document.createElement("section");
    host.className = "skill-graph-editor";
    host.innerHTML = `
      <h4>Add Node</h4>
      <div class="skill-graph-editor-actions">
        <button type="button" class="ghost mini-btn" data-create-action="cancel">Cancel</button>
        <button type="button" class="primary mini-btn" data-create-action="save">Create</button>
      </div>
      <label class="skill-graph-editor-field">Name
        <input data-create-field="name" type="text" placeholder="new-skill-name" />
      </label>
      <label class="skill-graph-editor-field">Description
        <input data-create-field="description" type="text" />
      </label>
      <label class="skill-graph-editor-field">Node type
        <select data-create-field="node_kind"></select>
        <span class="skill-graph-field-hint" data-node-kind-hint></span>
      </label>
      <label class="skill-graph-editor-field">Tags
        <input data-create-field="tags" type="text" placeholder="comma separated" />
      </label>
      <div class="skill-graph-editor-field"><span data-relation-label>Dependencies</span>
        <input data-dependency-filter type="text" placeholder="filter skills" />
        <span class="skill-graph-field-hint" data-relation-hint></span>
        <div class="skill-graph-dependency-list"></div>
      </div>
      <label class="skill-graph-editor-field">SKILL.md body
        <textarea data-create-field="content" spellcheck="false"></textarea>
      </label>
      <div class="skill-graph-editor-attachments">
        <strong>Attachments</strong>
        <div class="skill-graph-new-folder">
          <input data-new-folder-name type="text" placeholder="new folder, e.g. references/setup" />
          <button type="button" class="ghost mini-btn" data-create-action="add-folder">New folder</button>
        </div>
        <div class="skill-graph-folder-list"></div>
      </div>
      <div class="skill-graph-editor-status"></div>
    `;
    const nodeKindSelect = host.querySelector("[data-create-field='node_kind']");
    populateSkillGraphNodeKindSelect(nodeKindSelect, "basic");
    updateSkillGraphNodeKindHint(host, "basic");
    nodeKindSelect.addEventListener("change", () => updateSkillGraphNodeKindHint(host, nodeKindSelect.value));
    host.querySelector("[data-create-field='content']").value = "# New skill\n\nDescribe how and when to use this skill.";
    renderSkillGraphDependencyPicker(host, skillGraphAvailableSkillNames(), []);
    host.querySelector("[data-create-action='cancel']").addEventListener("click", () => renderSkillGraphDetail(null));
    host.querySelector("[data-create-action='save']").addEventListener("click", () => createSkillGraphNode(host));
    host.querySelector("[data-create-action='add-folder']").addEventListener("click", () => addSkillGraphFolder(host));
    host.querySelector("[data-new-folder-name]").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addSkillGraphFolder(host);
      }
    });
    skillGraphTab.detail.innerHTML = "";
    skillGraphTab.detail.appendChild(host);
    host.querySelector("[data-create-field='name']")?.focus();
  }

  async function createSkillGraphNode(host) {
    const status = host.querySelector(".skill-graph-editor-status");
    const saveButton = host.querySelector("[data-create-action='save']");
    const name = host.querySelector("[data-create-field='name']")?.value.trim();
    if (!name) {
      status.classList.add("error");
      status.textContent = "Name is required.";
      return;
    }
    const kind = selectedSkillGraphNodeKind(host, "data-create-field");
    const payload = {
      name,
      description: host.querySelector("[data-create-field='description']")?.value || "",
      entry_type: kind.entry_type,
      skill_level: kind.skill_level,
      tags: csvToList(host.querySelector("[data-create-field='tags']")?.value),
      dependent_skills: Array.from(host._skillGraphDependencySelected || []),
      content: host.querySelector("[data-create-field='content']")?.value || "",
    };
    status.textContent = "Creating...";
    status.classList.remove("error");
    saveButton.disabled = true;
    try {
      const resp = await fetch("/api/skill-graph/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error(await resp.text());
      for (const pending of host._skillGraphPendingUploads || []) {
        const files = pending.files || [];
        if (!files.length) continue;
        const formData = new FormData();
        formData.append("category", pending.folder || "references");
        files.forEach((file) => formData.append("files", file));
        const uploadResp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(name)}/attachments`, {
          method: "POST",
          body: formData,
        });
        if (!uploadResp.ok) throw new Error(await uploadResp.text());
      }
      await refreshSkillGraphData();
      const created = Array.from(skillGraphTab.nodeData.values()).find((node) => node.skill_name === name);
      if (created) {
        skillGraphTab.network?.selectNodes([created.id]);
        renderSkillGraphDetail(created);
      }
    } catch (err) {
      status.classList.add("error");
      status.textContent = `Create failed: ${String(err.message || err)}`;
    } finally {
      saveButton.disabled = false;
    }
  }

  async function removeSkillGraphNode(node) {
    if (!node) return;
    if (!node.skill_name) {
      if (!node.graph_editable) return;
      if (!window.confirm(`Remove graph node '${node.title || node.label}'? This also removes its connections.`)) return;
      const previousStatus = skillGraphTab.status.textContent;
      skillGraphTab.status.textContent = "removing";
      skillGraphTab.status.className = "graph-status status-polling";
      try {
        const resp = await fetch(`/api/skill-graph/nodes/${encodeURIComponent(node.id)}`, { method: "DELETE" });
        if (!resp.ok) throw new Error(await resp.text());
        await refreshSkillGraphData();
        renderSkillGraphDetail(null);
      } catch (err) {
        skillGraphTab.status.className = "graph-status status-idle";
        skillGraphTab.status.textContent = previousStatus || "idle";
        const error = document.createElement("div");
        error.className = "skill-graph-editor-status error";
        error.textContent = `Remove failed: ${String(err.message || err)}`;
        skillGraphTab.detail.prepend(error);
      }
      return;
    }
    if (!node.is_custom) {
      window.alert(`Managed skill '${node.skill_name}' cannot be removed here.`);
      return;
    }
    const message = node.is_custom
      ? `Remove custom skill '${node.skill_name}' from the workspace?`
      : `Remove default skill '${node.skill_name}'?\n\nThis can delete files from the bundled skill directory in this checkout. Only continue if you really intend to remove it.`;
    if (!window.confirm(message)) return;
    const previousStatus = skillGraphTab.status.textContent;
    skillGraphTab.status.textContent = "removing";
    skillGraphTab.status.className = "graph-status status-polling";
    try {
      const resp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(node.skill_name)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(await resp.text());
      await refreshSkillGraphData();
      renderSkillGraphDetail(null);
    } catch (err) {
      skillGraphTab.status.className = "graph-status status-idle";
      skillGraphTab.status.textContent = previousStatus || "idle";
      const error = document.createElement("div");
      error.className = "skill-graph-editor-status error";
      error.textContent = `Remove failed: ${String(err.message || err)}`;
      skillGraphTab.detail.prepend(error);
    }
  }

  function pushSkillGraphEditorHistory(host) {
    const edit = host._skillGraphEdit;
    if (!edit || edit.applying) return;
    edit.history = edit.history.slice(0, edit.index + 1);
    edit.history.push(skillGraphEditorSnapshot(host));
    edit.index = edit.history.length - 1;
  }

  function moveSkillGraphEditorHistory(host, direction) {
    const edit = host._skillGraphEdit;
    if (!edit) return;
    const nextIndex = edit.index + direction;
    if (nextIndex < 0 || nextIndex >= edit.history.length) return;
    edit.applying = true;
    edit.index = nextIndex;
    applySkillGraphEditorSnapshot(host, edit.history[edit.index]);
    edit.applying = false;
  }

  function renderSkillGraphEditor(host, node, data) {
    const metadata = data.metadata || {};
    const attachments = data.attachments || [];
    host.innerHTML = `
      <h4>Edit Skill</h4>
      <div class="skill-graph-editor-actions">
        <button type="button" class="ghost mini-btn" data-edit-action="cancel">Cancel</button>
        <button type="button" class="primary mini-btn" data-edit-action="save">Save</button>
      </div>
      <label class="skill-graph-editor-field">Description
        <input data-edit-field="description" type="text" />
      </label>
      <label class="skill-graph-editor-field">Node type
        <select data-edit-field="node_kind"></select>
        <span class="skill-graph-field-hint" data-node-kind-hint></span>
      </label>
      <label class="skill-graph-editor-field">Tags
        <input data-edit-field="tags" type="text" placeholder="comma separated" />
      </label>
      <div class="skill-graph-editor-field"><span data-relation-label>Dependencies</span>
        <input data-dependency-filter type="text" placeholder="filter skills" />
        <span class="skill-graph-field-hint" data-relation-hint></span>
        <div class="skill-graph-dependency-list"></div>
      </div>
      <label class="skill-graph-editor-field">SKILL.md body
        <textarea data-edit-field="content" spellcheck="false"></textarea>
      </label>
      <div class="skill-graph-editor-attachments">
        <strong>Attachments</strong>
        <div class="skill-graph-new-folder">
          <input data-new-folder-name type="text" placeholder="new folder, e.g. references/setup" />
          <button type="button" class="ghost mini-btn" data-edit-action="add-folder">New folder</button>
        </div>
        <div class="skill-graph-folder-list"></div>
      </div>
      <div class="skill-graph-editor-status"></div>
    `;

    const nodeKindSelect = host.querySelector("[data-edit-field='node_kind']");
    const currentKind = skillGraphNodeKindFor(data.entry_type, data.skill_level);
    populateSkillGraphNodeKindSelect(nodeKindSelect, currentKind.value);
    updateSkillGraphNodeKindHint(host, currentKind.value);
    nodeKindSelect.addEventListener("change", () => {
      updateSkillGraphNodeKindHint(host, nodeKindSelect.value);
      pushSkillGraphEditorHistory(host);
    });
    host.querySelector("[data-edit-field='description']").value = data.description || "";
    host.querySelector("[data-edit-field='tags']").value = listToCsv(data.tags || metadata.tags);
    host.querySelector("[data-edit-field='content']").value = data.content || "";
    // Frontmatter is the source of truth for persisted skill dependencies,
    // while the graph may already contain seeded connections from an earlier
    // refresh.  Include both so the picker faithfully reflects the graph.
    const dependentSkills = Array.from(new Set([
      ...(data.dependent_skills || metadata.dependent_skills || []),
      ...skillGraphConnectedSkillNames(node),
    ]));
    renderSkillGraphDependencyPicker(
      host,
      Array.from(new Set([...(data.available_skills || []), ...dependentSkills])),
      dependentSkills,
    );

    const initialFolders = [];
    attachments.forEach((category) => {
      initialFolders.push(category.name);
      const group = ensureSkillGraphFolderCard(host, category.name);
      const filesEl = group.querySelector(".skill-graph-folder-files");
      (category.files || []).forEach((file) => {
        const row = document.createElement("div");
        row.className = "skill-graph-attachment-row";
        row.innerHTML = '<span></span><button type="button" class="ghost mini-btn" aria-pressed="false">Remove</button>';
        row.querySelector("span").textContent = attachmentDisplayName(file.path, category.name);
        const button = row.querySelector("button");
        button.dataset.attachmentPath = file.path;
        button.addEventListener("click", () => {
          const pressed = button.getAttribute("aria-pressed") === "true";
          button.setAttribute("aria-pressed", String(!pressed));
          row.classList.toggle("pending-remove", !pressed);
          pushSkillGraphEditorHistory(host);
        });
        filesEl.appendChild(row);
      });
    });
    syncSkillGraphUploadFolders(host, initialFolders);

    host._skillGraphEdit = {
      skillName: node.skill_name,
      nodeId: node.id,
      history: [],
      index: -1,
      applying: false,
    };
    pushSkillGraphEditorHistory(host);

    host.querySelectorAll("[data-edit-field]").forEach((field) => {
      if (field.type === "file") return;
      field.addEventListener("input", () => pushSkillGraphEditorHistory(host));
      field.addEventListener("change", () => pushSkillGraphEditorHistory(host));
    });
    host.querySelector("[data-edit-action='cancel']").addEventListener("click", () => {
      renderSkillGraphDetail(skillGraphTab?.nodeData.get(node.id) || node);
    });
    host.querySelector("[data-edit-action='save']").addEventListener("click", () => saveSkillGraphEditor(host));
    host.querySelector("[data-edit-action='add-folder']").addEventListener("click", () => addSkillGraphFolder(host));
    host.querySelector("[data-new-folder-name]").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addSkillGraphFolder(host);
      }
    });
    host.addEventListener("keydown", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        moveSkillGraphEditorHistory(host, event.shiftKey ? 1 : -1);
      } else if (key === "y") {
        event.preventDefault();
        moveSkillGraphEditorHistory(host, 1);
      }
    });
  }

  function renderSkillGraphNodePicker(host, nodes, selected) {
    const list = host.querySelector(".skill-graph-dependency-list");
    const filter = host.querySelector("[data-dependency-filter]");
    const selectedSet = selected instanceof Set ? selected : new Set(selected || []);
    host._skillGraphNodeDependencies = selectedSet;
    const render = () => {
      const query = (filter.value || "").trim().toLowerCase();
      list.innerHTML = "";
      nodes
        .filter((candidate) => (
          !query
          || candidate.title.toLowerCase().includes(query)
          || selectedSet.has(candidate.id)
        ))
        .forEach((candidate) => {
          const label = document.createElement("label");
          label.className = "skill-graph-dependency-row";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = candidate.id;
          checkbox.checked = selectedSet.has(candidate.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedSet.add(candidate.id);
            else selectedSet.delete(candidate.id);
            host._skillGraphNodeDependencies = selectedSet;
            render();
          });
          const span = document.createElement("span");
          const kind = skillGraphDisplayNodeType(candidate.entry_type, candidate.skill_level);
          span.textContent = `${candidate.title} (${kind})`;
          label.append(checkbox, span);
          list.appendChild(label);
        });
      if (!list.children.length) {
        const empty = document.createElement("div");
        empty.className = "skill-graph-editor-empty";
        empty.textContent = "No matching nodes.";
        list.appendChild(empty);
      }
    };
    filter.addEventListener("input", render);
    render();
  }

  function renderSkillGraphNodeEditor(host, node, data) {
    host.innerHTML = `
      <h4>Edit Node</h4>
      <div class="skill-graph-editor-actions">
        <button type="button" class="ghost mini-btn" data-node-edit-action="cancel">Cancel</button>
        <button type="button" class="primary mini-btn" data-node-edit-action="save">Save</button>
      </div>
      <label class="skill-graph-editor-field">Title
        <input data-node-edit-field="title" type="text" />
      </label>
      <label class="skill-graph-editor-field">Node type
        <select data-node-edit-field="node_kind"></select>
        <span class="skill-graph-field-hint" data-node-kind-hint></span>
      </label>
      <label class="skill-graph-editor-field">Tags
        <input data-node-edit-field="tags" type="text" placeholder="comma separated" />
      </label>
      <div class="skill-graph-editor-field"><span data-relation-label>Dependencies</span>
        <input data-dependency-filter type="text" placeholder="filter nodes" />
        <span class="skill-graph-field-hint" data-relation-hint></span>
        <div class="skill-graph-dependency-list"></div>
      </div>
      <label class="skill-graph-editor-field">Content
        <textarea data-node-edit-field="content" spellcheck="false"></textarea>
      </label>
      <div class="skill-graph-editor-status"></div>
    `;
    const nodeKindSelect = host.querySelector("[data-node-edit-field='node_kind']");
    const currentKind = skillGraphNodeKindFor(data.entry_type, data.skill_level);
    populateSkillGraphNodeKindSelect(nodeKindSelect, currentKind.value);
    updateSkillGraphNodeKindHint(host, currentKind.value);
    nodeKindSelect.addEventListener("change", () => updateSkillGraphNodeKindHint(host, nodeKindSelect.value));
    host.querySelector("[data-node-edit-field='title']").value = data.title || "";
    host.querySelector("[data-node-edit-field='tags']").value = listToCsv(data.tags || []);
    host.querySelector("[data-node-edit-field='content']").value = data.content || "";
    renderSkillGraphNodePicker(host, data.available_nodes || [], data.dependent_node_ids || []);
    host._skillGraphNodeEdit = { nodeId: node.id };
    host.querySelector("[data-node-edit-action='cancel']").addEventListener("click", () => {
      renderSkillGraphDetail(skillGraphTab?.nodeData.get(node.id) || node);
    });
    host.querySelector("[data-node-edit-action='save']").addEventListener("click", () => saveSkillGraphNodeEditor(host));
  }

  async function loadSkillGraphNodeEditor(node, host) {
    try {
      const resp = await fetch(`/api/skill-graph/nodes/${encodeURIComponent(node.id)}/edit`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      renderSkillGraphNodeEditor(host, node, await resp.json());
    } catch (err) {
      host.innerHTML = `<h4>Edit Node</h4><div class="skill-graph-editor-status error">Editor unavailable: ${String(err.message || err)}</div>`;
    }
  }

  async function saveSkillGraphNodeEditor(host) {
    const edit = host._skillGraphNodeEdit;
    if (!edit) return;
    const status = host.querySelector(".skill-graph-editor-status");
    const saveButton = host.querySelector("[data-node-edit-action='save']");
    const kind = selectedSkillGraphNodeKind(host, "data-node-edit-field");
    const payload = {
      title: host.querySelector("[data-node-edit-field='title']")?.value || "",
      content: host.querySelector("[data-node-edit-field='content']")?.value || "",
      entry_type: kind.entry_type,
      skill_level: kind.skill_level,
      tags: csvToList(host.querySelector("[data-node-edit-field='tags']")?.value),
      dependent_node_ids: Array.from(host._skillGraphNodeDependencies || []),
    };
    status.textContent = "Saving...";
    status.classList.remove("error");
    saveButton.disabled = true;
    try {
      const resp = await fetch(`/api/skill-graph/nodes/${encodeURIComponent(edit.nodeId)}/edit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error(await resp.text());
      status.textContent = "Saved.";
      await refreshSkillGraphData({ selectedNodeId: edit.nodeId });
    } catch (err) {
      status.classList.add("error");
      status.textContent = `Save failed: ${String(err.message || err)}`;
    } finally {
      saveButton.disabled = false;
    }
  }

  async function loadSkillGraphEditor(node, host) {
    try {
      const resp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(node.skill_name)}/edit`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      renderSkillGraphEditor(host, node, await resp.json());
    } catch (err) {
      host.innerHTML = `<h4>Edit Skill</h4><div class="skill-graph-editor-status error">Editor unavailable: ${String(err.message || err)}</div>`;
    }
  }

  async function saveSkillGraphEditor(host) {
    const edit = host._skillGraphEdit;
    if (!edit) return;
    const status = host.querySelector(".skill-graph-editor-status");
    const saveButton = host.querySelector("[data-edit-action='save']");
    const snapshot = skillGraphEditorSnapshot(host);
    status.textContent = "Saving...";
    status.classList.remove("error");
    saveButton.disabled = true;
    try {
      const resp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(edit.skillName)}/edit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      });
      if (!resp.ok) throw new Error(await resp.text());
      for (const path of snapshot.pendingRemovals || []) {
        const delResp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(edit.skillName)}/attachments?path=${encodeURIComponent(path)}`, {
          method: "DELETE",
        });
        if (!delResp.ok) throw new Error(await delResp.text());
      }
      for (const pending of host._skillGraphPendingUploads || []) {
        const files = pending.files || [];
        if (!files.length) continue;
        const formData = new FormData();
        formData.append("category", pending.folder || "references");
        files.forEach((file) => formData.append("files", file));
        const uploadResp = await fetch(`/api/skill-graph/skills/${encodeURIComponent(edit.skillName)}/attachments`, {
          method: "POST",
          body: formData,
        });
        if (!uploadResp.ok) throw new Error(await uploadResp.text());
      }
      status.textContent = "Saved.";
      await refreshSkillGraphData({ selectedNodeId: edit.nodeId });
    } catch (err) {
      status.classList.add("error");
      status.textContent = `Save failed: ${String(err.message || err)}`;
    } finally {
      saveButton.disabled = false;
    }
  }

  function renderSkillGraphDetail(node) {
    if (!skillGraphTab?.detail) return;
    skillGraphTab.panel.classList.toggle("has-selection", Boolean(node));
    if (!node) {
      skillGraphTab.detail.innerHTML = "";
      return;
    }

    skillGraphTab.detail.innerHTML = "";
    const title = document.createElement("h3");
    title.textContent = node.title || node.label || "Untitled";
    const metadata = node.metadata || {};

    const meta = document.createElement("div");
    meta.className = "skill-graph-detail-meta";
    const metaItems = [
      skillGraphDisplayNodeType(node.entry_type, metadata?.skill_level),
      node.virtual ? "virtual / backing skill missing" : null,
      node.enabled === false ? "disabled" : "enabled",
      node.verification_status,
      node.refinement_status,
    ].filter(Boolean);
    meta.textContent = metaItems.join(" / ");

    const tags = document.createElement("div");
    tags.className = "skill-graph-tags";
    (node.tags || []).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag;
      tags.appendChild(chip);
    });

    const content = createSkillGraphMarkdown(node.content || "No content.", "skill-graph-detail-content skill-graph-markdown");

    const identity = createSkillGraphFacts([
      ["ID", node.id],
      ["Slug", node.slug],
      ["Skill", node.skill_name],
      ["Enabled", node.enabled !== false],
      ["Virtual", node.virtual === true],
      ["Type", skillGraphDisplayNodeType(node.entry_type, metadata.skill_level)],
      ["Aliases", node.aliases],
    ]);
    const quality = createSkillGraphFacts([
      ["Verification", metadata.verification_status || node.verification_status],
      ["Refinement", metadata.refinement_status || node.refinement_status],
      ["Kind", skillGraphDisplayNodeType(node.entry_type, metadata.skill_level)],
      ["Trust", metadata.trust_score ?? node.trust_score],
      ["Usage", metadata.usage_count ?? node.usage_count],
      ["Review count", metadata.review_count],
      ["Modify count", metadata.modify_count],
      ["Needs generalization", metadata.needs_generalization],
    ]);
    const provenance = createSkillGraphFacts([
      ["Source", metadata.source_provenance || node.source_provenance],
      ["Extraction", metadata.extraction_method],
      ["Timestamp", metadata.timestamp],
      ["Last reviewed", metadata.last_reviewed_at],
      ["Remote source", metadata.remote_source],
    ]);
    const requirements = createSkillGraphFacts([
      ["Applicability", metadata.applicability],
      ["Failure modes", metadata.failure_modes],
      ["Runtime", metadata.runtime_requirements],
      ["Related envs", metadata.related_environments],
      ["Script language", metadata.script_language],
      ["Script filename", metadata.script_filename],
      ["Script requirements", metadata.script_requirements],
    ]);
    const custom = createSkillGraphFacts([["Custom", metadata.custom]]);
    const attachmentSections = groupSkillGraphAttachments(node)
      .map(([folder, items]) => createSkillGraphSection(folder, [createSkillGraphAttachmentList(items, folder)]));

    skillGraphTab.detail.append(title, meta);
    if (tags.children.length) skillGraphTab.detail.appendChild(tags);
    const sections = [
      createSkillGraphEditToggle(node),
      createSkillGraphSection("Content", [content]),
      createSkillGraphSection("Identity", [identity]),
      createSkillGraphSection("Quality", [quality]),
      createSkillGraphSection("Provenance", [provenance]),
      createSkillGraphSection("References", [
        createSkillGraphList(node.internal_refs),
        createSkillGraphList(metadata.external_refs),
      ]),
      createSkillGraphSection("Progressive Retrieval", [skillGraphAttachedContextFacts(node.id)]),
      createSkillGraphSection("Execution Context", [requirements]),
      createSkillGraphSection("Feedback", [createSkillGraphObjectList(metadata.feedback_log, "verdict")]),
      ...attachmentSections,
      createSkillGraphSection("Custom Metadata", [custom]),
      createSkillGraphSection("Links", [createSkillGraphLinks(node.id)]),
    ].filter(Boolean);
    skillGraphTab.detail.append(...sections);
  }

  function ensureSkillGraphTab() {
    if (skillGraphTab) {
      activateCenterTab("skill-graph");
      return skillGraphTab;
    }

    const tabId = "skill-graph";
    const button = document.createElement("button");
    button.className = "center-tab";
    button.type = "button";
    button.role = "tab";
    button.dataset.tabId = tabId;
    button.id = `tab-${tabId}`;
    button.setAttribute("aria-selected", "false");
    button.setAttribute("aria-controls", `${tabId}-panel`);
    button.title = "Skill Graph";

    const title = document.createElement("span");
    title.className = "center-tab-title";
    title.textContent = "Skill Graph";
    button.appendChild(title);

    const close = document.createElement("span");
    close.className = "center-tab-close";
    close.dataset.closeTabId = tabId;
    close.setAttribute("aria-hidden", "true");
    close.textContent = "×";
    button.appendChild(close);

    const panel = document.createElement("div");
    panel.className = "center-tab-panel skill-graph-tab-panel";
    panel.id = `${tabId}-panel`;
    panel.role = "tabpanel";
    panel.dataset.tabId = tabId;
    panel.setAttribute("aria-labelledby", button.id);

    const header = document.createElement("div");
    header.className = "skill-graph-header";
    const heading = document.createElement("div");
    heading.innerHTML = '<div class="eyebrow">Knowledge</div><strong>Skill Graph</strong>';
    const actions = document.createElement("div");
    actions.className = "skill-graph-header-actions";
    const addNode = document.createElement("button");
    addNode.type = "button";
    addNode.className = "ghost mini-btn";
    addNode.textContent = "Add node";
    addNode.addEventListener("click", renderSkillGraphCreatePanel);
    const status = document.createElement("span");
    status.className = "graph-status status-idle";
    status.textContent = "idle";
    actions.append(addNode, status);
    header.append(heading, knowledgeReviewBanner, actions);

    const body = document.createElement("div");
    body.className = "skill-graph-body";
    const canvas = document.createElement("div");
    canvas.className = "skill-graph-canvas";
    const detail = document.createElement("aside");
    detail.className = "skill-graph-detail";
    body.append(canvas, detail);
    panel.append(header, body);

    centerTabs?.appendChild(button);
    centerTabPanels?.appendChild(panel);
    skillGraphTab = {
      button,
      panel,
      status,
      canvas,
      detail,
      network: null,
      nodesDataSet: null,
      edgesDataSet: null,
      nodeData: new Map(),
      edges: [],
      loaded: false,
    };
    activateCenterTab(tabId);
    return skillGraphTab;
  }

  function skillGraphNodeView(node, positions = {}) {
    const position = positions[node.id];
    const nodeKind = skillGraphNodeKindFor(node.entry_type, node.metadata?.skill_level);
    return {
      id: node.id,
      label: node.label,
      title: `${node.title}\n${nodeKind.label.toLowerCase()}${node.enabled === false ? "\ndisabled" : ""}`,
      color: skillGraphNodeColor(
        node.entry_type,
        node.enabled,
        node.metadata?.skill_level,
        node.virtual,
      ),
      font: {
        color: node.virtual
          ? (state.theme === "light" ? "rgba(153, 27, 27, 0.62)" : "rgba(254, 202, 202, 0.66)")
          : node.enabled === false
          ? (state.theme === "light" ? "rgba(19, 32, 51, 0.42)" : "rgba(231, 237, 247, 0.42)")
          : (state.theme === "light" ? "#132033" : "#e7edf7"),
        size: 13,
        face: "Manrope",
      },
      shape: "dot",
      size: nodeKind.value === "basic" || nodeKind.value === "workflow" ? 18 : 14,
      borderWidth: node.virtual ? 2 : node.enabled === false ? 1 : 2,
      shapeProperties: node.virtual
        ? { borderDashes: [6, 5] }
        : { borderDashes: false },
      ...(position ? { x: position.x, y: position.y } : {}),
    };
  }

  function skillGraphEdgeView(edge, nodeData) {
    return {
      id: edge.id,
      from: edge.from,
      to: edge.to,
      arrows: "to",
      color: skillGraphEdgeColor(
        nodeData.get(edge.from)?.enabled === false
          || nodeData.get(edge.to)?.enabled === false
      ),
      title: edge.relation || "related",
      smooth: { type: "dynamic" },
    };
  }

  function syncSkillGraphDataSet(dataSet, items) {
    const nextIds = new Set(items.map((item) => item.id));
    const staleIds = dataSet.getIds().filter((id) => !nextIds.has(id));
    if (staleIds.length) dataSet.remove(staleIds);
    if (items.length) dataSet.update(items);
  }

  async function refreshSkillGraphData({ selectedNodeId = null } = {}) {
    const tab = skillGraphTab;
    if (!tab?.network || !tab.nodesDataSet || !tab.edgesDataSet) {
      await loadSkillGraphTab({ force: true });
      return;
    }
    const selected = selectedNodeId || tab.network.getSelectedNodes()[0] || null;
    tab.status.textContent = "updating";
    tab.status.className = "graph-status status-polling";
    const resp = await fetch("/api/skill-graph/data?limit=500");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const positions = tab.network.getPositions();
    tab.nodeData = new Map((data.nodes || []).map((node) => [node.id, node]));
    tab.edges = data.edges || [];
    tab.network.setOptions({ physics: { enabled: false } });
    syncSkillGraphDataSet(tab.nodesDataSet, (data.nodes || []).map((node) => skillGraphNodeView(node, positions)));
    syncSkillGraphDataSet(tab.edgesDataSet, (data.edges || []).map((edge) => skillGraphEdgeView(edge, tab.nodeData)));
    tab.status.className = "graph-status status-idle";
    tab.status.textContent = `${data.nodes?.length || 0} nodes / ${data.edges?.length || 0} edges`;
    if (selected && tab.nodeData.has(selected)) {
      tab.network.selectNodes([selected]);
      renderSkillGraphDetail(tab.nodeData.get(selected));
    } else {
      renderSkillGraphDetail(null);
    }
  }

  async function loadSkillGraphTab({ force = false } = {}) {
    const tab = ensureSkillGraphTab();
    if (tab.loaded && !force) return;
    tab.status.textContent = "loading";
    tab.status.className = "graph-status status-polling";
    tab.loaded = false;
    tab.network?.destroy();
    tab.network = null;
    tab.nodesDataSet = null;
    tab.edgesDataSet = null;
    renderSkillGraphDetail(null);
    tab.canvas.innerHTML = '<div class="skill-graph-loading">Loading graph...</div>';

    try {
      const resp = await fetch("/api/skill-graph/data?limit=500");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      tab.nodeData = new Map((data.nodes || []).map((node) => [node.id, node]));
      tab.edges = data.edges || [];

      const nodes = new DataSet((data.nodes || []).map(skillGraphNodeView));
      const edges = new DataSet((data.edges || []).map((edge) => skillGraphEdgeView(edge, tab.nodeData)));
      tab.nodesDataSet = nodes;
      tab.edgesDataSet = edges;

      tab.canvas.innerHTML = "";
      tab.network?.destroy();
      tab.network = new Network(tab.canvas, { nodes, edges }, {
        autoResize: true,
        physics: {
          enabled: true,
          stabilization: { iterations: 160 },
          barnesHut: { gravitationalConstant: -5600, springLength: 120, springConstant: 0.045 },
        },
        interaction: { hover: true, tooltipDelay: 180, navigationButtons: false, keyboard: false },
        nodes: { borderWidth: 2 },
        edges: { width: 1.6 },
      });
      tab.network.on("selectNode", (params) => {
        renderSkillGraphDetail(tab.nodeData.get(params.nodes[0]));
      });
      tab.network.on("deselectNode", () => renderSkillGraphDetail(null));
      tab.network.once("stabilizationIterationsDone", () => {
        tab.network.fit({ animation: false });
      });
      tab.loaded = true;
      tab.status.className = "graph-status status-idle";
      tab.status.textContent = `${data.nodes?.length || 0} nodes / ${data.edges?.length || 0} edges`;
    } catch (err) {
      tab.status.className = "graph-status status-idle";
      tab.status.textContent = "failed";
      tab.canvas.innerHTML = "";
      const error = document.createElement("div");
      error.className = "skill-graph-loading";
      error.textContent = `Failed to load graph: ${String(err.message || err)}`;
      tab.canvas.appendChild(error);
    }
  }


  function activate(tabId) {
    if (tabId !== "skill-graph" || !skillGraphTab?.network) return;
    requestAnimationFrame(() => {
      try {
        skillGraphTab.network.fit({ animation: false });
      } catch (_) {}
    });
  }

  function close(tabId) {
    if (tabId !== "skill-graph" || !skillGraphTab) return false;
    skillGraphTab.network?.destroy();
    skillGraphTab.button.remove();
    skillGraphTab.panel.remove();
    skillGraphTab = null;
    return true;
  }

  return { open: loadSkillGraphTab, activate, close };
}
