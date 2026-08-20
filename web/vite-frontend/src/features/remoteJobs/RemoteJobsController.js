import { httpClient as defaultHttpClient } from "../../shared/api/http.js";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "submitting", "resuming"]);
const STATUS_LABELS = {
  created: "Created",
  submitting: "Submitting",
  queued: "Queued",
  running: "Running",
  pause_requested: "Pausing",
  paused: "Paused",
  resume_requested: "Resuming",
  resuming: "Resuming",
  succeeded: "Completed",
  collecting: "Collecting results",
  collected: "Completed",
  terminate_requested: "Terminating",
  terminated: "Terminated",
  failed: "Failed",
  cancelled: "Cancelled",
  lost: "Lost",
};

export function remoteJobLifecycle(status) {
  const key = String(status || "unknown").toLowerCase();
  return { key, label: STATUS_LABELS[key] || "Unknown" };
}

function demoJobs() {
  return [
    {
      job_id: "demo-running-job",
      external_id: "sandbox-demo-running",
      provider: "e2b",
      status: "running",
      snapshot: { provider_status: "running" },
    },
    {
      job_id: "demo-paused-job",
      external_id: "sandbox-demo-paused",
      provider: "e2b",
      status: "paused",
      snapshot: { provider_status: "paused" },
    },
    {
      job_id: "demo-complete-job",
      external_id: "sandbox-demo-complete",
      provider: "e2b",
      status: "collected",
      snapshot: { provider_status: "completed" },
    },
  ];
}

