import { httpClient as defaultHttpClient } from "../../shared/api/http.js";

const REFRESHABLE_JOB_STATUSES = new Set([
  "submitting", "queued", "running", "resuming",
]);
const PAUSABLE_JOB_STATUSES = new Set(["queued", "running"]);
const TERMINABLE_JOB_STATUSES = new Set([
  "queued", "running", "pause_requested", "paused", "resume_requested", "resuming",
]);
const LEGACY_PAUSE_PROVIDERS = new Set(["e2b"]);
const EXECUTION_PROGRESS_STATUSES = new Set([
  "running", "pause_requested", "paused", "resume_requested", "resuming",
]);
const TERMINAL_FAILURE_STATUSES = new Set(["failed", "cancelled", "terminated", "lost"]);
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

const SANDBOX_PROVIDERS = new Set(["e2b", "bohr_sandbox"]);
const STAGE_ROW_PX = 18;
const RACK_LIQUID_GLASS_FILTER_ROOT_ID = "rack-remote-job-liquid-glass-defs";
const RACK_LIQUID_GLASS_FILTER_ID = "rack-remote-job-liquid-glass-filter";
const WORKLOAD_DEFINITIONS = Object.freeze({
  md: Object.freeze({
    label: "MD",
    phases: Object.freeze([
      ["prepare", "Prepare"], ["submit", "Submit"], ["queue", "Queue"],
      ["execute", "Simulate"], ["collect", "Collect"], ["validate", "Validate"],
    ]),
  }),
  relaxation: Object.freeze({
    label: "Relaxation",
    phases: Object.freeze([
      ["prepare", "Prepare"], ["submit", "Submit"], ["queue", "Queue"],
      ["execute", "Relax"], ["collect", "Collect"], ["validate", "Verify"],
    ]),
  }),
  vasp: Object.freeze({
    label: "VASP",
    phases: Object.freeze([
      ["prepare", "Prepare"], ["submit", "Submit"], ["queue", "Queue"],
      ["execute", "Solve"], ["collect", "Collect"], ["validate", "Verify"],
    ]),
  }),
  training: Object.freeze({
    label: "Training",
    phases: Object.freeze([
      ["prepare", "Prepare"], ["submit", "Submit"], ["queue", "Queue"],
      ["execute", "Train"], ["collect", "Collect"], ["validate", "Evaluate"],
    ]),
  }),
  generic: Object.freeze({
    label: "Generic",
    phases: Object.freeze([
      ["prepare", "Prepare"], ["submit", "Submit"], ["queue", "Queue"],
      ["execute", "Execute"], ["collect", "Collect"], ["validate", "Verify"],
    ]),
  }),
});
const PHASE_ALIASES = Object.freeze({
  prepare: "prepare", preparing: "prepare", preparation: "prepare", setup: "prepare", staging: "prepare",
  submit: "submit", submitting: "submit", submitted: "submit", provisioning: "submit",
  queue: "queue", queued: "queue", pending: "queue", scheduling: "queue", input_staging: "queue",
  execute: "execute", executing: "execute", running: "execute", simulation: "execute", simulate: "execute",
  execution: "execute", solve: "execute", solving: "execute", train: "execute", training: "execute",
  relax: "execute", relaxing: "execute", relaxation: "execute", optimization: "execute", optimisation: "execute",
  collect: "collect", collecting: "collect", collection: "collect", collected: "collect", succeeded: "collect",
  validate: "validate", validating: "validate", validation: "validate", verify: "validate", verifying: "validate",
  evaluate: "validate", evaluating: "validate",
});
const SENSITIVE_CONFIG_KEY = /(api.?key|token|secret|password|credential|authorization)/i;
const CONFIGURATION_FIELDS = Object.freeze([
  ["template", "Template"],
  ["job_name", "Job name"],
  ["machine_type", "Machine"],
  ["image", "Image"],
  ["image_address", "Image"],
  ["gpu", "GPU"],
  ["timeout", "Timeout"],
  ["max_run_time", "Max time"],
  ["project_id", "Project"],
  ["input_directory", "Input"],
  ["result_path", "Results"],
  ["never_timeout", "Persistent"],
  ["lifecycle", "Lifecycle"],
  ["command", "Command"],
  ["env", "Environment"],
]);

