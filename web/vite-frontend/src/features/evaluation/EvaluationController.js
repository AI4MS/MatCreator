export function createEvaluationController({
  state,
  activateCenterTab,
  switchSession,
  removeOverlayWithMotion,
  document: documentRef = globalThis.document,
  fetch: fetchImpl = globalThis.fetch?.bind(globalThis),
  setInterval: setIntervalImpl = globalThis.setInterval?.bind(globalThis),
  clearInterval: clearIntervalImpl = globalThis.clearInterval?.bind(globalThis),
  FormData: FormDataCtor = globalThis.FormData,
  URLSearchParams: URLSearchParamsCtor = globalThis.URLSearchParams,
}) {
  const appModeToggle = documentRef.getElementById("app-mode-toggle");
  // Keep the legacy references for embedders that still render the old
  // two-button mode switch.
  const workspaceModeBtn = documentRef.getElementById("workspace-mode-btn");
  const evaluationModeBtn = documentRef.getElementById("evaluation-mode-btn");
  const evaluationPane = documentRef.getElementById("evaluation-pane");
  const evaluationTab = documentRef.getElementById("tab-evaluation");
  const evaluationTabPanel = documentRef.getElementById("evaluation-tab-panel");
  const evaluationQuestionList = documentRef.getElementById("evaluation-question-list");
  const evaluationSelectionCount = documentRef.getElementById("evaluation-selection-count");
  const evaluationStatus = documentRef.getElementById("evaluation-status");
  const evaluationCampaignSummary = documentRef.getElementById("evaluation-campaign-summary");
  const evaluationCampaignList = documentRef.getElementById("evaluation-campaign-list");
  const evaluationLiveFeed = documentRef.getElementById("evaluation-live-feed");
  let evaluationPoll = null;

  function setEvaluationStatus(message = "", isError = false) {
    if (!evaluationStatus) return;
    evaluationStatus.textContent = message;
    evaluationStatus.classList.toggle("is-error", isError);
  }

  function questionSetById(setId) {
    return state.evaluationQuestionSets.find((questionSet) => questionSet.set_id === setId) || null;
  }

  function questionTemplateById(templateId) {
    return state.evaluationQuestionTemplates.find((template) => template.template_id === templateId) || null;
  }

  function renderEvaluationQuestionTemplates() {
    const select = documentRef.getElementById("evaluation-question-template-select");
    const edit = documentRef.getElementById("evaluation-template-edit");
    const remove = documentRef.getElementById("evaluation-template-delete");
    if (!select) return;
    select.innerHTML = "";
    for (const template of state.evaluationQuestionTemplates) {
      const option = documentRef.createElement("option");
      option.value = template.template_id;
      option.textContent = `${template.name}${template.is_default ? " (default)" : ""}`;
      select.appendChild(option);
    }
    if (!questionTemplateById(state.activeEvaluationQuestionTemplateId)) {
      state.activeEvaluationQuestionTemplateId = state.evaluationQuestionTemplates[0]?.template_id || "";
    }
    select.value = state.activeEvaluationQuestionTemplateId;
    const active = questionTemplateById(state.activeEvaluationQuestionTemplateId);
    if (edit) edit.disabled = !active || active.is_default;
    if (remove) remove.disabled = !active || active.is_default;
  }

  async function loadEvaluationQuestionTemplates() {
    if (!state.userId) return;
    try {
      const response = await fetchImpl(
        `/api/evaluation-question-templates?user_id=${encodeURIComponent(state.userId)}`,
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      state.evaluationQuestionTemplates = data.templates || [];
      renderEvaluationQuestionTemplates();
    } catch (error) {
      setEvaluationStatus(`Could not load question templates: ${error.message}`, true);
    }
  }

  async function loadEvaluationQuestionGenerators(owner = state.userId) {
    try {
      const query = owner ? `?user_id=${encodeURIComponent(owner)}` : "";
      const response = await fetchImpl(`/api/session-question-generators${query}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      state.evaluationQuestionGenerators = data.generators || [];
      if (!state.evaluationQuestionGenerators.some((item) => item.generator_id === state.activeEvaluationQuestionGeneratorId)) {
        state.activeEvaluationQuestionGeneratorId = data.selected_generator_id
          || state.evaluationQuestionGenerators[0]?.generator_id || "";
      }
      return state.evaluationQuestionGenerators;
    } catch (error) {
      setEvaluationStatus(`Could not load question generators: ${error.message}`, true);
      return [];
    }
  }

  async function fetchEvaluationQuestionTemplate(templateId) {
    const response = await fetchImpl(
      `/api/evaluation-question-templates/${encodeURIComponent(templateId)}?user_id=${encodeURIComponent(state.userId)}`,
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function showEvaluationQuestionTemplateModal({ templateId = "", template = {}, copy = false } = {}) {
    documentRef.querySelector(".evaluation-template-overlay")?.remove();
    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay evaluation-template-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    const header = documentRef.createElement("header");
    header.className = "evaluation-draft-header";
    const heading = documentRef.createElement("h2");
    heading.textContent = templateId && !copy ? "Edit question template" : "New question template";
    const close = documentRef.createElement("button");
    close.className = "ghost";
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => void removeOverlayWithMotion(overlay));
    header.append(heading, close);
    const json = documentRef.createElement("textarea");
    json.className = "evaluation-draft-yaml";
    json.value = JSON.stringify(template, null, 2);
    json.setAttribute("aria-label", "Question template JSON");
    json.spellcheck = false;
    const upload = documentRef.createElement("input");
    upload.type = "file";
    upload.accept = "application/json,.json";
    upload.addEventListener("change", async () => {
      const file = upload.files?.[0];
      if (!file) return;
      try {
        json.value = JSON.stringify(JSON.parse(await file.text()), null, 2);
      } catch (_error) {
        status.textContent = "The uploaded file must contain valid JSON.";
        status.className = "evaluation-draft-action-status is-error";
      }
    });
    const actions = documentRef.createElement("div");
    actions.className = "evaluation-draft-actions";
    const save = documentRef.createElement("button");
    save.className = "evaluation-draft-export";
    save.type = "button";
    save.textContent = templateId && !copy ? "Save template" : "Create template";
    const status = documentRef.createElement("p");
    status.className = "evaluation-draft-action-status";
    save.addEventListener("click", async () => {
      let parsed;
      try {
        parsed = JSON.parse(json.value);
      } catch (_error) {
        status.textContent = "Template JSON is invalid.";
        status.className = "evaluation-draft-action-status is-error";
        return;
      }
      if (!parsed.name?.trim()) {
        status.textContent = "Template JSON needs a non-empty name.";
        status.className = "evaluation-draft-action-status is-error";
        return;
      }
      save.disabled = true;
      try {
        const method = templateId && !copy ? "PUT" : "POST";
        const path = method === "PUT"
          ? `/api/evaluation-question-templates/${encodeURIComponent(templateId)}`
          : "/api/evaluation-question-templates";
        const response = await fetchImpl(`${path}?user_id=${encodeURIComponent(state.userId)}`, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ template: parsed }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        state.activeEvaluationQuestionTemplateId = data.template_id;
        await loadEvaluationQuestionTemplates();
        setEvaluationStatus("Question template saved");
        await removeOverlayWithMotion(overlay);
      } catch (error) {
        status.textContent = error.message;
        status.className = "evaluation-draft-action-status is-error";
        save.disabled = false;
      }
    });
    actions.append(save);
    card.append(header, upload, json, actions, status);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
    json.focus();
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) void removeOverlayWithMotion(overlay);
    });
  }

  async function openEvaluationQuestionTemplate(templateId, copy = false) {
    try {
      const data = await fetchEvaluationQuestionTemplate(templateId);
      showEvaluationQuestionTemplateModal({ templateId, template: data.template, copy });
    } catch (error) {
      setEvaluationStatus(`Could not open question template: ${error.message}`, true);
    }
  }

  async function deleteEvaluationQuestionTemplate() {
    const template = questionTemplateById(state.activeEvaluationQuestionTemplateId);
    if (!template || template.is_default) return;
    try {
      const response = await fetchImpl(
        `/api/evaluation-question-templates/${encodeURIComponent(template.template_id)}?user_id=${encodeURIComponent(state.userId)}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
      state.activeEvaluationQuestionTemplateId = "default";
      await loadEvaluationQuestionTemplates();
      setEvaluationStatus("Question template deleted");
    } catch (error) {
      setEvaluationStatus(`Could not delete question template: ${error.message}`, true);
    }
  }

  function renderEvaluationQuestionSets() {
    const select = documentRef.getElementById("evaluation-question-set-select");
    const updateButton = documentRef.getElementById("evaluation-update-question-set");
    const deleteButton = documentRef.getElementById("evaluation-delete-question-set");
    if (!select) return;
    const selectedId = state.activeEvaluationQuestionSetId;
    select.innerHTML = '<option value="">Load a saved set</option>';
    for (const questionSet of state.evaluationQuestionSets) {
      const option = documentRef.createElement("option");
      option.value = questionSet.set_id;
      option.textContent = `${questionSet.name} (${questionSet.visibility})`;
      select.appendChild(option);
    }
    select.value = selectedId;
    const active = questionSetById(selectedId);
    const owned = active?.owner_id === state.userId;
    updateButton.disabled = !owned;
    deleteButton.disabled = !owned;
  }

  function renderEvaluationGeneratedQuestions() {
    const list = documentRef.getElementById("evaluation-generated-question-list");
    if (!list) return;
    list.innerHTML = "";
    if (!state.evaluationGeneratedQuestions.length) {
      const empty = documentRef.createElement("p");
      empty.className = "empty";
      empty.textContent = "No generated questions yet";
      list.appendChild(empty);
      return;
    }
    for (const draft of state.evaluationGeneratedQuestions) {
      const row = documentRef.createElement("button");
      row.type = "button";
      row.className = "evaluation-generated-question";
      row.title = "Browse generated question YAML";
      const heading = documentRef.createElement("span");
      heading.className = "evaluation-generated-question-heading";
      const questionId = documentRef.createElement("strong");
      questionId.textContent = draft.question_id || "Untitled question";
      const status = documentRef.createElement("span");
      status.className = `evaluation-generated-question-status status-${draft.status}`;
      status.textContent = draft.status.replaceAll("_", " ");
      heading.append(questionId, status);
      const meta = documentRef.createElement("span");
      meta.className = "evaluation-generated-question-meta";
      meta.textContent = `Session ${draft.source_session_id || "unknown"} · ${draft.refinement_count || 0} refinements`;
      row.append(heading, meta);
      row.addEventListener("click", async () => {
        try {
          const response = await fetchImpl(
            `/api/evaluation-question-drafts/${encodeURIComponent(draft.draft_id)}?user_id=${encodeURIComponent(state.userId)}`,
          );
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
          showEvaluationQuestionDraftModal(data);
        } catch (error) {
          setEvaluationStatus(`Could not open generated question: ${error.message}`, true);
        }
      });
      list.appendChild(row);
    }
  }

  async function loadEvaluationGeneratedQuestions() {
    if (!state.userId) return;
    try {
      const response = await fetchImpl(
        `/api/evaluation-question-drafts?user_id=${encodeURIComponent(state.userId)}`,
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      state.evaluationGeneratedQuestions = data.drafts || [];
      renderEvaluationGeneratedQuestions();
    } catch (error) {
      setEvaluationStatus(`Could not load generated questions: ${error.message}`, true);
    }
  }

  async function loadEvaluationQuestionSets() {
    if (!state.userId) return;
    try {
      const response = await fetchImpl(`/api/evaluations/question-sets?user_id=${encodeURIComponent(state.userId)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(data, `HTTP ${response.status}`));
      state.evaluationQuestionSets = data.question_sets || [];
      if (!questionSetById(state.activeEvaluationQuestionSetId)) state.activeEvaluationQuestionSetId = "";
      renderEvaluationQuestionSets();
    } catch (error) {
      setEvaluationStatus(`Could not load question sets: ${error.message}`, true);
    }
  }

  function loadSelectedQuestionSet(setId) {
    state.activeEvaluationQuestionSetId = setId;
    const questionSet = questionSetById(setId);
    if (questionSet) {
      state.selectedEvaluationQuestions = new Set(questionSet.question_ids || []);
      documentRef.getElementById("evaluation-question-set-name").value = questionSet.name;
      documentRef.getElementById("evaluation-question-set-visibility").value = questionSet.visibility;
      renderEvaluationQuestions();
    }
    renderEvaluationQuestionSets();
  }

  async function saveEvaluationQuestionSet(update = false) {
    const active = questionSetById(state.activeEvaluationQuestionSetId);
    if (update && active?.owner_id !== state.userId) return;
    const name = documentRef.getElementById("evaluation-question-set-name")?.value.trim();
    const visibility = documentRef.getElementById("evaluation-question-set-visibility")?.value || "private";
    if (!name || !state.selectedEvaluationQuestions.size) {
      setEvaluationStatus("A set name and at least one selected question are required", true);
      return;
    }
    const path = update ? `/api/evaluations/question-sets/${encodeURIComponent(active.set_id)}` : "/api/evaluations/question-sets";
    try {
      const response = await fetchImpl(`${path}?user_id=${encodeURIComponent(state.userId)}`, {
        method: update ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, visibility, question_ids: [...state.selectedEvaluationQuestions] }),
      });
      const questionSet = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(questionSet, `HTTP ${response.status}`));
      state.activeEvaluationQuestionSetId = questionSet.set_id;
      await loadEvaluationQuestionSets();
      setEvaluationStatus(update ? "Question set updated" : "Question set saved");
    } catch (error) {
      setEvaluationStatus(`Could not save question set: ${error.message}`, true);
    }
  }

  async function deleteEvaluationQuestionSet() {
    const active = questionSetById(state.activeEvaluationQuestionSetId);
    if (!active || active.owner_id !== state.userId) return;
    try {
      const response = await fetchImpl(
        `/api/evaluations/question-sets/${encodeURIComponent(active.set_id)}?user_id=${encodeURIComponent(state.userId)}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(apiErrorMessage(await response.json(), `HTTP ${response.status}`));
      state.activeEvaluationQuestionSetId = "";
      await loadEvaluationQuestionSets();
      setEvaluationStatus("Question set deleted");
    } catch (error) {
      setEvaluationStatus(`Could not delete question set: ${error.message}`, true);
    }
  }

  function apiErrorMessage(data, fallback) {
    const detail = data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "request";
        return `${field || "request"}: ${item?.msg || "invalid value"}`;
      }).join("; ");
    }
    return fallback;
  }

  function renderEvaluationQuestions() {
    if (!evaluationQuestionList) return;
    evaluationQuestionList.innerHTML = "";
    const selectedOnly = documentRef.getElementById("evaluation-selected-only")?.checked;
    const questions = selectedOnly
      ? state.evaluationCatalog.filter((question) => state.selectedEvaluationQuestions.has(question.id))
      : state.evaluationCatalog;
    if (!questions.length) {
      const empty = documentRef.createElement("p");
      empty.className = "empty";
      empty.textContent = "No benchmark questions loaded";
      evaluationQuestionList.appendChild(empty);
    }
    for (const question of questions) {
      const row = documentRef.createElement("label");
      row.className = "evaluation-question-row";
      const checkbox = documentRef.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedEvaluationQuestions.has(question.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selectedEvaluationQuestions.add(question.id);
        else state.selectedEvaluationQuestions.delete(question.id);
        renderEvaluationQuestions();
      });
      const details = documentRef.createElement("span");
      details.className = "evaluation-question-details";
      const title = documentRef.createElement("strong");
      title.textContent = question.id || "Unnamed question";
      const meta = documentRef.createElement("span");
      meta.textContent = [question.domain, question.capability, question.intent].filter(Boolean).join(" · ") || "No metadata";
      details.append(title, meta);
      row.append(checkbox, details);
      evaluationQuestionList.appendChild(row);
    }
    if (evaluationSelectionCount) {
      const count = state.selectedEvaluationQuestions.size;
      const catalogCount = Number.isInteger(state.evaluationCatalogTotal)
        ? `${state.evaluationCatalogTotal} total questions`
        : `${state.evaluationCatalog.length} loaded questions`;
      evaluationSelectionCount.textContent = `${count} selected · ${catalogCount}`;
    }
  }

  function populateEvaluationSelect(id, values, placeholder) {
    const select = documentRef.getElementById(id);
    if (!select) return;
    const current = select.value;
    select.innerHTML = "";
    const empty = documentRef.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.appendChild(empty);
    for (const value of [...new Set(values.filter(Boolean))].sort()) {
      const option = documentRef.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    }
    select.value = current;
  }

  async function loadEvaluationCatalog() {
    const params = new URLSearchParamsCtor({ limit: "500" });
    const search = documentRef.getElementById("evaluation-search")?.value.trim();
    const domain = documentRef.getElementById("evaluation-domain")?.value.trim();
    const capability = documentRef.getElementById("evaluation-capability")?.value.trim();
    const taskType = documentRef.getElementById("evaluation-task-type")?.value.trim();
    if (search) params.set("q", search);
    if (domain) params.set("domain", domain);
    if (capability) params.set("capability", capability);
    if (taskType) params.set("task_type", taskType);
    setEvaluationStatus("Loading catalog");
    try {
      if (state.userId) params.set("user_id", state.userId);
      const response = await fetchImpl(`/api/evaluations/catalog?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      state.evaluationCatalog = data.questions || [];
      state.evaluationCatalogTotal = Number.isInteger(data.total) ? data.total : null;
      populateEvaluationSelect("evaluation-domain", data.facets?.domain || state.evaluationCatalog.map((item) => item.domain), "All domains");
      populateEvaluationSelect(
        "evaluation-capability",
        data.facets?.capability || state.evaluationCatalog.flatMap((item) => Array.isArray(item.capability) ? item.capability : [item.capability]),
        "All capabilities",
      );
      populateEvaluationSelect(
        "evaluation-task-type",
        data.facets?.task_type || state.evaluationCatalog.map((item) => item.task_type),
        "All task types",
      );
      renderEvaluationQuestions();
      setEvaluationStatus("");
    } catch (error) {
      setEvaluationStatus(`Catalog unavailable: ${error.message}`, true);
    }
  }

  function renderEvaluationCampaign(campaign) {
    if (!evaluationCampaignSummary) return;
    if (!campaign) {
      evaluationCampaignSummary.textContent = "Select questions to create an evaluation campaign.";
      return;
    }
    const attempts = campaign.attempts || [];
    const counts = attempts.reduce((result, attempt) => {
      result[attempt.status] = (result[attempt.status] || 0) + 1;
      return result;
    }, {});
    const completed = counts.completed || 0;
    const score = attempts
      .map((attempt) => Number(attempt.result?.weighted_score))
      .filter(Number.isFinite);
    const average = score.length ? (score.reduce((sum, value) => sum + value, 0) / score.length).toFixed(3) : "Pending";
    evaluationCampaignSummary.innerHTML = "";
    const status = documentRef.createElement("strong");
    status.textContent = `${campaign.status} · ${completed}/${attempts.length} completed`;
    const result = documentRef.createElement("span");
    result.textContent = `Score: ${average}`;
    evaluationCampaignSummary.append(status, result);
    if (["starting", "active", "cancelling"].includes(campaign.status)) {
      const cancelButton = documentRef.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "evaluation-cancel-btn";
      cancelButton.textContent = campaign.status === "cancelling" ? "Cancelling" : "Stop evaluation";
      cancelButton.disabled = campaign.status === "cancelling";
      cancelButton.addEventListener("click", () => void cancelEvaluationCampaign(campaign));
      evaluationCampaignSummary.appendChild(cancelButton);
    }
    for (const attempt of attempts) {
      const row = documentRef.createElement("div");
      row.className = "evaluation-attempt-row";
      const label = documentRef.createElement("span");
      label.textContent = `${attempt.question_id} · ${attempt.status}`;
      const openButton = documentRef.createElement("button");
      openButton.type = "button";
      openButton.className = "evaluation-attempt-open";
      openButton.textContent = "Open session";
      openButton.disabled = !attempt.runtime_session_id;
      openButton.addEventListener("click", () => openEvaluationAttempt(campaign, attempt));
      row.append(label, openButton);
      evaluationCampaignSummary.appendChild(row);
      const task = attempt.task_payload || {};
      if (task.prompt) {
        const details = documentRef.createElement("details");
        details.className = "evaluation-task-details";
        const summary = documentRef.createElement("summary");
        summary.textContent = "Question sent to agent";
        const prompt = documentRef.createElement("pre");
        prompt.textContent = task.prompt;
        details.append(summary, prompt);
        if (task.data_files?.length) {
          const files = documentRef.createElement("p");
          files.className = "evaluation-task-files";
          files.textContent = `Input files: ${task.data_files.join(", ")}`;
          details.appendChild(files);
        }
        evaluationCampaignSummary.appendChild(details);
      }
    }
    for (const attempt of attempts.filter((item) => item.error)) {
      const error = documentRef.createElement("span");
      error.className = "evaluation-attempt-error";
      error.textContent = `${attempt.question_id}: ${attempt.error}`;
      evaluationCampaignSummary.appendChild(error);
    }
  }

  async function loadEvaluationAttemptEvents(campaign) {
    if (!evaluationLiveFeed) return;
    const activeAttempts = (campaign?.attempts || []).filter((item) => ["runtime_starting", "running"].includes(item.status));
    evaluationLiveFeed.textContent = activeAttempts.length
      ? `${activeAttempts.length} agent session${activeAttempts.length === 1 ? "" : "s"} running. Open an attempt to view its standard session stream.`
      : "";
  }

  function openEvaluationAttempt(campaign, attempt) {
    if (!attempt.runtime_session_id) return;
    setApplicationMode("workspace");
    void switchSession(attempt.runtime_session_id, campaign.owner_id || state.userId);
  }

  async function loadEvaluationCampaign(campaignId) {
    if (!state.userId || !campaignId) return;
    try {
      const response = await fetchImpl(`/api/evaluations/campaigns/${encodeURIComponent(campaignId)}?user_id=${encodeURIComponent(state.userId)}`);
      const campaign = await response.json();
      if (!response.ok) throw new Error(campaign.detail || `HTTP ${response.status}`);
      state.activeEvaluationCampaign = campaign;
      renderEvaluationCampaign(campaign);
      await loadEvaluationAttemptEvents(campaign);
      setEvaluationStatus("");
    } catch (error) {
      setEvaluationStatus(`Could not load campaign: ${error.message}`, true);
    }
  }

  async function loadEvaluationCampaigns() {
    if (!state.userId || !evaluationCampaignList) return;
    try {
      const response = await fetchImpl(`/api/evaluations/campaigns?user_id=${encodeURIComponent(state.userId)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      evaluationCampaignList.innerHTML = "";
      for (const campaign of data.campaigns || []) {
        const button = documentRef.createElement("button");
        button.type = "button";
        button.className = "evaluation-campaign-row";
        button.textContent = `${campaign.model_name} · ${campaign.status}`;
        const isActive = campaign.campaign_id === state.activeEvaluationCampaign?.campaign_id;
        button.classList.toggle("is-active", isActive);
        if (isActive) button.setAttribute("aria-current", "true");
        button.addEventListener("click", () => void loadEvaluationCampaign(campaign.campaign_id));
        evaluationCampaignList.appendChild(button);
      }
      if (!evaluationCampaignList.childElementCount) {
        evaluationCampaignList.textContent = "No campaigns yet";
      }
      setEvaluationStatus("");
    } catch (error) {
      setEvaluationStatus(`Could not load campaigns: ${error.message}`, true);
    }
  }

  async function cancelEvaluationCampaign(campaign) {
    if (!state.userId || !campaign?.campaign_id) return;
    setEvaluationStatus("Cancelling evaluation");
    try {
      const response = await fetchImpl(
        `/api/evaluations/campaigns/${encodeURIComponent(campaign.campaign_id)}/cancel?user_id=${encodeURIComponent(state.userId)}`,
        { method: "POST" },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(data, `HTTP ${response.status}`));
      state.activeEvaluationCampaign = data;
      renderEvaluationCampaign(data);
      setEvaluationStatus("Evaluation cancellation requested");
      await loadEvaluationCampaigns();
    } catch (error) {
      setEvaluationStatus(`Could not cancel evaluation: ${error.message}`, true);
    }
  }

  async function createAndStartEvaluation() {
    if (!state.userId) {
      setEvaluationStatus("Sign in before starting an evaluation", true);
      return;
    }
    const questionIds = [...state.selectedEvaluationQuestions];
    if (!questionIds.length) {
      setEvaluationStatus("Select at least one question", true);
      return;
    }
    if (questionIds.some((questionId) => typeof questionId !== "string" || !questionId.trim())) {
      setEvaluationStatus("Selected question data is invalid. Refresh the catalog and select again.", true);
      return;
    }
    const valueOf = (id, label) => {
      const value = Number(documentRef.getElementById(id)?.value);
      if (!Number.isInteger(value) || value < 1) {
        throw new Error(`${label} must be a positive integer`);
      }
      return value;
    };
    let maxParallelism;
    let maxTurns;
    let timeoutSeconds;
    try {
      maxParallelism = valueOf("evaluation-parallelism", "Parallelism");
      maxTurns = valueOf("evaluation-turns", "Max turns");
      timeoutSeconds = valueOf("evaluation-timeout", "Timeout");
    } catch (error) {
      setEvaluationStatus(error.message, true);
      return;
    }
    const body = {
      model_name: documentRef.getElementById("evaluation-model")?.value.trim() || "matcreator",
      question_ids: questionIds,
      max_parallelism: maxParallelism,
      max_turns: maxTurns,
      timeout_seconds: timeoutSeconds,
      flash: false,
    };
    setEvaluationStatus("Creating campaign");
    try {
      const createResponse = await fetchImpl(`/api/evaluations/campaigns?user_id=${encodeURIComponent(state.userId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const campaign = await createResponse.json();
      if (!createResponse.ok) throw new Error(apiErrorMessage(campaign, `HTTP ${createResponse.status}`));
      const startResponse = await fetchImpl(`/api/evaluations/campaigns/${encodeURIComponent(campaign.campaign_id)}/start?user_id=${encodeURIComponent(state.userId)}`, { method: "POST" });
      const started = await startResponse.json();
      if (!startResponse.ok) throw new Error(apiErrorMessage(started, `HTTP ${startResponse.status}`));
      state.activeEvaluationCampaign = { ...started, attempts: [] };
      renderEvaluationCampaign(state.activeEvaluationCampaign);
      await loadEvaluationCampaigns();
      setEvaluationStatus("Evaluation queued");
    } catch (error) {
      setEvaluationStatus(`Could not start evaluation: ${error.message}`, true);
    }
  }

  function setApplicationMode(mode) {
    const evaluation = mode === "evaluation";
    state.appMode = evaluation ? "evaluation" : "workspace";
    if (appModeToggle) {
      const nextMode = evaluation ? "Workspace" : "Evaluation";
      appModeToggle.dataset.appMode = state.appMode;
      appModeToggle.title = `Switch to ${nextMode} mode`;
      appModeToggle.setAttribute("aria-label", `Switch to ${nextMode} mode`);
      appModeToggle.setAttribute("aria-pressed", String(evaluation));
    }
    workspaceModeBtn?.classList.toggle("active", !evaluation);
    evaluationModeBtn?.classList.toggle("active", evaluation);
    workspaceModeBtn?.setAttribute("aria-pressed", String(!evaluation));
    evaluationModeBtn?.setAttribute("aria-pressed", String(evaluation));
    evaluationPane?.classList.toggle("hidden", !evaluation);
    evaluationTab?.classList.toggle("hidden", !evaluation);
    evaluationTabPanel?.classList.toggle("hidden", !evaluation);
    documentRef.querySelectorAll(".sessions-pane, .remote-jobs-slot, .file-explorer-col").forEach((element) => {
      element.classList.toggle("hidden", evaluation);
    });
    if (evaluation) {
      activateCenterTab("evaluation");
      void loadEvaluationCatalog();
      void loadEvaluationCampaigns();
      void loadEvaluationQuestionSets();
      void loadEvaluationGeneratedQuestions();
      void loadEvaluationQuestionTemplates();
      void loadEvaluationQuestionGenerators();
      if (!evaluationPoll) {
        evaluationPoll = setIntervalImpl(() => {
          if (state.activeEvaluationCampaign?.campaign_id && ["draft", "starting", "active", "cancelling"].includes(state.activeEvaluationCampaign.status)) {
            void loadEvaluationCampaign(state.activeEvaluationCampaign.campaign_id);
          }
          void loadEvaluationCampaigns();
        }, 2000);
      }
    } else {
      activateCenterTab("chat");
      if (evaluationPoll) {
        clearIntervalImpl(evaluationPoll);
        evaluationPoll = null;
      }
    }
  }

  appModeToggle?.addEventListener("click", () => {
    setApplicationMode(state.appMode === "evaluation" ? "workspace" : "evaluation");
  });
  workspaceModeBtn?.addEventListener("click", () => setApplicationMode("workspace"));
  evaluationModeBtn?.addEventListener("click", () => setApplicationMode("evaluation"));
  documentRef.getElementById("evaluation-refresh-catalog")?.addEventListener("click", () => void loadEvaluationCatalog());
  documentRef.getElementById("evaluation-refresh-campaigns")?.addEventListener("click", () => void loadEvaluationCampaigns());
  documentRef.getElementById("evaluation-refresh-question-sets")?.addEventListener("click", () => void loadEvaluationQuestionSets());
  documentRef.getElementById("evaluation-refresh-generated-questions")?.addEventListener("click", () => void loadEvaluationGeneratedQuestions());
  documentRef.getElementById("evaluation-refresh-question-templates")?.addEventListener("click", () => void loadEvaluationQuestionTemplates());
  documentRef.getElementById("evaluation-create-start")?.addEventListener("click", () => void createAndStartEvaluation());
  documentRef.getElementById("evaluation-question-set-select")?.addEventListener("change", (event) => loadSelectedQuestionSet(event.target.value));
  documentRef.getElementById("evaluation-save-question-set")?.addEventListener("click", () => void saveEvaluationQuestionSet());
  documentRef.getElementById("evaluation-update-question-set")?.addEventListener("click", () => void saveEvaluationQuestionSet(true));
  documentRef.getElementById("evaluation-delete-question-set")?.addEventListener("click", () => void deleteEvaluationQuestionSet());
  documentRef.getElementById("evaluation-clear-selection")?.addEventListener("click", () => {
    state.selectedEvaluationQuestions.clear();
    state.activeEvaluationQuestionSetId = "";
    renderEvaluationQuestions();
    renderEvaluationQuestionSets();
  });
  ["evaluation-search", "evaluation-domain", "evaluation-capability", "evaluation-task-type"].forEach((id) => {
    documentRef.getElementById(id)?.addEventListener("change", () => void loadEvaluationCatalog());
  });
  documentRef.getElementById("evaluation-selected-only")?.addEventListener("change", renderEvaluationQuestions);
  documentRef.getElementById("evaluation-question-template-select")?.addEventListener("change", (event) => {
    state.activeEvaluationQuestionTemplateId = event.target.value;
    renderEvaluationQuestionTemplates();
  });
  documentRef.getElementById("evaluation-template-new")?.addEventListener("click", () => {
    showEvaluationQuestionTemplateModal();
  });
  documentRef.getElementById("evaluation-template-copy")?.addEventListener("click", () => {
    void openEvaluationQuestionTemplate(state.activeEvaluationQuestionTemplateId, true);
  });
  documentRef.getElementById("evaluation-template-edit")?.addEventListener("click", () => {
    void openEvaluationQuestionTemplate(state.activeEvaluationQuestionTemplateId);
  });
  documentRef.getElementById("evaluation-template-delete")?.addEventListener("click", () => {
    void deleteEvaluationQuestionTemplate();
  });

  function showEvaluationQuestionDraftModal(draft, actionMessage = "") {
    const existing = documentRef.querySelector(".evaluation-draft-overlay");
    if (existing) existing.remove();

    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", "Evaluation question draft");

    const header = documentRef.createElement("header");
    header.className = "evaluation-draft-header";
    const heading = documentRef.createElement("div");
    const eyebrow = documentRef.createElement("div");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "Staged benchmark question";
    const title = documentRef.createElement("h2");
    title.textContent = draft.question.id || "Generated question";
    heading.append(eyebrow, title);
    const close = documentRef.createElement("button");
    close.className = "ghost";
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => void removeOverlayWithMotion(overlay));
    header.append(heading, close);

    const isLocked = draft.status === "exported" || draft.status === "published";
    const notice = documentRef.createElement("p");
    notice.className = "evaluation-draft-notice";
    const statusNotices = {
      ready_for_review: "This draft is saved and ready for review. Approve it when the YAML is final.",
      invalid: "This saved draft has validation issues. Edit it manually or refine it with MatCreator feedback.",
      approved: "This draft is approved and saved. Export it to add it to the configured benchmark bank.",
      exported: "This draft has been exported to the configured benchmark bank and is now read-only.",
      published: "This draft has been published to your custom benchmark bank and is now read-only.",
    };
    notice.textContent = statusNotices[draft.status] || "This question draft is saved for review.";
    const validation = documentRef.createElement("div");
    const isValid = ["ready_for_review", "approved", "exported", "published"].includes(draft.status);
    validation.className = `evaluation-draft-validation ${isValid ? "is-valid" : "is-invalid"}`;
    validation.textContent = isValid
      ? "Schema and executable-verifier checks passed"
      : "Validation issues";
    if (draft.validation_errors?.length) {
      const errors = documentRef.createElement("ul");
      for (const message of draft.validation_errors) {
        const item = documentRef.createElement("li");
        item.textContent = message;
        errors.appendChild(item);
      }
      validation.appendChild(errors);
    }
    const yamlHeading = documentRef.createElement("h3");
    yamlHeading.textContent = "Generated question YAML";
    const yaml = documentRef.createElement("textarea");
    yaml.className = "evaluation-draft-yaml";
    yaml.textContent = draft.question_yaml || "No YAML was returned.";
    yaml.value = draft.question_yaml || "";
    yaml.setAttribute("aria-label", "Generated question YAML");
    yaml.spellcheck = false;
    const actions = documentRef.createElement("div");
    actions.className = "evaluation-draft-actions";
    const instruction = documentRef.createElement("textarea");
    instruction.className = "evaluation-draft-instruction";
    instruction.rows = 2;
    instruction.maxLength = 2000;
    instruction.placeholder = "Optional refinement instruction";
    instruction.setAttribute("aria-label", "Optional refinement instruction");
    instruction.disabled = isLocked;
    const templateSelect = documentRef.createElement("select");
    templateSelect.className = "evaluation-input";
    templateSelect.setAttribute("aria-label", "Refinement question template");
    for (const template of state.evaluationQuestionTemplates) {
      const option = documentRef.createElement("option");
      option.value = template.template_id;
      option.textContent = `${template.name}${template.is_default ? " (default)" : ""}`;
      templateSelect.appendChild(option);
    }
    templateSelect.value = draft.template?.template_id || state.activeEvaluationQuestionTemplateId || "default";
    templateSelect.disabled = isLocked;
    const actionStatus = documentRef.createElement("p");
    actionStatus.className = actionMessage
      ? "evaluation-draft-action-status is-success"
      : "evaluation-draft-action-status";
    actionStatus.setAttribute("role", "status");
    actionStatus.textContent = actionMessage;
    const buttons = [];
    const declaredDataFiles = Array.isArray(draft.question?.data_files) ? draft.question.data_files : [];
    let dataFilesSection = null;
    if (declaredDataFiles.length) {
      dataFilesSection = documentRef.createElement("section");
      dataFilesSection.className = "evaluation-draft-data-files";
      const dataFilesHeading = documentRef.createElement("h3");
      dataFilesHeading.textContent = "Question input files";
      dataFilesSection.appendChild(dataFilesHeading);
      const dataFilesList = documentRef.createElement("ul");
      for (const dataFile of declaredDataFiles) {
        const path = typeof dataFile?.path === "string" ? dataFile.path : "";
        const item = documentRef.createElement("li");
        const label = documentRef.createElement("code");
        label.textContent = path || "Invalid declared path";
        const picker = documentRef.createElement("input");
        picker.type = "file";
        picker.disabled = isLocked || !path;
        picker.setAttribute("aria-label", `Upload ${path}`);
        const upload = documentRef.createElement("button");
        upload.type = "button";
        upload.className = "ghost";
        upload.textContent = "Upload";
        upload.disabled = picker.disabled;
        upload.addEventListener("click", () => void (async () => {
          const selectedFile = picker.files?.[0];
          if (!selectedFile) {
            actionStatus.className = "evaluation-draft-action-status is-error";
            actionStatus.textContent = `Choose a file for ${path}.`;
            actionStatus.focus();
            return;
          }
          picker.disabled = true;
          upload.disabled = true;
          upload.textContent = "Uploading...";
          try {
            const formData = new FormDataCtor();
            formData.append("path", path);
            formData.append("file", selectedFile);
            const response = await fetchImpl(
              `/api/evaluation-question-drafts/${encodeURIComponent(draft.draft_id)}/data-files?user_id=${encodeURIComponent(draft.evidence?.source?.owner_id || state.userId)}`,
              { method: "POST", body: formData },
            );
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            showEvaluationQuestionDraftModal(data, `Staged ${path}.`);
          } catch (error) {
            picker.disabled = false;
            upload.disabled = false;
            upload.textContent = "Upload";
            actionStatus.className = "evaluation-draft-action-status is-error";
            actionStatus.textContent = error.message || `Could not stage ${path}.`;
            actionStatus.focus();
          }
        })());
        item.append(label, picker, upload);
        dataFilesList.appendChild(item);
      }
      dataFilesSection.appendChild(dataFilesList);
    }
    const runDraftAction = async (path, options = {}, activeButton, pendingLabel, successLabel) => {
      actionStatus.className = "evaluation-draft-action-status";
      actionStatus.textContent = "";
      const originalLabel = activeButton.textContent;
      buttons.forEach((button) => { button.disabled = true; });
      instruction.disabled = true;
      card.setAttribute("aria-busy", "true");
      activeButton.textContent = pendingLabel;
      try {
        const response = await fetchImpl(
          `/api/evaluation-question-drafts/${encodeURIComponent(draft.draft_id)}${path}?user_id=${encodeURIComponent(draft.evidence?.source?.owner_id || state.userId)}`,
          options,
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        const savedPath = data.staging_path ? `${data.staging_path}/question.yaml` : "draft storage";
        if (state.appMode === "evaluation") void loadEvaluationGeneratedQuestions();
        showEvaluationQuestionDraftModal(data, `${successLabel} ${savedPath}`);
        return data;
      } catch (error) {
        activeButton.textContent = originalLabel;
        buttons.forEach((button) => { button.disabled = false; });
        save.disabled = isLocked;
        refine.disabled = isLocked;
        approve.disabled = isLocked;
        exportButton.disabled = draft.status !== "approved";
        publishButton.disabled = draft.status !== "approved";
        instruction.disabled = isLocked;
        card.removeAttribute("aria-busy");
        actionStatus.className = "evaluation-draft-action-status is-error";
        actionStatus.textContent = error.message || "The action failed.";
        actionStatus.focus();
        return null;
      }
    };
    const save = documentRef.createElement("button");
    save.type = "button";
    save.className = "ghost";
    save.textContent = "Save YAML";
    save.disabled = isLocked;
    save.addEventListener("click", () => void runDraftAction("", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_yaml: yaml.value }),
    }, save, "Saving...", "Saved to"));
    actions.appendChild(save);
    const refine = documentRef.createElement("button");
    refine.type = "button";
    refine.className = "ghost";
    refine.textContent = "Refine with feedback";
    refine.disabled = isLocked;
    refine.addEventListener("click", () => void (async () => {
      try {
        let current = draft;
        if (yaml.value !== draft.question_yaml) {
          current = await runDraftAction("", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question_yaml: yaml.value }),
          }, refine, "Saving...", "Saved to");
        }
        if (!current) return;
        await runDraftAction("/refine", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction: instruction.value, template_id: templateSelect.value }),
        }, refine, "Refining...", "Refined and saved to");
      } catch (_error) {
        // runDraftAction renders the actionable error in the dialog.
      }
    })());
    actions.appendChild(refine);
    const approve = documentRef.createElement("button");
    approve.type = "button";
    approve.className = "ghost";
    approve.textContent = draft.status === "approved" ? "Approved" : "Approve";
    approve.disabled = isLocked;
    approve.title = draft.status === "invalid"
      ? "Validate this saved YAML and show why it cannot be approved"
      : "Approve this saved YAML";
    approve.addEventListener("click", () => void runDraftAction(
      "/approve", { method: "POST" }, approve, "Approving...", "Approved and saved at",
    ));
    actions.appendChild(approve);
    const exportButton = documentRef.createElement("button");
    exportButton.type = "button";
    exportButton.className = "evaluation-draft-export";
    exportButton.textContent = draft.status === "exported" ? "Exported" : "Export to benchmark bank";
    exportButton.disabled = draft.status !== "approved";
    exportButton.addEventListener("click", () => void runDraftAction(
      "/export", { method: "POST" }, exportButton, "Exporting...", "Exported from",
    ));
    actions.appendChild(exportButton);
    const publishButton = documentRef.createElement("button");
    publishButton.type = "button";
    publishButton.className = "evaluation-draft-publish";
    publishButton.textContent = draft.status === "published" ? "Published" : "Publish to my bank";
    publishButton.title = "Publish this approved question to your own custom benchmark bank";
    publishButton.disabled = draft.status !== "approved";
    publishButton.addEventListener("click", () => void runDraftAction(
      "/publish", { method: "POST" }, publishButton, "Publishing...", "Published from",
    ));
    actions.appendChild(publishButton);
    buttons.push(save, refine, approve, exportButton, publishButton);
    const evidence = documentRef.createElement("div");
    evidence.className = "evaluation-draft-evidence";
    const stepsHeading = documentRef.createElement("h3");
    stepsHeading.textContent = "Observable session steps";
    evidence.appendChild(stepsHeading);
    const stepList = documentRef.createElement("ol");
    for (const step of draft.evidence?.steps || []) {
      const item = documentRef.createElement("li");
      item.textContent = `${step.action}${step.status ? ` [${step.status}]` : ""}${step.summary ? `: ${step.summary}` : ""}`;
      stepList.appendChild(item);
    }
    evidence.appendChild(stepList);
    const artifacts = documentRef.createElement("p");
    artifacts.className = "evaluation-draft-artifacts";
    const artifactCount = draft.evidence?.artifacts?.length || 0;
    artifacts.textContent = `${artifactCount} source artifact${artifactCount === 1 ? "" : "s"} available for review.`;
    evidence.appendChild(artifacts);
    card.append(
      header,
      notice,
      validation,
      yamlHeading,
      yaml,
      ...(dataFilesSection ? [dataFilesSection] : []),
      templateSelect,
      instruction,
      actions,
      actionStatus,
      evidence,
    );
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
    close.focus();
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) void removeOverlayWithMotion(overlay);
    });
  }

  function showEvaluationQuestionDraftError(error) {
    const message = typeof error === "string" ? error : error?.message;
    const diagnostics = typeof error === "object" ? error?.diagnostics : null;
    const existing = documentRef.querySelector(".evaluation-draft-overlay");
    if (existing) existing.remove();

    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card";
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", "Question generation failed");
    const heading = documentRef.createElement("h2");
    heading.textContent = "Question generation failed";
    const detail = documentRef.createElement("p");
    detail.className = "evaluation-draft-notice";
    detail.textContent = message || "The server did not return a reason.";
    card.append(heading, detail);
    if (diagnostics && typeof diagnostics === "object") {
      const diagnosticsPanel = documentRef.createElement("details");
      diagnosticsPanel.className = "evaluation-generation-diagnostics";
      diagnosticsPanel.open = true;
      const summary = documentRef.createElement("summary");
      summary.textContent = "Generation details";
      const metadata = documentRef.createElement("p");
      metadata.textContent = [
        diagnostics.generator && `Generator: ${diagnostics.generator}`,
        diagnostics.stage && `Stage: ${diagnostics.stage}`,
        Number.isFinite(diagnostics.response_length) && `Response: ${diagnostics.response_length} characters`,
      ].filter(Boolean).join(" · ");
      const expected = documentRef.createElement("p");
      expected.textContent = diagnostics.expected_format || "";
      const preview = documentRef.createElement("pre");
      preview.className = "evaluation-generation-response-preview";
      preview.textContent = diagnostics.response_preview || "No response preview is available.";
      diagnosticsPanel.append(summary, metadata, expected, preview);
      card.appendChild(diagnosticsPanel);
    }
    const close = documentRef.createElement("button");
    close.className = "ghost";
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => void removeOverlayWithMotion(overlay));
    card.appendChild(close);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
    close.focus();
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) void removeOverlayWithMotion(overlay);
    });
  }

  function showNoEvaluationQuestionExtracted(result) {
    const existing = documentRef.querySelector(".evaluation-draft-overlay");
    if (existing) existing.remove();

    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", "No benchmark question extracted");
    const heading = documentRef.createElement("h2");
    heading.textContent = "No benchmark question extracted";
    const detail = documentRef.createElement("p");
    detail.className = "evaluation-draft-notice";
    detail.textContent = result.reason || "The generator found no grounded benchmark task in this session.";
    const close = documentRef.createElement("button");
    close.className = "ghost";
    close.type = "button";
    close.textContent = "Close";
    close.addEventListener("click", () => void removeOverlayWithMotion(overlay));
    card.append(heading, detail, close);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
    close.focus();
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) void removeOverlayWithMotion(overlay);
    });
  }

  function showEvaluationQuestionDraftGenerating(generatorLabel = "selected generator") {
    const existing = documentRef.querySelector(".evaluation-draft-overlay");
    if (existing) existing.remove();

    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card evaluation-draft-generating";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", "Generating benchmark question");
    card.setAttribute("aria-busy", "true");
    const spinner = documentRef.createElement("div");
    spinner.className = "evaluation-draft-spinner";
    spinner.setAttribute("aria-hidden", "true");
    const content = documentRef.createElement("div");
    const heading = documentRef.createElement("h2");
    heading.textContent = "Generating benchmark question";
    const detail = documentRef.createElement("p");
    detail.className = "evaluation-draft-notice";
    detail.setAttribute("role", "status");
    detail.textContent = `Preparing session evidence and asking ${generatorLabel} for a reviewable draft.`;
    content.append(heading, detail);
    card.append(spinner, content);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
  }

  async function showEvaluationQuestionDraft(
    sessionId, owner = state.userId, generatorId = "", templateId = state.activeEvaluationQuestionTemplateId,
  ) {
    const query = owner ? `?user_id=${encodeURIComponent(owner)}` : "";
    const generator = state.evaluationQuestionGenerators.find((item) => item.generator_id === generatorId);
    showEvaluationQuestionDraftGenerating(generator?.label || "the selected generator");
    try {
      const response = await fetchImpl(
        `/api/sessions/${encodeURIComponent(sessionId)}/evaluation-question-drafts${query}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ template_id: templateId, generator_id: generatorId }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        if (payload.detail && typeof payload.detail === "object") {
          const error = new Error(payload.detail.message || `HTTP ${response.status}`);
          error.diagnostics = payload.detail.diagnostics;
          throw error;
        }
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const draft = await response.json();
      if (draft.status === "no_qa_extracted") {
        showNoEvaluationQuestionExtracted(draft);
        return;
      }
      showEvaluationQuestionDraftModal(draft);
    } catch (error) {
      console.warn("Failed to generate staged benchmark question", error);
      showEvaluationQuestionDraftError(error);
    }
  }

  async function showSessionQuestionGeneratorPicker(sessionId, owner = state.userId) {
    const generators = await loadEvaluationQuestionGenerators(owner);
    if (!generators.length) {
      showEvaluationQuestionDraftError("No session question generators are configured.");
      return;
    }
    const existing = documentRef.querySelector(".evaluation-draft-overlay");
    if (existing) existing.remove();

    const overlay = documentRef.createElement("div");
    overlay.className = "evaluation-draft-overlay";
    const card = documentRef.createElement("section");
    card.className = "evaluation-draft-card evaluation-generator-picker";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", "Select question generator");
    const heading = documentRef.createElement("h2");
    heading.textContent = "Generate benchmark question";
    const detail = documentRef.createElement("p");
    detail.className = "evaluation-draft-notice";
    const label = documentRef.createElement("label");
    label.className = "evaluation-label";
    label.textContent = "Question generator";
    const select = documentRef.createElement("select");
    select.className = "evaluation-input";
    select.setAttribute("aria-label", "Question generator");
    for (const generator of generators) {
      const option = documentRef.createElement("option");
      option.value = generator.generator_id;
      option.textContent = generator.label;
      select.appendChild(option);
    }
    select.value = state.activeEvaluationQuestionGeneratorId || generators[0].generator_id;
    label.appendChild(select);
    const updateDescription = () => {
      const generator = generators.find((item) => item.generator_id === select.value);
      detail.textContent = generator?.description || "Generate a reviewable question from this session.";
    };
    select.addEventListener("change", updateDescription);
    updateDescription();
    const actions = documentRef.createElement("div");
    actions.className = "evaluation-draft-actions";
    const cancel = documentRef.createElement("button");
    cancel.className = "ghost";
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => void removeOverlayWithMotion(overlay));
    const generate = documentRef.createElement("button");
    generate.className = "evaluation-draft-export";
    generate.type = "button";
    generate.textContent = "Generate";
    generate.addEventListener("click", () => {
      state.activeEvaluationQuestionGeneratorId = select.value;
      overlay.remove();
      void showEvaluationQuestionDraft(sessionId, owner, select.value);
    });
    actions.append(cancel, generate);
    card.append(heading, detail, label, actions);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);
    select.focus();
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) void removeOverlayWithMotion(overlay);
    });
  }

  async function showSavedQuestionDrafts() {
    try {
      const response = await fetchImpl(
        `/api/evaluation-question-drafts?user_id=${encodeURIComponent(state.userId)}`,
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const overlay = documentRef.createElement("div");
      overlay.className = "evaluation-draft-overlay";
      const card = documentRef.createElement("section");
      card.className = "evaluation-draft-card evaluation-draft-list-card";
      card.setAttribute("role", "dialog");
      card.setAttribute("aria-modal", "true");
      card.setAttribute("aria-label", "Saved evaluation question drafts");
      const header = documentRef.createElement("header");
      header.className = "evaluation-draft-header";
      const heading = documentRef.createElement("h2");
      heading.textContent = "Saved question drafts";
      const close = documentRef.createElement("button");
      close.type = "button";
      close.className = "ghost";
      close.textContent = "Close";
      close.addEventListener("click", () => void removeOverlayWithMotion(overlay));
      header.append(heading, close);
      const list = documentRef.createElement("div");
      list.className = "evaluation-draft-list";
      const drafts = payload.drafts || [];
      if (!drafts.length) {
        const empty = documentRef.createElement("p");
        empty.className = "evaluation-draft-notice";
        empty.textContent = "No saved question drafts yet.";
        list.appendChild(empty);
      }
      for (const draft of drafts) {
        const row = documentRef.createElement("button");
        row.type = "button";
        row.className = "evaluation-draft-list-item";
        const title = documentRef.createElement("strong");
        title.textContent = draft.question_id || "Untitled question";
        const meta = documentRef.createElement("span");
        meta.textContent = `${draft.status} · session ${draft.source_session_id || "unknown"}`;
        row.append(title, meta);
        row.addEventListener("click", async () => {
          const loaded = await fetchImpl(
            `/api/evaluation-question-drafts/${encodeURIComponent(draft.draft_id)}?user_id=${encodeURIComponent(state.userId)}`,
          );
          const data = await loaded.json().catch(() => ({}));
          if (!loaded.ok) {
            showEvaluationQuestionDraftError(data.detail || `HTTP ${loaded.status}`);
            return;
          }
          showEvaluationQuestionDraftModal(data);
        });
        list.appendChild(row);
      }
      card.append(header, list);
      overlay.appendChild(card);
      documentRef.body.appendChild(overlay);
      close.focus();
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) void removeOverlayWithMotion(overlay);
      });
    } catch (error) {
      showEvaluationQuestionDraftError(error.message || "Saved drafts could not be loaded.");
    }
  }

  documentRef.getElementById("saved-question-drafts")?.addEventListener("click", () => {
    void showSavedQuestionDrafts();
  });

  return {
    setApplicationMode,
    showSessionQuestionGeneratorPicker,
  };
}