/** Owns remote-job data loading, polling, rendering, and user controls. */
export function createRemoteJobsController({
  state,
  dummyMode = false,
  onJobsChanged = () => {},
  httpClient = defaultHttpClient,
  document: documentRef = globalThis.document,
  window: windowRef = globalThis.window,
  pollIntervalMs = 15_000,
} = {}) {
  const list = documentRef.getElementById("remote-job-list");
  const refreshButton = documentRef.getElementById("refresh-remote-jobs");
  const toggleButton = documentRef.getElementById("remote-jobs-toggle");
  const pane = documentRef.getElementById("remote-jobs-pane");
  const demoBadge = documentRef.getElementById("remote-jobs-demo-badge");
  const graphRail = documentRef.getElementById("graph-column");
  const popover = documentRef.createElement("div");
  popover.className = "remote-job-detail";
  popover.id = "remote-job-detail-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "Remote job details");
  documentRef.body.appendChild(popover);

  const demoJobsBySession = new Map();
  let pollTimer = null;
  let popoverHideTimer = null;
  let visibleCard = null;
  let expanded = false;
  let destroyed = false;

  function getDemoJobs(sessionId, owner) {
    const key = `${owner}:${sessionId}`;
    if (!demoJobsBySession.has(key)) demoJobsBySession.set(key, demoJobs());
    return demoJobsBySession.get(key);
  }

  function hidePopover() {
    windowRef.clearTimeout(popoverHideTimer);
    visibleCard?.classList.remove("is-detail-open");
    popover.classList.remove("is-visible");
    visibleCard = null;
  }

  function schedulePopoverHide() {
    windowRef.clearTimeout(popoverHideTimer);
    popoverHideTimer = windowRef.setTimeout(hidePopover, 150);
  }

  function createDetail(job, providerStatus) {
    const detail = documentRef.createDocumentFragment();
    const fields = [
      ["Provider", job.provider || "remote"],
      ["Status", remoteJobLifecycle(job.status).label],
      ["Sandbox", job.external_id || "—"],
      ["Job ID", job.job_id || "—"],
    ];
    if (providerStatus) fields.splice(2, 0, ["Provider status", providerStatus]);
    for (const [label, value] of fields) {
      const row = documentRef.createElement("div");
      const key = documentRef.createElement("span");
      key.textContent = label;
      const content = documentRef.createElement("code");
      content.textContent = String(value);
      row.append(key, content);
      detail.appendChild(row);
    }
    return detail;
  }

  function showPopover(card, job, providerStatus) {
    windowRef.clearTimeout(popoverHideTimer);
    if (visibleCard && visibleCard !== card) visibleCard.classList.remove("is-detail-open");
    visibleCard = card;
    card.classList.add("is-detail-open");
    popover.replaceChildren(createDetail(job, providerStatus));
    popover.classList.add("is-visible");
    const rect = card.getBoundingClientRect();
    const width = Math.min(280, windowRef.innerWidth - 16);
    const left = Math.max(8, Math.min(rect.left, windowRef.innerWidth - width - 8));
    const top = Math.min(rect.bottom + 8, windowRef.innerHeight - 150);
    popover.style.left = `${left}px`;
    popover.style.top = `${Math.max(8, top)}px`;
  }

  async function controlJob(job, action, button) {
    const owner = state.activeSessionUserId || state.userId;
    const sessionId = state.sessionId;
    if (!sessionId || !owner || !job?.job_id) return;
    button.disabled = true;
    try {
      if (dummyMode) {
        if (action === "pause") {
          job.status = "paused";
          job.snapshot = { ...job.snapshot, provider_status: "paused" };
        } else if (action === "terminate") {
          job.status = "terminated";
          job.snapshot = { ...job.snapshot, provider_status: "terminated" };
        }
        await load(sessionId, owner);
        return;
      }
      await httpClient.requestJson(
        `/api/sessions/${encodeURIComponent(sessionId)}/remote-jobs/${encodeURIComponent(job.job_id)}/${action}`,
        { method: "POST", query: { user_id: owner } },
      );
      await load(sessionId, owner);
    } catch (_) {
      // Keep the last server snapshot visible; the next poll can recover.
    } finally {
      button.disabled = false;
    }
  }

  function createActions(job) {
    const actions = documentRef.createElement("div");
    actions.className = "remote-job-actions";
    const active = ACTIVE_JOB_STATUSES.has(job.status);

    const refresh = documentRef.createElement("button");
    refresh.type = "button";
    refresh.className = "remote-job-action refresh-button";
    refresh.innerHTML = '<svg class="refresh-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M18.5 9A7 7 0 1 0 19 15"></path><path d="M18.5 5v4h-4"></path></svg>';
    refresh.title = "Refresh sandbox status";
    refresh.setAttribute("aria-label", "Refresh sandbox status");
    refresh.addEventListener("click", (event) => {
      event.stopPropagation();
      void controlJob(job, "refresh", refresh);
    });

    const pause = documentRef.createElement("button");
    pause.type = "button";
    pause.className = "remote-job-action";
    pause.textContent = "Ⅱ";
    pause.title = "Pause sandbox";
    pause.setAttribute("aria-label", "Pause sandbox");
    pause.disabled = !active;
    pause.addEventListener("click", (event) => {
      event.stopPropagation();
      void controlJob(job, "pause", pause);
    });

    const terminate = documentRef.createElement("button");
    terminate.type = "button";
    terminate.className = "remote-job-action terminate";
    terminate.textContent = "■";
    terminate.title = "Terminate sandbox";
    terminate.setAttribute("aria-label", "Terminate sandbox");
    terminate.disabled = !active && job.status !== "paused";
    terminate.addEventListener("click", (event) => {
      event.stopPropagation();
      void controlJob(job, "terminate", terminate);
    });
    actions.append(refresh, pause, terminate);
    return actions;
  }

  function render() {
    if (!list || destroyed) return;
    hidePopover();
    list.replaceChildren();
    if (!state.remoteJobs.length) {
      const empty = documentRef.createElement("li");
      empty.className = "empty";
      empty.textContent = "No remote jobs in this session";
      list.appendChild(empty);
      return;
    }

    for (const job of state.remoteJobs) {
      const item = documentRef.createElement("li");
      const providerStatus = job.snapshot?.provider_status;
      const lifecycle = remoteJobLifecycle(job.status);
      item.className = `remote-job status-${lifecycle.key}`;
      item.tabIndex = 0;
      item.title = "Hover for job details";
      const header = documentRef.createElement("div");
      header.className = "remote-job-header";
      const provider = documentRef.createElement("span");
      provider.className = "remote-job-provider";
      provider.textContent = job.provider || "remote";
      const status = documentRef.createElement("span");
      status.className = "remote-job-status";
      status.textContent = lifecycle.label;
      header.append(provider, status, createActions(job));
      const identifier = documentRef.createElement("div");
      identifier.className = "remote-job-id";
      identifier.textContent = job.external_id || job.job_id;
      item.append(header, identifier);
      if (job.error) {
        const error = documentRef.createElement("div");
        error.className = "remote-job-error";
        error.textContent = job.error;
        item.appendChild(error);
      }
      const showDetails = () => showPopover(item, job, providerStatus);
      item.addEventListener("mouseenter", showDetails);
      item.addEventListener("mouseleave", schedulePopoverHide);
      item.addEventListener("focusin", showDetails);
      item.addEventListener("focusout", schedulePopoverHide);
      list.appendChild(item);
    }
  }

  async function load(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
    if (destroyed || !sessionId || !owner) return;
    if (dummyMode) {
      state.remoteJobs = getDemoJobs(sessionId, owner);
      demoBadge?.classList.remove("hidden");
      render();
      onJobsChanged();
      return;
    }
    try {
      const data = await httpClient.getJson(
        `/api/sessions/${encodeURIComponent(sessionId)}/remote-jobs`,
        { query: { user_id: owner } },
      );
      if (sessionId !== state.sessionId || owner !== state.activeSessionUserId) return;
      state.remoteJobs = Array.isArray(data?.jobs) ? data.jobs : [];
      render();
      onJobsChanged();
    } catch (_) {
      // The control plane may be restarting; retain the last visible snapshot.
    }
  }

  function startPolling(sessionId, owner) {
    stopPolling();
    if (destroyed || !sessionId || !owner) return;
    pollTimer = windowRef.setInterval(() => void load(sessionId, owner), pollIntervalMs);
  }

  function stopPolling() {
    if (pollTimer !== null) windowRef.clearInterval(pollTimer);
    pollTimer = null;
  }

  function setExpanded(nextExpanded) {
    expanded = Boolean(nextExpanded);
    list?.classList.toggle("hidden", !expanded);
    toggleButton?.setAttribute("aria-expanded", String(expanded));
    toggleButton?.classList.toggle("is-expanded", expanded);
    pane?.classList.toggle("is-expanded", expanded);
    graphRail?.classList.toggle("remote-jobs-expanded", expanded);
  }

  function reset({ notify = false } = {}) {
    stopPolling();
    state.remoteJobs = [];
    hidePopover();
    render();
    if (notify) onJobsChanged();
  }

  function handleKeydown(event) {
    if (event.key === "Escape") hidePopover();
  }

  const handleRefresh = () => void load();
  const handleToggle = () => setExpanded(!expanded);
  const keepPopoverOpen = () => windowRef.clearTimeout(popoverHideTimer);
  refreshButton?.addEventListener("click", handleRefresh);
  toggleButton?.addEventListener("click", handleToggle);
  popover.addEventListener("mouseenter", keepPopoverOpen);
  popover.addEventListener("mouseleave", schedulePopoverHide);
  documentRef.addEventListener("keydown", handleKeydown);

  function destroy() {
    if (destroyed) return;
    reset();
    destroyed = true;
    refreshButton?.removeEventListener("click", handleRefresh);
    toggleButton?.removeEventListener("click", handleToggle);
    popover.removeEventListener("mouseenter", keepPopoverOpen);
    popover.removeEventListener("mouseleave", schedulePopoverHide);
    documentRef.removeEventListener("keydown", handleKeydown);
    popover.remove();
  }

  return { destroy, load, reset, startPolling, stopPolling };
}