export function remoteJobLifecycle(status) {
  const key = String(status || "unknown").toLowerCase();
  return { key, label: STATUS_LABELS[key] || "Unknown" };
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function remoteJobView(job = {}) {
  return isRecord(job.view) ? job.view : {};
}

function projectedLifecycleKey(job = {}) {
  const view = remoteJobView(job);
  const lifecycle = isRecord(view.lifecycle) ? view.lifecycle : {};
  const allocation = isRecord(view.allocation) ? view.allocation : {};
  return remoteJobLifecycle(
    lifecycle.status ?? allocation.lifecycle_status ?? view.lifecycle_status ?? job.status,
  ).key;
}

function projectedActionDecision(job = {}, action) {
  const view = remoteJobView(job);
  const controls = isRecord(view.controls) ? view.controls : {};
  const sources = [
    view.actions,
    controls.actions,
    view.action_projection,
    job.actions,
    job.action_projection,
    job.available_actions,
  ];
  for (const source of sources) {
    if (Array.isArray(source)) return source.map(String).includes(action);
    if (!isRecord(source)) continue;
    const value = source[action];
    if (isRecord(value)) {
      return value.enabled === true || value.available === true || value.allowed === true;
    }
    // A projected action matrix is complete: an omitted or non-true entry is
    // deliberately unavailable and must not fall through to provider guesses.
    return value === true;
  }
  return null;
}

function projectedCapabilities(job = {}) {
  const view = remoteJobView(job);
  const allocation = isRecord(view.allocation) ? view.allocation : {};
  const sources = [view.capabilities, allocation.capabilities, job.capabilities];
  for (const source of sources) {
    if (Array.isArray(source)) {
      return { present: true, values: new Set(source.map((value) => String(value).toLowerCase())) };
    }
    if (isRecord(source)) {
      const values = new Set(
        Object.entries(source)
          .filter(([, enabled]) => enabled === true || isRecord(enabled) && enabled.enabled === true)
          .map(([name]) => name.toLowerCase()),
      );
      return { present: true, values };
    }
  }
  return { present: false, values: new Set() };
}

/** Resolve controls from a backend projection, with a conservative legacy fallback. */
export function remoteJobActionEnabled(job = {}, action) {
  if (!["refresh", "pause", "terminate"].includes(action)) return false;
  const projected = projectedActionDecision(job, action);
  if (projected !== null) return projected;

  const status = projectedLifecycleKey(job);
  const hasProviderIdentity = Boolean(job.external_id || remoteJobView(job).identity?.provider_job_id);
  if (action === "refresh") {
    return hasProviderIdentity && REFRESHABLE_JOB_STATUSES.has(status);
  }
  if (action === "terminate") {
    return hasProviderIdentity && TERMINABLE_JOB_STATUSES.has(status);
  }
  if (!hasProviderIdentity || !PAUSABLE_JOB_STATUSES.has(status)) return false;

  const capabilities = projectedCapabilities(job);
  if (capabilities.present) return capabilities.values.has("pause");
  // Old list responses have no public capability projection. Preserve their
  // known E2B behavior only as the final fallback; it can never override a
  // backend actions/capabilities decision above.
  return LEGACY_PAUSE_PROVIDERS.has(String(job.provider || "").toLowerCase());
}

function isRemoteJobViewV2(job = {}) {
  return remoteJobView(job).version === "mc.remote-job-view.v2";
}

function projectedWorkload(job = {}) {
  const view = remoteJobView(job);
  if (isRecord(view.workload)) return view.workload;
  if (isRecord(view.task)) return view.task;
  return {};
}

function presentationSources(job = {}) {
  if (isRemoteJobViewV2(job)) {
    return [projectedWorkload(job), remoteJobView(job)];
  }
  const specification = isRecord(job.specification) ? job.specification : {};
  return [job.view, job.presentation, specification.presentation].filter(isRecord);
}

function firstPresentationValue(job, ...keys) {
  for (const source of presentationSources(job)) {
    for (const key of keys) {
      const value = source[key];
      if (value !== undefined && value !== null && value !== "") return value;
    }
  }
  return undefined;
}

function normalizedWorkloadKind(value) {
  const key = String(value || "").trim().toLowerCase().replaceAll("-", "_");
  if (["md", "molecular_dynamics", "dynamics", "simulation", "lammps", "ase_md"].includes(key)) return "md";
  if ([
    "relaxation", "relax", "structure_relaxation", "geometry_optimization",
    "geometry_optimisation", "optimization", "optimisation",
  ].includes(key)) return "relaxation";
  if (["vasp", "dft", "electronic_structure", "first_principles"].includes(key)) return "vasp";
  if (["training", "train", "finetune", "fine_tune", "deepmd", "model_training"].includes(key)) return "training";
  if (key === "generic" || key === "task") return "generic";
  return null;
}

function inferLegacyWorkloadKind(job = {}) {
  const specification = isRecord(job.specification) ? job.specification : {};
  // Deliberately exclude command and env: both can contain credentials or
  // arbitrary user text and are not part of the presentation contract.
  const corpus = [
    job.job_name, job.node_id, job.task_type, job.workload_kind,
    specification.job_name, specification.template, specification.image_address,
    specification.task_type, specification.workload_kind,
  ].filter((value) => value !== undefined && value !== null).join(" ").toLowerCase();
  if (/\b(?:structure[ _-]?)?relax(?:ation|ing|ed)?\b|\bgeometry[ _-]?(?:optimi[sz]ation|relaxation)\b/.test(corpus)) return "relaxation";
  if (/\bvasp\b|\bdft\b|\bscf\b|\bnscf\b/.test(corpus)) return "vasp";
  if (/\bdeepmd\b|\bfinetun(?:e|ing)\b|\btraining\b|\btrain\b|\bmattergen\b/.test(corpus)) return "training";
  if (/\blammps\b|\bmolecular[ _-]?dynamics\b|\base[ _-]?md\b|\bnpt\b|\bnvt\b|\bnve\b/.test(corpus)) return "md";
  return "generic";
}

function normalizePhaseId(value) {
  const key = String(value || "").trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  return PHASE_ALIASES[key] || null;
}

function defaultPhasePlan(kind) {
  return WORKLOAD_DEFINITIONS[kind].phases.map(([id, label]) => ({
    id,
    label,
    progressApplicable: id === "execute",
  }));
}

function normalizePhasePlan(kind, suppliedPlan) {
  if (!Array.isArray(suppliedPlan) || suppliedPlan.length === 0) return defaultPhasePlan(kind);
  const defaults = new Map(defaultPhasePlan(kind).map((phase) => [phase.id, phase.label]));
  const seen = new Set();
  const phases = [];
  for (const entry of suppliedPlan) {
    const rawId = isRecord(entry) ? entry.id ?? entry.key ?? entry.phase : entry;
    const id = normalizePhaseId(rawId);
    if (!id || seen.has(id)) continue;
    const label = isRecord(entry) && (entry.label || entry.title)
      ? String(entry.label || entry.title)
      : defaults.get(id);
    if (!label) continue;
    const configuredProgress = isRecord(entry)
      ? entry.progress_applicable ?? entry.show_progress
      : undefined;
    seen.add(id);
    phases.push({
      id,
      label,
      progressApplicable: typeof configuredProgress === "boolean"
        ? configuredProgress
        : id === "execute",
    });
  }
  return phases.length ? phases : defaultPhasePlan(kind);
}

function legacyPhaseForLifecycle(status) {
  const key = remoteJobLifecycle(status).key;
  if (key === "created") return "prepare";
  if (key === "submitting") return "submit";
  if (key === "queued") return "queue";
  if (EXECUTION_PROGRESS_STATUSES.has(key) || key === "terminate_requested") return "execute";
  if (["succeeded", "collecting", "collected"].includes(key)) return "collect";
  return null;
}

/** Build a presentation-only task model without changing remote-job lifecycle. */
export function normalizeRemoteJobPresentation(job = {}) {
  const specification = isRecord(job.specification) ? job.specification : {};
  const explicitKind = firstPresentationValue(job, "workload_kind", "task_type", "kind", "type")
    ?? job.workload_kind ?? job.task_type ?? specification.workload_kind ?? specification.task_type;
  // A versioned backend view is authoritative. Never reclassify it from
  // provider/template strings when it intentionally reports an unknown kind.
  const kind = normalizedWorkloadKind(explicitKind)
    || (isRemoteJobViewV2(job) ? "generic" : inferLegacyWorkloadKind(job));
  const definition = WORKLOAD_DEFINITIONS[kind];
  const suppliedPlan = firstPresentationValue(job, "phase_plan", "phases", "stages");
  const phasePlan = normalizePhasePlan(kind, suppliedPlan);
  const explicitPhase = firstPresentationValue(job, "current_phase", "current_stage", "phase", "stage")
    ?? job.current_phase ?? job.task_phase;
  const currentPhase = normalizePhaseId(explicitPhase) || legacyPhaseForLifecycle(job.status);
  const phaseIndex = phasePlan.findIndex(({ id }) => id === currentPhase);
  const lifecycle = remoteJobLifecycle(job.status);
  const paused = lifecycle.key === "paused";
  const collected = lifecycle.key === "collected";
  const failed = TERMINAL_FAILURE_STATUSES.has(lifecycle.key);
  const phases = phasePlan.map((phase, index) => {
    let state = "pending";
    if (phaseIndex >= 0 && index < phaseIndex) state = "complete";
    else if (index === phaseIndex) {
      if (failed) state = "failed";
      else if (paused) state = "paused";
      else if (collected && phase.id === "collect") state = "complete";
      else state = "active";
    }
    return { ...phase, state };
  });
  const projectedPhaseLabel = firstPresentationValue(job, "phase_label");
  const currentLabel = projectedPhaseLabel
    || (phaseIndex >= 0 ? phasePlan[phaseIndex].label : lifecycle.label);
  const title = firstPresentationValue(job, "title", "display_name", "task_label") || definition.label;
  const projectedShowProgress = firstPresentationValue(job, "show_progress");
  const currentPhaseDefinition = phaseIndex >= 0 ? phasePlan[phaseIndex] : null;
  const phaseAllowsProgress = typeof projectedShowProgress === "boolean"
    ? projectedShowProgress
    : currentPhaseDefinition?.progressApplicable ?? currentPhase === "execute";
  const showsProgress = phaseAllowsProgress
    && (EXECUTION_PROGRESS_STATUSES.has(lifecycle.key) || lifecycle.key === "unknown");

  return {
    schemaVersion: isRemoteJobViewV2(job) ? 2 : 1,
    kind,
    kindLabel: definition.label,
    title: String(title),
    currentPhase,
    currentLabel,
    phaseIndex,
    phases,
    phaseAllowsProgress,
    showsProgress,
    // Compatibility alias for consumers of the first presentation model.
    showsExecutionProgress: showsProgress,
  };
}

function normalizedPercent(value, { fraction = false } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const maximum = fraction ? 1 : 100;
  if (value < 0 || value > maximum) return null;
  return Math.round(fraction ? value * 100 : value);
}

function progressPercentFromSource(source, { allowLegacyScalar = false } = {}) {
  if (isRecord(source)) {
    const direct = normalizedPercent(source.percent);
    if (direct !== null) return direct;
    const fraction = normalizedPercent(source.fraction, { fraction: true });
    if (fraction !== null) return fraction;
    const current = source.current ?? source.completed;
    const total = source.total;
    if (typeof current === "number" && Number.isFinite(current)
        && typeof total === "number" && Number.isFinite(total)
        && current >= 0 && total > 0 && current <= total) {
      return Math.round((current / total) * 100);
    }
    return null;
  }
  // A bare scalar has no unit. It is accepted only for legacy fields and is
  // always a percentage; fractions must use the explicit ``fraction`` key.
  if (!allowLegacyScalar) return null;
  return normalizedPercent(source);
}

function explicitRemoteJobProgress(job = {}) {
  const snapshot = isRecord(job.snapshot) ? job.snapshot : {};
  const specification = isRecord(job.specification) ? job.specification : {};
  const view = remoteJobView(job);
  if (isRemoteJobViewV2(job) && Object.prototype.hasOwnProperty.call(view, "progress")) {
    return progressPercentFromSource(view.progress);
  }
  // Versioned/public projections take precedence over legacy job fields, and
  // current job fields take precedence over potentially stale snapshots.
  const projectedSources = [
    job.view?.progress,
    job.presentation?.progress,
    specification.presentation?.progress,
    job.view?.execution_progress,
    job.presentation?.execution_progress,
    specification.presentation?.execution_progress,
  ].filter((value) => value !== undefined && value !== null);
  for (const source of projectedSources) {
    const percent = progressPercentFromSource(source, { allowLegacyScalar: true });
    if (percent !== null) return percent;
  }

  const currentJobSources = [
    isRecord(job.progress) ? job.progress : undefined,
    job.progress_percent === undefined ? undefined : { percent: job.progress_percent },
    job.progress_fraction === undefined ? undefined : { fraction: job.progress_fraction },
    isRecord(job.progress) ? undefined : job.progress,
  ].filter((value) => value !== undefined && value !== null);
  for (const source of currentJobSources) {
    const percent = progressPercentFromSource(source, { allowLegacyScalar: true });
    if (percent !== null) return percent;
  }

  const snapshotSources = [
    snapshot.execution_progress,
    snapshot.task_progress,
    snapshot.progress_percent === undefined ? undefined : { percent: snapshot.progress_percent },
    snapshot.percent === undefined ? undefined : { percent: snapshot.percent },
    snapshot.progress_fraction === undefined ? undefined : { fraction: snapshot.progress_fraction },
    snapshot.progress,
  ].filter((value) => value !== undefined && value !== null);
  for (const source of snapshotSources) {
    const percent = progressPercentFromSource(source, { allowLegacyScalar: true });
    if (percent !== null) return percent;
  }
  return null;
}

export function remoteJobProgress(
  job = {},
  lifecycle = remoteJobLifecycle(job.status),
  presentation = normalizeRemoteJobPresentation(job),
) {
  if (!presentation.showsProgress) {
    return {
      mode: "hidden",
      percent: null,
      shortLabel: "",
      ariaText: `${presentation.currentLabel} · progress is not applicable to this stage`,
    };
  }
  const explicitPercent = explicitRemoteJobProgress(job);
  if (explicitPercent !== null) {
    return {
      mode: lifecycle.key === "paused" ? "paused" : "determinate",
      percent: explicitPercent,
      shortLabel: `${explicitPercent}%`,
      ariaText: `${presentation.currentLabel} · ${explicitPercent}%`,
    };
  }
  if (lifecycle.key === "paused") {
    return {
      mode: "paused",
      percent: null,
      shortLabel: "Held",
      ariaText: `${presentation.currentLabel} paused · exact progress unavailable`,
    };
  }
  return {
    mode: "indeterminate",
    percent: null,
    shortLabel: "Live",
    ariaText: `${presentation.currentLabel} · exact progress unavailable`,
  };
}

export function remoteJobErrorSummary(error) {
  return error ? "Provider error · details hidden for safety" : "";
}

function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return String(value);
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

function configurationValue(key, value) {
  if (key === "command") return value ? "Configured" : "—";
  if (key === "env") {
    const count = value && typeof value === "object" ? Object.keys(value).length : 0;
    return count ? `${count} variable${count === 1 ? "" : "s"}` : "None";
  }
  if (key === "lifecycle" && value && typeof value === "object") {
    const onTimeout = value.on_timeout ? `timeout: ${value.on_timeout}` : "";
    const autoResume = value.auto_resume === undefined ? "" : `auto resume: ${value.auto_resume ? "yes" : "no"}`;
    return [onTimeout, autoResume].filter(Boolean).join(" · ") || "Configured";
  }
  if (key === "timeout" || key === "max_run_time") return formatDuration(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value && typeof value === "object") return `${Object.keys(value).length} settings`;
  return String(value);
}

export function remoteJobConfiguration(job = {}) {
  const specification = job.specification && typeof job.specification === "object"
    ? job.specification
    : {};
  const rows = [];
  if (job.provider) rows.push({ label: "Provider", value: String(job.provider).replaceAll("_", " ") });
  for (const [key, label] of CONFIGURATION_FIELDS) {
    if (SENSITIVE_CONFIG_KEY.test(key) || specification[key] === undefined || specification[key] === null || specification[key] === "") continue;
    rows.push({ label, value: configurationValue(key, specification[key]) });
  }
  if (job.node_id) rows.push({ label: "Node", value: String(job.node_id) });
  if (job.step_number !== undefined && job.step_number !== null) rows.push({ label: "Step", value: String(job.step_number) });
  if (job.output_dir) rows.push({ label: "Output", value: String(job.output_dir) });
  return rows.slice(0, 8);
}

export function setRemoteJobCardFlipped(card, flipped) {
  if (!card) return;
  const nextFlipped = Boolean(flipped);
  const summary = card.querySelector(".remote-job-face-summary");
  const details = card.querySelector(".remote-job-face-details");
  const toggle = card.querySelector(".remote-job-card-toggle");
  const detailsBack = card.querySelector(".remote-job-details-back");

  card.classList.toggle("is-flipped", nextFlipped);
  card.dataset.flipped = String(nextFlipped);

  summary?.setAttribute("aria-hidden", String(nextFlipped));
  details?.setAttribute("aria-hidden", String(!nextFlipped));
  if (nextFlipped) {
    summary?.setAttribute("inert", "");
    details?.removeAttribute("inert");
  } else {
    summary?.removeAttribute("inert");
    details?.setAttribute("inert", "");
  }

  if (toggle) {
    const jobLabel = toggle.dataset.jobLabel || "remote job";
    toggle.setAttribute("aria-expanded", String(nextFlipped));
    toggle.setAttribute("aria-label", `Show details for ${jobLabel}`);
    toggle.title = `Show details for ${jobLabel}`;
  }
  if (detailsBack) {
    const jobLabel = detailsBack.dataset.jobLabel || "remote job";
    detailsBack.setAttribute("aria-label", `Return to summary for ${jobLabel}`);
    detailsBack.title = `Return to summary for ${jobLabel}`;
  }
}

function demoJobs() {
  return [
    {
      job_id: "demo-running-job",
      external_id: "sandbox-demo-running",
      provider: "e2b",
      status: "running",
      specification: {
        template: "demo-template",
        timeout: 7200,
        env: { DEMO_SECRET: "never-render-this-value" },
      },
      snapshot: { provider_status: "running", progress_percent: 42 },
    },
    {
      job_id: "demo-paused-job",
      external_id: "sandbox-demo-paused",
      provider: "e2b",
      status: "paused",
      specification: { template: "demo-template", timeout: 3600 },
      snapshot: { provider_status: "paused" },
    },
    {
      job_id: "demo-complete-job",
      external_id: "sandbox-demo-complete",
      provider: "e2b",
      status: "collected",
      specification: { template: "demo-template", timeout: 3600 },
      snapshot: { provider_status: "completed" },
    },
  ];
}

/** Owns remote-job data loading, polling, rendering, and user controls. */
export function createRemoteJobsController({
  state,
  dummyMode = false,
  onJobsChanged = () => {},
  onLayoutChanged = () => {},
  httpClient = defaultHttpClient,
  document: documentRef = globalThis.document,
  window: windowRef = globalThis.window,
  pollIntervalMs = 15_000,
} = {}) {
  const list = documentRef.getElementById("remote-job-list");
  const refreshButton = documentRef.getElementById("refresh-remote-jobs");
  const toggleButton = documentRef.getElementById("remote-jobs-toggle");
  const pane = documentRef.getElementById("remote-jobs-pane");
  const graphRail = documentRef.getElementById("graph-column");

  const demoJobsBySession = new Map();
  const flippedJobIds = new Set();
  const lastPhaseByJobId = new Map();
  let presentationJobs = null;
  let pollTimer = null;
  let expanded = false;
  let destroyed = false;
  let rackLiquidGlassFilterRoot = null;
  let rackLiquidGlassActiveCard = null;
  let rackLiquidGlassPointerFrame = null;
  let rackLiquidGlassPointerSample = null;

  function rackLiquidGlassEnabled() {
    return documentRef.body?.dataset?.skin === "rack-lab";
  }

  function setRackLiquidGlassProperty(card, name, value) {
    if (typeof card?.style?.setProperty === "function") card.style.setProperty(name, value);
    else if (card?.style) card.style[name] = value;
  }

  function resetRackLiquidGlassCard(card) {
    if (!card) return;
    card.classList.remove("is-liquid-glass-engaged");
    setRackLiquidGlassProperty(card, "--rack-glass-angle", "135deg");
    setRackLiquidGlassProperty(card, "--rack-glass-tilt-x", "0deg");
    setRackLiquidGlassProperty(card, "--rack-glass-tilt-y", "0deg");
    setRackLiquidGlassProperty(card, "--rack-glass-shift-x", "0px");
    setRackLiquidGlassProperty(card, "--rack-glass-shift-y", "0px");
  }

  function closestRemoteJob(node) {
    let current = node;
    while (current && current !== list) {
      if (current.classList?.contains("remote-job")) return current;
      current = current.parentElement;
    }
    return null;
  }

  function clearRackLiquidGlassPointerFrame() {
    if (rackLiquidGlassPointerFrame !== null) {
      windowRef.cancelAnimationFrame?.(rackLiquidGlassPointerFrame);
    }
    rackLiquidGlassPointerFrame = null;
    rackLiquidGlassPointerSample = null;
  }

  function flushRackLiquidGlassPointer() {
    rackLiquidGlassPointerFrame = null;
    const sample = rackLiquidGlassPointerSample;
    rackLiquidGlassPointerSample = null;
    const card = sample?.card;
    if (!card || !rackLiquidGlassEnabled()) return;
    const rect = card.getBoundingClientRect?.();
    if (!rect?.width || !rect?.height) return;
    const x = Math.min(1, Math.max(0, (sample.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (sample.clientY - rect.top) / rect.height));
    const normalizedX = (x - 0.5) * 2;
    const normalizedY = (y - 0.5) * 2;
    card.classList.add("is-liquid-glass-engaged");
    setRackLiquidGlassProperty(card, "--rack-glass-angle", `${(135 + normalizedX * 28).toFixed(1)}deg`);
    setRackLiquidGlassProperty(card, "--rack-glass-tilt-x", `${(-normalizedY * 1.6).toFixed(2)}deg`);
    setRackLiquidGlassProperty(card, "--rack-glass-tilt-y", `${(normalizedX * 2.1).toFixed(2)}deg`);
    setRackLiquidGlassProperty(card, "--rack-glass-shift-x", `${(normalizedX * 0.65).toFixed(2)}px`);
    setRackLiquidGlassProperty(card, "--rack-glass-shift-y", `${(normalizedY * 0.5).toFixed(2)}px`);
  }

  function handleRackLiquidGlassPointerMove(event) {
    if (!rackLiquidGlassEnabled()) return;
    if (windowRef.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
    if (windowRef.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches === false) return;
    const card = closestRemoteJob(event.target);
    if (!card) return;
    if (rackLiquidGlassActiveCard && rackLiquidGlassActiveCard !== card) {
      resetRackLiquidGlassCard(rackLiquidGlassActiveCard);
    }
    rackLiquidGlassActiveCard = card;
    rackLiquidGlassPointerSample = {
      card,
      clientX: Number(event.clientX),
      clientY: Number(event.clientY),
    };
    if (!Number.isFinite(rackLiquidGlassPointerSample.clientX)
      || !Number.isFinite(rackLiquidGlassPointerSample.clientY)) return;
    if (rackLiquidGlassPointerFrame !== null) return;
    if (typeof windowRef.requestAnimationFrame === "function") {
      rackLiquidGlassPointerFrame = windowRef.requestAnimationFrame(flushRackLiquidGlassPointer);
    } else {
      flushRackLiquidGlassPointer();
    }
  }

  function handleRackLiquidGlassPointerOut(event) {
    const fromCard = closestRemoteJob(event.target);
    const toCard = closestRemoteJob(event.relatedTarget);
    if (!fromCard || fromCard === toCard) return;
    if (rackLiquidGlassPointerSample?.card === fromCard) clearRackLiquidGlassPointerFrame();
    resetRackLiquidGlassCard(fromCard);
    if (rackLiquidGlassActiveCard === fromCard) rackLiquidGlassActiveCard = null;
  }

  function ensureRackLiquidGlassFilter() {
    if (!rackLiquidGlassEnabled() || rackLiquidGlassFilterRoot) return;
    const existingFilterRoot = documentRef.getElementById?.(RACK_LIQUID_GLASS_FILTER_ROOT_ID);
    if (existingFilterRoot) {
      rackLiquidGlassFilterRoot = existingFilterRoot;
      return;
    }
    const createSvgElement = (name) => typeof documentRef.createElementNS === "function"
      ? documentRef.createElementNS("http://www.w3.org/2000/svg", name)
      : documentRef.createElement(name);
    const svg = createSvgElement("svg");
    svg.id = RACK_LIQUID_GLASS_FILTER_ROOT_ID;
    svg.setAttribute("class", "rack-remote-job-liquid-glass-defs");
    svg.setAttribute("width", "0");
    svg.setAttribute("height", "0");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    // One shared, low-cost displacement field keeps all cards on the same
    // visual material without creating one SVG/filter instance per face.
    svg.innerHTML = `<defs>
      <filter id="${RACK_LIQUID_GLASS_FILTER_ID}" x="-16%" y="-24%" width="132%" height="148%" color-interpolation-filters="sRGB">
        <feTurbulence type="fractalNoise" baseFrequency="0.012 0.055" numOctaves="1" seed="23" result="rackGlassNoise" />
        <feGaussianBlur in="rackGlassNoise" stdDeviation="0.55" result="rackGlassMap" />
        <feDisplacementMap in="SourceGraphic" in2="rackGlassMap" scale="15" xChannelSelector="R" yChannelSelector="B" result="rackGlassRedWarp" />
        <feDisplacementMap in="SourceGraphic" in2="rackGlassMap" scale="13.8" xChannelSelector="R" yChannelSelector="B" result="rackGlassGreenWarp" />
        <feDisplacementMap in="SourceGraphic" in2="rackGlassMap" scale="12.6" xChannelSelector="R" yChannelSelector="B" result="rackGlassBlueWarp" />
        <feColorMatrix in="rackGlassRedWarp" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="rackGlassRed" />
        <feColorMatrix in="rackGlassGreenWarp" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="rackGlassGreen" />
        <feColorMatrix in="rackGlassBlueWarp" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="rackGlassBlue" />
        <feBlend in="rackGlassGreen" in2="rackGlassBlue" mode="screen" result="rackGlassGB" />
        <feBlend in="rackGlassRed" in2="rackGlassGB" mode="screen" result="rackGlassRGB" />
        <feGaussianBlur in="rackGlassRGB" stdDeviation="0.16" />
      </filter>
    </defs>`;
    documentRef.body?.appendChild(svg);
    rackLiquidGlassFilterRoot = svg;
  }

  function decorateRackLiquidGlassFace(face) {
    if (!rackLiquidGlassEnabled()) return;
    const warp = documentRef.createElement("span");
    warp.className = "remote-job-glass-warp";
    warp.setAttribute("aria-hidden", "true");
    const edge = documentRef.createElement("span");
    edge.className = "remote-job-glass-edge";
    edge.setAttribute("aria-hidden", "true");
    face.append(warp, edge);
  }

  function getDemoJobs(sessionId, owner) {
    const key = `${owner}:${sessionId}`;
    if (!demoJobsBySession.has(key)) demoJobsBySession.set(key, demoJobs());
    return demoJobsBySession.get(key);
  }

  async function controlJob(job, action, button) {
    const owner = state.activeSessionUserId || state.userId;
    const sessionId = state.sessionId;
    if (presentationJobs !== null) return;
    if (!sessionId || !owner || !job?.job_id || !remoteJobActionEnabled(job, action)) return;
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

  function createRefreshControl(job, jobLabel) {
    const refresh = documentRef.createElement("button");
    refresh.type = "button";
    refresh.className = "remote-job-refresh-button remote-job-action refresh-button";
    refresh.dataset.remoteJobAction = "refresh";
    refresh.innerHTML = '<svg class="refresh-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M18.5 9A7 7 0 1 0 19 15"></path><path d="M18.5 5v4h-4"></path></svg>';
    refresh.title = `Refresh status for ${jobLabel}`;
    refresh.setAttribute("aria-label", `Refresh status for ${jobLabel}`);
    refresh.disabled = !remoteJobActionEnabled(job, "refresh");
    refresh.setAttribute("aria-disabled", String(refresh.disabled));
    refresh.addEventListener("click", (event) => {
      event.stopPropagation();
      if (refresh.disabled) return;
      void controlJob(job, "refresh", refresh);
    });
    return refresh;
  }

  function createActions(job) {
    const actions = documentRef.createElement("div");
    actions.className = "remote-job-actions";
    const canPause = remoteJobActionEnabled(job, "pause");
    const canTerminate = remoteJobActionEnabled(job, "terminate");

    const pause = documentRef.createElement("button");
    pause.type = "button";
    pause.className = "remote-job-action pause";
    pause.dataset.remoteJobAction = "pause";
    pause.textContent = "Ⅱ";
    pause.title = "Pause job";
    pause.setAttribute("aria-label", "Pause job");
    pause.disabled = !canPause;
    pause.setAttribute("aria-disabled", String(pause.disabled));
    if (!canPause) pause.title = "Pause is not available for this job";
    pause.addEventListener("click", (event) => {
      event.stopPropagation();
      if (pause.disabled) return;
      void controlJob(job, "pause", pause);
    });

    const terminate = documentRef.createElement("button");
    terminate.type = "button";
    terminate.className = "remote-job-action terminate";
    terminate.dataset.remoteJobAction = "terminate";
    terminate.textContent = "■";
    terminate.title = "Terminate job";
    terminate.setAttribute("aria-label", "Terminate job");
    terminate.disabled = !canTerminate;
    terminate.setAttribute("aria-disabled", String(terminate.disabled));
    terminate.addEventListener("click", (event) => {
      event.stopPropagation();
      if (terminate.disabled) return;
      void controlJob(job, "terminate", terminate);
    });
    actions.append(pause, terminate);
    return actions;
  }

  function createDetailRow(label, value) {
    const row = documentRef.createElement("div");
    row.className = "remote-job-detail-row";
    const key = documentRef.createElement("span");
    key.textContent = label;
    const content = documentRef.createElement("code");
    content.textContent = String(value || "—");
    content.title = String(value || "—");
    row.append(key, content);
    return row;
  }

  function createProgress(job, lifecycle, presentation, jobLabel) {
    const progress = remoteJobProgress(job, lifecycle, presentation);
    if (progress.mode === "hidden") return { progress, track: null };
    const track = documentRef.createElement("div");
    track.className = "remote-job-progress";
    track.dataset.progressMode = progress.mode;
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", `${jobLabel} progress`);
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuetext", progress.ariaText);
    if (progress.percent !== null) track.setAttribute("aria-valuenow", String(progress.percent));
    const fill = documentRef.createElement("span");
    fill.className = "remote-job-progress-fill";
    if (progress.percent !== null) fill.style.width = `${progress.percent}%`;
    track.appendChild(fill);
    return { progress, track };
  }

  function setStageTrackIndex(track, index) {
    const safeIndex = Math.max(0, Number.isFinite(index) ? index : 0);
    track.dataset.stageIndex = String(safeIndex);
    track.style.transform = `translateY(${-safeIndex * STAGE_ROW_PX}px)`;
  }

  function createStageItem(phase, index, count, className = "") {
    const stage = documentRef.createElement("div");
    stage.className = `remote-job-stage-item state-${phase.state || "pending"}${className ? ` ${className}` : ""}`;
    stage.dataset.phase = phase.id;
    const marker = documentRef.createElement("span");
    marker.className = "remote-job-stage-marker";
    marker.setAttribute("aria-hidden", "true");
    const label = documentRef.createElement("span");
    label.className = "remote-job-stage-label";
    label.textContent = phase.label;
    const ordinal = documentRef.createElement("span");
    ordinal.className = "remote-job-stage-ordinal";
    ordinal.textContent = Number.isFinite(index) && count > 0 ? `${index + 1}/${count}` : "—";
    stage.append(marker, label, ordinal);
    return stage;
  }

  function createStageViewport(presentation, jobKey) {
    const viewport = documentRef.createElement("div");
    viewport.className = "remote-job-stage-viewport";
    viewport.dataset.currentPhase = presentation.currentPhase || "unknown";
    viewport.setAttribute("role", "status");
    viewport.setAttribute("aria-live", "polite");
    viewport.setAttribute("aria-atomic", "true");

    const visiblePhases = presentation.phaseIndex >= 0
      ? presentation.phases
      : [{ id: "ended", label: presentation.currentLabel, state: "failed" }];
    const targetIndex = presentation.phaseIndex >= 0 ? presentation.phaseIndex : 0;
    const track = documentRef.createElement("div");
    track.className = "remote-job-stage-track";
    track.setAttribute("aria-hidden", "true");
    visiblePhases.forEach((phase, index) => track.appendChild(createStageItem(
      phase,
      presentation.phaseIndex >= 0 ? index : Number.NaN,
      presentation.phaseIndex >= 0 ? visiblePhases.length : 0,
    )));
    viewport.appendChild(track);
    viewport.setAttribute(
      "aria-label",
      `${presentation.kindLabel} task stage: ${presentation.currentLabel}${presentation.phaseIndex >= 0 ? `, ${presentation.phaseIndex + 1} of ${presentation.phases.length}` : ""}`,
    );

    const prior = lastPhaseByJobId.get(jobKey);
    const reducedMotion = Boolean(windowRef.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
    const phaseChanged = prior?.phase !== presentation.currentPhase
      || prior?.label !== presentation.currentLabel;
    const canTransition = !reducedMotion
      && prior?.kind === presentation.kind
      && prior.index >= 0
      && presentation.phaseIndex >= 0
      && phaseChanged;
    setStageTrackIndex(track, canTransition ? prior.index : targetIndex);
    viewport.dataset.previousPhase = prior?.phase || presentation.currentPhase || "unknown";
    if (canTransition && typeof windowRef.requestAnimationFrame === "function") {
      viewport.classList.add("is-stage-transitioning");
      windowRef.requestAnimationFrame(() => setStageTrackIndex(track, targetIndex));
    } else {
      setStageTrackIndex(track, targetIndex);
    }
    lastPhaseByJobId.set(jobKey, {
      index: presentation.phaseIndex,
      kind: presentation.kind,
      phase: presentation.currentPhase,
      label: presentation.currentLabel,
      state: visiblePhases[targetIndex]?.state || "pending",
      count: visiblePhases.length,
    });
    return viewport;
  }

  function clonePresentationJobs() {
    return (presentationJobs || []).map((job) => ({
      ...job,
      specification: { ...(job.specification || {}) },
      snapshot: { ...(job.snapshot || {}) },
    }));
  }

  function renderPresentationJobs() {
    if (presentationJobs === null) return false;
    state.remoteJobs = clonePresentationJobs();
    render();
    onJobsChanged();
    return true;
  }

  function flipCard(card, jobKey, nextFlipped) {
    if (nextFlipped) {
      for (const openCard of list?.querySelectorAll(".remote-job.is-flipped") || []) {
        if (openCard === card) continue;
        flippedJobIds.delete(openCard.dataset.jobKey);
        setRemoteJobCardFlipped(openCard, false);
      }
      flippedJobIds.add(jobKey);
    } else {
      flippedJobIds.delete(jobKey);
    }
    const details = card.querySelector(".remote-job-face-details");
    let focusedNode = documentRef.activeElement;
    let focusWasInDetails = false;
    while (focusedNode && focusedNode !== card) {
      if (focusedNode === details) focusWasInDetails = true;
      focusedNode = focusedNode.parentElement;
    }
    setRemoteJobCardFlipped(card, nextFlipped);
    list?.classList.toggle("has-expanded-card", Boolean(list.querySelector(".remote-job.is-flipped")));
    if (!nextFlipped && focusWasInDetails) {
      card.querySelector(".remote-job-card-toggle")?.focus({ preventScroll: true });
    }
  }

  function focusedRemoteJobControl() {
    const control = documentRef.activeElement;
    const action = control?.dataset?.remoteJobAction;
    if (!action) return null;
    let card = control;
    while (card && card !== list && !card.classList?.contains("remote-job")) card = card.parentElement;
    if (!card || card === list) return null;
    return { action, jobKey: card.dataset.jobKey };
  }

  function restoreRemoteJobControlFocus(focusedControl) {
    if (!focusedControl) return;
    const selectorByAction = {
      flip: ".remote-job-card-toggle",
      "flip-back": ".remote-job-details-back",
      refresh: ".remote-job-refresh-button",
      pause: ".remote-job-action.pause",
      terminate: ".remote-job-action.terminate",
    };
    const card = Array.from(list?.querySelectorAll(".remote-job") || [])
      .find((candidate) => candidate.dataset.jobKey === focusedControl.jobKey);
    const requested = card?.querySelector(selectorByAction[focusedControl.action]);
    const target = requested && !requested.disabled
      ? requested
      : card?.querySelector(".remote-job-card-toggle");
    target?.focus({ preventScroll: true });
  }

  function render() {
    if (!list || destroyed) return;
    clearRackLiquidGlassPointerFrame();
    rackLiquidGlassActiveCard = null;
    ensureRackLiquidGlassFilter();
    const focusedControl = focusedRemoteJobControl();
    list.replaceChildren();
    list.classList.remove("has-expanded-card");
    if (!state.remoteJobs.length) {
      const empty = documentRef.createElement("li");
      empty.className = "empty";
      empty.textContent = "No remote jobs in this session";
      list.appendChild(empty);
      return;
    }

    const currentJobIds = new Set();
    state.remoteJobs.forEach((job, index) => {
      const item = documentRef.createElement("li");
      const lifecycle = remoteJobLifecycle(job.status);
      const presentation = normalizeRemoteJobPresentation(job);
      const jobKey = String(job.job_id || job.external_id || `remote-job-${index}`);
      const jobLabel = String(job.external_id || job.job_id || "remote job");
      const summaryId = `remote-job-summary-${index}`;
      const detailsId = `remote-job-details-${index}`;
      currentJobIds.add(jobKey);
      item.className = `remote-job status-${lifecycle.key}`;
      item.dataset.jobKey = jobKey;
      item.dataset.workloadKind = presentation.kind;
      item.dataset.currentPhase = presentation.currentPhase || "unknown";
      if (rackLiquidGlassEnabled()) item.dataset.visualMaterial = "liquid-glass";

      const cardToggle = documentRef.createElement("button");
      cardToggle.type = "button";
      cardToggle.className = "remote-job-card-toggle";
      cardToggle.dataset.remoteJobAction = "flip";
      cardToggle.dataset.jobLabel = jobLabel;
      cardToggle.setAttribute("aria-controls", detailsId);
      cardToggle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (!item.classList.contains("is-flipped")) flipCard(item, jobKey, true);
      });
      cardToggle.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        event.stopPropagation();
        if (!item.classList.contains("is-flipped")) flipCard(item, jobKey, true);
      });

      const rotor = documentRef.createElement("div");
      rotor.className = "remote-job-rotor";

      const summary = documentRef.createElement("section");
      summary.className = "remote-job-face remote-job-face-summary";
      summary.id = summaryId;
      summary.setAttribute("aria-label", `${jobLabel} summary`);
      decorateRackLiquidGlassFace(summary);
      const identity = documentRef.createElement("div");
      identity.className = "remote-job-identity";
      const identityLabel = documentRef.createElement("span");
      identityLabel.className = "remote-job-identity-label";
      const identifierKind = SANDBOX_PROVIDERS.has(String(job.provider || "").toLowerCase()) ? "Sandbox" : "Job";
      identityLabel.textContent = identifierKind;
      const identifier = documentRef.createElement("code");
      identifier.className = "remote-job-id";
      identifier.textContent = job.external_id || job.job_id || "—";
      identifier.title = String(job.external_id || job.job_id || "—");
      identity.append(identityLabel, identifier);
      const statusRow = documentRef.createElement("div");
      statusRow.className = "remote-job-status-row";
      const status = documentRef.createElement("span");
      status.className = "remote-job-status";
      const statusDot = documentRef.createElement("span");
      statusDot.className = "remote-job-status-dot";
      statusDot.setAttribute("aria-hidden", "true");
      const statusText = documentRef.createElement("span");
      statusText.textContent = lifecycle.label;
      status.append(statusDot, statusText);
      const stageViewport = createStageViewport(presentation, jobKey);
      const { progress, track: progressTrack } = createProgress(job, lifecycle, presentation, jobLabel);
      statusRow.appendChild(status);
      if (progressTrack) {
        const progressLabel = documentRef.createElement("span");
        progressLabel.className = "remote-job-progress-label";
        progressLabel.textContent = progress.shortLabel;
        statusRow.appendChild(progressLabel);
        summary.classList.add("has-execution-progress");
        summary.append(identity, stageViewport, statusRow, progressTrack);
      } else {
        summary.append(identity, stageViewport, statusRow);
      }

      const details = documentRef.createElement("section");
      details.className = "remote-job-face remote-job-face-details";
      details.id = detailsId;
      details.setAttribute("aria-label", `${jobLabel} details and controls`);
      decorateRackLiquidGlassFace(details);
      const detailsHeader = documentRef.createElement("div");
      detailsHeader.className = "remote-job-details-header";
      const detailsTitle = documentRef.createElement("span");
      detailsTitle.className = "remote-job-details-title";
      detailsTitle.textContent = "Task config";
      const provider = documentRef.createElement("span");
      provider.className = "remote-job-provider";
      provider.textContent = String(job.provider || "remote").replaceAll("_", " ");
      const detailsBack = documentRef.createElement("button");
      detailsBack.type = "button";
      detailsBack.className = "remote-job-details-back remote-job-action";
      detailsBack.dataset.remoteJobAction = "flip-back";
      detailsBack.dataset.jobLabel = jobLabel;
      detailsBack.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m10 7-5 5 5 5"></path><path d="M5 12h10a4 4 0 0 1 4 4v1"></path></svg>';
      detailsBack.addEventListener("click", (event) => {
        event.stopPropagation();
        flipCard(item, jobKey, false);
      });
      detailsHeader.append(detailsBack, detailsTitle, provider);
      const detailRows = documentRef.createElement("div");
      detailRows.className = "remote-job-detail-rows";
      detailRows.appendChild(createDetailRow("Job ID", job.job_id));
      detailRows.appendChild(createDetailRow(
        SANDBOX_PROVIDERS.has(String(job.provider || "").toLowerCase()) ? "Sandbox ID" : "Provider ID",
        job.external_id,
      ));
      detailRows.appendChild(createDetailRow("Provider status", job.snapshot?.provider_status));
      const configuration = remoteJobConfiguration(job);
      if (configuration.length) {
        configuration.forEach(({ label, value }) => detailRows.appendChild(createDetailRow(label, value)));
      } else {
        detailRows.appendChild(createDetailRow("Config", "No persisted configuration"));
      }
      details.append(detailsHeader, detailRows);
      if (job.error) {
        const error = documentRef.createElement("div");
        error.className = "remote-job-error";
        error.textContent = remoteJobErrorSummary(job.error);
        error.title = remoteJobErrorSummary(job.error);
        error.setAttribute("role", "status");
        details.appendChild(error);
      }
      details.appendChild(createActions(job));

      const refresh = createRefreshControl(job, jobLabel);
      item.addEventListener("click", (event) => {
        if (event.defaultPrevented || event.target?.closest?.("button, a, input, select, textarea, [data-remote-job-action]")) return;
        if (item.classList.contains("is-flipped")) return;
        flipCard(item, jobKey, true);
      });

      rotor.append(summary, details);
      item.append(cardToggle, rotor, refresh);
      setRemoteJobCardFlipped(item, flippedJobIds.has(jobKey));
      list.appendChild(item);
    });
    for (const jobKey of flippedJobIds) {
      if (!currentJobIds.has(jobKey)) flippedJobIds.delete(jobKey);
    }
    for (const jobKey of lastPhaseByJobId.keys()) {
      if (!currentJobIds.has(jobKey)) lastPhaseByJobId.delete(jobKey);
    }
    list.classList.toggle("has-expanded-card", Boolean(list.querySelector(".remote-job.is-flipped")));
    restoreRemoteJobControlFocus(focusedControl);
  }

  async function load(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
    if (destroyed || !sessionId || !owner) return;
    if (renderPresentationJobs()) return;
    if (dummyMode) {
      state.remoteJobs = getDemoJobs(sessionId, owner);
      render();
      onJobsChanged();
      return;
    }
    try {
      const data = await httpClient.getJson(
        `/api/sessions/${encodeURIComponent(sessionId)}/remote-jobs`,
        { query: { user_id: owner } },
      );
      if (renderPresentationJobs()) return;
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
    if (destroyed || presentationJobs !== null || !sessionId || !owner) return;
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
    onLayoutChanged();
  }

  function reset({ notify = false } = {}) {
    stopPolling();
    state.remoteJobs = presentationJobs === null ? [] : clonePresentationJobs();
    flippedJobIds.clear();
    lastPhaseByJobId.clear();
    render();
    if (notify) onJobsChanged();
  }

  function handleKeydown(event) {
    if (event.key !== "Escape") return;
    const openCard = list?.querySelector(".remote-job.is-flipped");
    if (!openCard) return;
    flipCard(openCard, openCard.dataset.jobKey, false);
    openCard.querySelector(".remote-job-card-toggle")?.focus({ preventScroll: true });
  }

  const handleRefresh = () => void load();
  const handleToggle = () => setExpanded(!expanded);
  const handleThemeChange = () => render();
  const handlePaneTransitionEnd = (event) => {
    if (event.propertyName === "height") onLayoutChanged();
  };
  refreshButton?.addEventListener("click", handleRefresh);
  toggleButton?.addEventListener("click", handleToggle);
  pane?.addEventListener("transitionend", handlePaneTransitionEnd);
  list?.addEventListener("pointermove", handleRackLiquidGlassPointerMove);
  list?.addEventListener("pointerout", handleRackLiquidGlassPointerOut);
  windowRef.addEventListener?.("matcreator-theme-change", handleThemeChange);
  documentRef.addEventListener("keydown", handleKeydown);

  function destroy() {
    if (destroyed) return;
    presentationJobs = null;
    reset();
    destroyed = true;
    clearRackLiquidGlassPointerFrame();
    rackLiquidGlassActiveCard = null;
    rackLiquidGlassFilterRoot?.remove?.();
    rackLiquidGlassFilterRoot = null;
    refreshButton?.removeEventListener("click", handleRefresh);
    toggleButton?.removeEventListener("click", handleToggle);
    pane?.removeEventListener("transitionend", handlePaneTransitionEnd);
    list?.removeEventListener("pointermove", handleRackLiquidGlassPointerMove);
    list?.removeEventListener("pointerout", handleRackLiquidGlassPointerOut);
    windowRef.removeEventListener?.("matcreator-theme-change", handleThemeChange);
    documentRef.removeEventListener("keydown", handleKeydown);
  }

  function setPresentationJobs(jobs = null) {
    presentationJobs = Array.isArray(jobs) ? jobs : null;
    stopPolling();
    if (presentationJobs === null) {
      return;
    }
    renderPresentationJobs();
  }

  return { destroy, load, render, reset, setExpanded, setPresentationJobs, startPolling, stopPolling };
}
