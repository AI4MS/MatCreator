import assert from "node:assert/strict";
import test from "node:test";

import {
  createRemoteJobsController,
  normalizeRemoteJobPresentation,
  remoteJobConfiguration,
  remoteJobActionEnabled,
  remoteJobErrorSummary,
  remoteJobLifecycle,
  remoteJobProgress,
} from "../src/features/remoteJobs/RemoteJobsController.js";

class FakeClassList {
  constructor(element) {
    this.element = element;
  }

  values() {
    return new Set(this.element.className.split(/\s+/).filter(Boolean));
  }

  contains(name) {
    return this.values().has(name);
  }

  add(...names) {
    const values = this.values();
    names.forEach((name) => values.add(name));
    this.element.className = Array.from(values).join(" ");
  }

  remove(...names) {
    const values = this.values();
    names.forEach((name) => values.delete(name));
    this.element.className = Array.from(values).join(" ");
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.contains(name) : Boolean(force);
    if (enabled) this.add(name);
    else this.remove(name);
    return enabled;
  }
}

function matchesClassSelector(element, selector) {
  const classes = selector.split(".").filter(Boolean);
  return classes.length > 0 && classes.every((name) => element.classList.contains(name));
}

class FakeElement {
  constructor(ownerDocument, tagName) {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.classList = new FakeClassList(this);
    this.textContent = "";
    this.innerHTML = "";
    this.id = "";
    this.title = "";
    this.type = "";
    this.disabled = false;
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    children.forEach((child) => this.appendChild(child));
  }

  replaceChildren(...children) {
    this.children.forEach((child) => {
      child.parentElement = null;
    });
    this.children = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
    this.parentElement = null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type, overrides = {}) {
    let propagationStopped = false;
    const event = {
      key: undefined,
      currentTarget: this,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { propagationStopped = true; },
      target: this,
      ...overrides,
    };
    let current = this;
    while (current) {
      event.currentTarget = current;
      for (const listener of current.listeners.get(type) || []) listener(event);
      if (propagationStopped) break;
      current = current.parentElement;
    }
    return event;
  }

  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
  }

  querySelector(selector) {
    return this.descendants().find((element) => matchesClassSelector(element, selector)) || null;
  }

  querySelectorAll(selector) {
    return this.descendants().filter((element) => matchesClassSelector(element, selector));
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.listeners = new Map();
    this.activeElement = null;
    this.body = this.createElement("body");
    this.elements = new Map([
      ["remote-job-list", this.createElement("ul")],
      ["refresh-remote-jobs", this.createElement("button")],
      ["remote-jobs-toggle", this.createElement("button")],
      ["remote-jobs-pane", this.createElement("section")],
      ["graph-column", this.createElement("div")],
    ]);
  }

  createElement(tagName) {
    return new FakeElement(this, tagName);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type, overrides = {}) {
    const event = { key: undefined, ...overrides };
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}

function createFixture({ controllerOverrides = {}, skin = "rack-lab", windowOverrides = {} } = {}) {
  const document = new FakeDocument();
  document.body.dataset.skin = skin;
  const state = {
    activeSessionUserId: "owner-1",
    remoteJobs: [],
    sessionId: "session-1",
    userId: "owner-1",
  };
  const window = {
    clearInterval() {},
    setInterval() { return 1; },
    ...windowOverrides,
  };
  const controller = createRemoteJobsController({
    state,
    dummyMode: true,
    document,
    window,
    ...controllerOverrides,
  });
  return {
    controller,
    document,
    list: document.getElementById("remote-job-list"),
    state,
  };
}

function treeText(element) {
  return [element, ...element.descendants()].map((node) => node.textContent || "").join(" ");
}

test("normalizes remote job lifecycle labels", () => {
  assert.deepEqual(remoteJobLifecycle("RUNNING"), { key: "running", label: "Running" });
  assert.deepEqual(remoteJobLifecycle("collected"), { key: "collected", label: "Completed" });
  assert.deepEqual(remoteJobLifecycle("pause_requested"), { key: "pause_requested", label: "Pausing" });
  assert.deepEqual(remoteJobLifecycle(undefined), { key: "unknown", label: "Unknown" });
});

test("backend action and capability projections override legacy provider inference", () => {
  const legacyE2b = {
    job_id: "job-actions",
    external_id: "provider-actions",
    provider: "e2b",
    status: "running",
  };
  const deniedByActions = {
    ...legacyE2b,
    capabilities: ["pause"],
    view: { actions: { refresh: true, pause: false, terminate: false } },
  };
  assert.equal(remoteJobActionEnabled(deniedByActions, "refresh"), true);
  assert.equal(remoteJobActionEnabled(deniedByActions, "pause"), false);
  assert.equal(remoteJobActionEnabled(deniedByActions, "terminate"), false);

  const grantedByActions = {
    ...legacyE2b,
    provider: "bohr_job",
    capabilities: [],
    view: { controls: { actions: { pause: true, terminate: true } } },
  };
  assert.equal(remoteJobActionEnabled(grantedByActions, "pause"), true);
  assert.equal(remoteJobActionEnabled(grantedByActions, "terminate"), true);
  assert.equal(remoteJobActionEnabled(grantedByActions, "refresh"), false);

  assert.equal(remoteJobActionEnabled({ ...legacyE2b, capabilities: [] }, "pause"), false);
  assert.equal(remoteJobActionEnabled({
    ...legacyE2b,
    provider: "bohr_sandbox",
    capabilities: ["pause"],
  }, "pause"), true);
});

test("legacy action fallback mirrors the backend lifecycle transition matrix", () => {
  const expected = {
    submitting: [true, false, false],
    queued: [true, true, true],
    running: [true, true, true],
    pause_requested: [false, false, true],
    paused: [false, false, true],
    resume_requested: [false, false, true],
    resuming: [true, false, true],
    succeeded: [false, false, false],
    collecting: [false, false, false],
    terminate_requested: [false, false, false],
    terminated: [false, false, false],
  };
  for (const [status, [refresh, pause, terminate]] of Object.entries(expected)) {
    const job = {
      job_id: `job-${status}`,
      external_id: `provider-${status}`,
      provider: "e2b",
      status,
    };
    assert.equal(remoteJobActionEnabled(job, "refresh"), refresh, `${status} refresh`);
    assert.equal(remoteJobActionEnabled(job, "pause"), pause, `${status} pause`);
    assert.equal(remoteJobActionEnabled(job, "terminate"), terminate, `${status} terminate`);
  }
  assert.equal(remoteJobActionEnabled({
    job_id: "job-no-provider-id",
    provider: "e2b",
    status: "running",
  }, "refresh"), false);
});

test("rendered Rack Lab controls follow the backend action matrix", () => {
  const fixture = createFixture();
  fixture.controller.setPresentationJobs([
    {
      job_id: "projected-denied",
      external_id: "provider-denied",
      provider: "e2b",
      status: "running",
      view: { actions: { refresh: true, pause: false, terminate: false } },
    },
    {
      job_id: "projected-granted",
      external_id: "provider-granted",
      provider: "bohr_job",
      status: "running",
      view: { actions: { refresh: false, pause: true, terminate: true } },
    },
  ]);

  const [denied, granted] = fixture.list.children;
  assert.equal(denied.querySelector(".remote-job-refresh-button").disabled, false);
  assert.equal(denied.querySelector(".remote-job-action.pause").disabled, true);
  assert.equal(denied.querySelector(".remote-job-action.terminate").disabled, true);
  assert.equal(granted.querySelector(".remote-job-refresh-button").disabled, true);
  assert.equal(granted.querySelector(".remote-job-action.pause").disabled, false);
  assert.equal(granted.querySelector(".remote-job-action.terminate").disabled, false);
  fixture.controller.destroy();
});

test("Remote Jobs expansion asks the graph to refit before and after its height transition", () => {
  let layoutChanges = 0;
  const { controller, document } = createFixture({
    controllerOverrides: { onLayoutChanged: () => { layoutChanges += 1; } },
  });
  const pane = document.getElementById("remote-jobs-pane");

  controller.setExpanded(true);
  assert.equal(layoutChanges, 1);
  pane.dispatch("transitionend", { propertyName: "opacity" });
  assert.equal(layoutChanges, 1);
  pane.dispatch("transitionend", { propertyName: "height" });
  assert.equal(layoutChanges, 2);

  controller.destroy();
  pane.dispatch("transitionend", { propertyName: "height" });
  assert.equal(layoutChanges, 2);
});

test("reports real percentages and honest non-numeric progress states", () => {
  assert.deepEqual(
    remoteJobProgress({ status: "running", snapshot: { progress_percent: 64 } }),
    { mode: "determinate", percent: 64, shortLabel: "64%", ariaText: "Execute · 64%" },
  );
  assert.equal(remoteJobProgress({ status: "running", progress_percent: 1 }).percent, 1);
  assert.equal(remoteJobProgress({ status: "running", progress: 1 }).percent, 1);
  assert.equal(remoteJobProgress({ status: "running", snapshot: { progress: 1 } }).percent, 1);
  assert.equal(remoteJobProgress({ status: "running", progress: 64 }).percent, 64);
  assert.equal(remoteJobProgress({ status: "running", snapshot: { progress: 37 } }).percent, 37);
  assert.equal(remoteJobProgress({ status: "running", progress_fraction: 0.25 }).percent, 25);
  assert.equal(remoteJobProgress({
    status: "running",
    view: { workload_kind: "relaxation", current_phase: "execute", progress: { fraction: 0.37 } },
  }).percent, 37);
  assert.equal(remoteJobProgress({
    status: "running",
    view: {
      version: "mc.remote-job-view.v2",
      workload: { workload_kind: "relaxation", current_phase: "execute" },
      progress: { percent: 1 },
    },
  }).percent, 1);
  assert.equal(remoteJobProgress({
    status: "running",
    view: {
      version: "mc.remote-job-view.v2",
      workload: { workload_kind: "relaxation", current_phase: "execute" },
      progress: { current: 1, total: 4 },
    },
  }).percent, 25);
  assert.equal(remoteJobProgress({
    status: "running",
    view: { workload_kind: "relaxation", current_phase: "execute", progress: { fraction: 0.37 } },
    snapshot: { progress_percent: 80 },
  }).percent, 37);
  assert.equal(remoteJobProgress({
    status: "running",
    snapshot: { progress_percent: 80 },
    view: {
      version: "mc.remote-job-view.v2",
      workload: { workload_kind: "relaxation", current_phase: "execute" },
      progress: 1,
    },
  }).mode, "indeterminate");
  assert.equal(remoteJobProgress({
    status: "running",
    progress_percent: 80,
    view: {
      version: "mc.remote-job-view.v2",
      workload: { workload_kind: "relaxation", current_phase: "execute" },
      progress: { mode: "indeterminate" },
    },
  }).mode, "indeterminate");
  const omittedV2Progress = remoteJobProgress({
    status: "running",
    progress_percent: 91,
    snapshot: { task_progress: { kind: "md_steps", current: 88, total: 100, unit: "steps" } },
    view: {
      version: "mc.remote-job-view.v2",
      workload: { workload_kind: "md", current_phase: "execute" },
    },
  });
  assert.equal(omittedV2Progress.mode, "indeterminate");
  assert.equal(omittedV2Progress.percent, null);
  for (const invalidProgress of [true, "37", -1, 101, Number.POSITIVE_INFINITY]) {
    assert.equal(remoteJobProgress({ status: "running", progress: invalidProgress }).mode, "indeterminate");
  }
  assert.equal(remoteJobProgress({
    status: "running",
    progress: 0.37,
    view: { workload_kind: "relaxation", current_phase: "execute", show_progress: false },
  }).mode, "hidden");
  assert.deepEqual(
    remoteJobProgress({ status: "running", snapshot: { provider_status: "running" } }),
    { mode: "indeterminate", percent: null, shortLabel: "Live", ariaText: "Execute · exact progress unavailable" },
  );
  assert.equal(remoteJobProgress({ status: "paused" }).mode, "paused");
  assert.deepEqual(
    remoteJobProgress({ status: "queued", progress_percent: 88 }),
    { mode: "hidden", percent: null, shortLabel: "", ariaText: "Queue · progress is not applicable to this stage" },
  );
  assert.equal(remoteJobProgress({ status: "succeeded" }).mode, "hidden");
  assert.equal(remoteJobProgress({ status: "succeeded" }).percent, null);
  assert.equal(remoteJobProgress({ status: "collected" }).mode, "hidden");
  assert.equal(remoteJobProgress({ status: "collected" }).percent, null);
  assert.equal(remoteJobProgress({ status: "failed" }).mode, "hidden");
});

test("uses durable task progress but never presents waiting or stale telemetry as a percentage", () => {
  assert.deepEqual(
    remoteJobProgress({
      status: "running",
      snapshot: { task_progress: { current: 3, total: 8, unit: "ionic steps" } },
    }),
    {
      mode: "determinate",
      percent: 38,
      shortLabel: "38%",
      ariaText: "Execute · 38%",
      unit: "ionic steps",
    },
  );

  for (const [progressStatus, shortLabel, ariaText] of [
    ["waiting", "Waiting", "Execute · waiting for a progress update"],
    ["stale", "Stale", "Execute · last progress update is stale"],
    ["invalid", "Telemetry", "Execute · latest progress update was invalid"],
    ["unavailable", "Offline", "Execute · progress telemetry is temporarily unavailable"],
  ]) {
    assert.deepEqual(
      remoteJobProgress({
        status: "running",
        snapshot: {
          progress_status: progressStatus,
          task_progress: { current: 7, total: 8, unit: "ionic steps" },
        },
      }),
      { mode: "indeterminate", percent: null, shortLabel, ariaText },
    );
  }

  assert.deepEqual(
    remoteJobProgress({
      status: "running",
      snapshot: {
        progress_status: "failed",
        task_progress: { current: 7, total: 8, unit: "ionic steps" },
      },
    }),
    {
      mode: "failed",
      percent: null,
      shortLabel: "Failed",
      ariaText: "Execute · workload failed; remote resource status is reported separately",
    },
  );
});

test("labels relaxation counters as budget use and preserves their typed semantics", () => {
  for (const [kind, unit, axis, countUnit] of [
    ["iteration", "iteration", "iteration", "iterations"],
    ["relaxation_steps", "ionic steps", "step", "ionic steps"],
  ]) {
    assert.deepEqual(
      remoteJobProgress({
        status: "running",
        view: { workload_kind: "relaxation", current_phase: "execute" },
        snapshot: { task_progress: { kind, current: 3, total: 8, unit } },
      }),
      {
        mode: "determinate",
        percent: 38,
        shortLabel: "38% budget",
        ariaText: `Relax · 38% of ${axis} budget used, 3 of 8 ${countUnit}`,
        kind,
        unit,
      },
    );
  }
});

test("shows a workload finished state without inventing a missing counter percentage", () => {
  assert.deepEqual(
    remoteJobProgress({
      status: "running",
      provider: "bohr_sandbox",
      view: { workload_kind: "relaxation", current_phase: "execute" },
      snapshot: { progress_status: "finished", workload: { state: "finished" } },
    }),
    {
      mode: "finished",
      percent: null,
      shortLabel: "Finished",
      ariaText: "Workload finished · no exact iteration-budget sample was recorded",
    },
  );
});

test("v2 workload projection never falls back to stale raw terminal state", () => {
  const job = {
    status: "running",
    provider: "bohr_sandbox",
    view: {
      version: "mc.remote-job-view.v2",
      workload: {
        workload_kind: "relaxation",
        current_phase: "execute",
        state: "running",
      },
    },
    snapshot: {
      progress_status: "failed",
      workload: { state: "failed", exit_code: 2 },
    },
  };

  const presentation = normalizeRemoteJobPresentation(job);
  assert.equal(presentation.workloadState, "");
  assert.equal(presentation.currentLabel, "Relax");
  assert.deepEqual(
    remoteJobProgress(job),
    {
      mode: "indeterminate",
      percent: null,
      shortLabel: "Live",
      ariaText: "Relax · exact progress unavailable",
    },
  );
});

test("normalizes typed phase plans with presentation precedence and safe legacy fallbacks", () => {
  const preferred = normalizeRemoteJobPresentation({
    status: "running",
    view: {
      workload_kind: "md",
      current_phase: "simulate",
      title: "NVT production",
    },
    presentation: { workload_kind: "vasp", current_phase: "solve" },
    specification: { presentation: { workload_kind: "training", current_phase: "train" } },
  });
  assert.equal(preferred.kind, "md");
  assert.equal(preferred.kindLabel, "MD");
  assert.equal(preferred.title, "NVT production");
  assert.equal(preferred.currentPhase, "execute");
  assert.equal(preferred.currentLabel, "Simulate");
  assert.equal(preferred.phaseIndex, 3);
  assert.equal(preferred.showsExecutionProgress, true);

  assert.equal(normalizeRemoteJobPresentation({
    status: "running",
    presentation: { workload_kind: "vasp", current_phase: "solve" },
  }).currentLabel, "Solve");
  assert.equal(normalizeRemoteJobPresentation({
    status: "running",
    specification: { presentation: { workload_kind: "training", current_phase: "train" } },
  }).currentLabel, "Train");
  assert.equal(normalizeRemoteJobPresentation({ status: "running" }).currentLabel, "Execute");

  const upstreamRelaxation = normalizeRemoteJobPresentation({
    status: "running",
    specification: {
      workload_kind: "relaxation",
      task_type: "Structure relaxation",
      template: "deepmd",
      presentation: {
        workload_kind: "relaxation",
        current_phase: "execute",
        phase_plan: [
          { id: "prepare", label: "Prepare relaxation inputs" },
          { id: "submit", label: "Provision relaxation runtime" },
          { id: "queue", label: "Stage relaxation inputs" },
          { id: "execute", label: "Relax structure", progress_applicable: true },
          { id: "collect", label: "Collect relaxed structure" },
          { id: "validate", label: "Verify convergence" },
        ],
      },
    },
  });
  assert.equal(upstreamRelaxation.kind, "relaxation");
  assert.equal(upstreamRelaxation.kindLabel, "Relaxation");
  assert.equal(upstreamRelaxation.currentLabel, "Relax structure");
  assert.equal(upstreamRelaxation.showsProgress, true);
  assert.deepEqual(
    upstreamRelaxation.phases.map(({ id, label }) => [id, label]),
    [
      ["prepare", "Prepare relaxation inputs"],
      ["submit", "Provision relaxation runtime"],
      ["queue", "Stage relaxation inputs"],
      ["execute", "Relax structure"],
      ["collect", "Collect relaxed structure"],
      ["validate", "Verify convergence"],
    ],
  );

  const legacyRelaxation = normalizeRemoteJobPresentation({
    status: "running",
    job_name: "structure-relaxation",
    specification: { template: "deepmd" },
  });
  assert.equal(legacyRelaxation.kind, "relaxation");
  assert.equal(legacyRelaxation.currentLabel, "Relax");

  const nestedV2Relaxation = normalizeRemoteJobPresentation({
    status: "running",
    job_name: "vasp-training-md-misleading",
    view: {
      version: "mc.remote-job-view.v2",
      workload: {
        workload_kind: "relaxation",
        current_phase: "execution",
        phase_label: "Optimize geometry",
        phase_plan: [
          { phase: "execution", label: "Optimize geometry", progress_applicable: true },
        ],
      },
    },
  });
  assert.equal(nestedV2Relaxation.schemaVersion, 2);
  assert.equal(nestedV2Relaxation.kind, "relaxation");
  assert.equal(nestedV2Relaxation.currentPhase, "execute");
  assert.equal(nestedV2Relaxation.currentLabel, "Optimize geometry");
  assert.equal(nestedV2Relaxation.showsProgress, true);

  assert.equal(normalizeRemoteJobPresentation({
    status: "running",
    job_name: "deepmd-training-misleading",
    view: { version: "mc.remote-job-view.v2", workload: { workload_kind: "future-kind" } },
  }).kind, "generic");
  assert.equal(normalizeRemoteJobPresentation({
    status: "running",
    workload_kind: "relaxation",
    specification: { task_type: "relaxation" },
    view: {
      version: "mc.remote-job-view.v2",
      workload: { current_phase: "execute" },
    },
  }).kind, "generic");

  const authoritativeLifecycle = normalizeRemoteJobPresentation({
    status: "failed",
    external_id: "sandbox-authoritative",
    view: {
      version: "mc.remote-job-view.v2",
      lifecycle: { status: "running" },
      workload: { workload_kind: "relaxation", current_phase: "execute" },
    },
  });
  assert.equal(authoritativeLifecycle.showsProgress, true);
  assert.equal(authoritativeLifecycle.phases[authoritativeLifecycle.phaseIndex].state, "active");

  const customPlan = normalizeRemoteJobPresentation({
    status: "running",
    view: {
      workload_kind: "md",
      current_phase: "simulation",
      phase_plan: [
        { id: "preparing", label: "Stage inputs" },
        { id: "simulation", label: "Integrate trajectory" },
        { id: "validating", label: "Audit outputs" },
      ],
    },
  });
  assert.deepEqual(
    customPlan.phases.map(({ id, label }) => [id, label]),
    [["prepare", "Stage inputs"], ["execute", "Integrate trajectory"], ["validate", "Audit outputs"]],
  );
  assert.equal(customPlan.currentLabel, "Integrate trajectory");

  const canonicalContract = normalizeRemoteJobPresentation({
    status: "running",
    view: {
      workload_kind: "md",
      current_phase: "execution",
      phase_plan: [
        { phase: "preparation", label: "Prepare" },
        { phase: "provisioning", label: "Provision" },
        { phase: "input_staging", label: "Stage inputs" },
        { phase: "execution", label: "Run MD", progress_applicable: true },
        { phase: "validation", label: "Validate" },
        { phase: "collection", label: "Collect" },
      ],
    },
  });
  assert.deepEqual(
    canonicalContract.phases.map(({ id, label }) => [id, label]),
    [
      ["prepare", "Prepare"], ["submit", "Provision"], ["queue", "Stage inputs"],
      ["execute", "Run MD"], ["validate", "Validate"], ["collect", "Collect"],
    ],
  );
  assert.equal(canonicalContract.currentPhase, "execute");
  assert.equal(canonicalContract.showsExecutionProgress, true);
  assert.equal(canonicalContract.showsProgress, true);
  assert.equal(canonicalContract.phases[3].progressApplicable, true);

  const publicProjection = normalizeRemoteJobPresentation({
    status: "running",
    view: {
      workload_kind: "vasp",
      current_phase: "execute",
      phase_label: "Relaxation",
      show_progress: false,
      phase_plan: [
        { key: "prepare", label: "Prepare", progress_applicable: false },
        { key: "execute", label: "Relaxation", progress_applicable: true },
      ],
    },
  });
  assert.equal(publicProjection.currentLabel, "Relaxation");
  assert.equal(publicProjection.phases[1].progressApplicable, true);
  assert.equal(publicProjection.phaseAllowsProgress, false);
  assert.equal(publicProjection.showsProgress, false);

  const lifecyclePhases = [
    ["created", "prepare"],
    ["submitting", "submit"],
    ["queued", "queue"],
    ["running", "execute"],
    ["collecting", "collect"],
    ["collected", "collect"],
  ];
  lifecyclePhases.forEach(([status, phase]) => {
    assert.equal(normalizeRemoteJobPresentation({ status }).currentPhase, phase);
  });

  assert.equal(normalizeRemoteJobPresentation({
    status: "queued",
    job_name: "lammps-npt-production",
  }).kind, "md");
  assert.equal(normalizeRemoteJobPresentation({
    status: "running",
    job_name: "ordinary-task",
    specification: {
      command: "dp train input.json",
      env: { WORKLOAD_KIND: "vasp" },
    },
  }).kind, "generic");
});

test("configuration rows are allowlisted and never reveal command or environment values", () => {
  const rows = remoteJobConfiguration({
    provider: "bohr_sandbox",
    node_id: "step-relax",
    specification: {
      api_key: "not-visible",
      command: "curl https://example.invalid/?token=not-visible",
      env: { SECRET_TOKEN: "not-visible" },
      template: "dpa4-template",
      timeout: 7200,
    },
  });
  const values = rows.map(({ value }) => value).join(" ");

  assert.equal(rows.find(({ label }) => label === "Template")?.value, "dpa4-template");
  assert.equal(rows.find(({ label }) => label === "Command")?.value, "Configured");
  assert.equal(rows.find(({ label }) => label === "Environment")?.value, "1 variable");
  assert.doesNotMatch(values, /not-visible|curl|SECRET_TOKEN/);
});

test("provider errors use a fixed summary instead of exposing command arguments", () => {
  const unsafeError = "bohr command failed: --env SECRET_TOKEN=not-visible --api-key not-visible";
  const summary = remoteJobErrorSummary(unsafeError);
  const fixture = createFixture();

  fixture.controller.setPresentationJobs([{
    job_id: "unsafe-error-fixture",
    provider: "bohr_sandbox",
    status: "failed",
    error: unsafeError,
  }]);
  const renderedDetails = fixture.list.children[0].querySelector(".remote-job-face-details");

  assert.equal(summary, "Provider error · details hidden for safety");
  assert.doesNotMatch(summary, /SECRET_TOKEN|not-visible|api-key|--env/);
  assert.match(treeText(renderedDetails), /Provider error · details hidden for safety/);
  assert.doesNotMatch(treeText(renderedDetails), /SECRET_TOKEN|not-visible|api-key|--env/);
  assert.equal(remoteJobErrorSummary(""), "");
  fixture.controller.destroy();
});

test("remote job summaries open from the whole surface while details close from a dedicated control", async () => {
  const fixture = createFixture();
  await fixture.controller.load("session-1", "owner-1");

  const card = fixture.list.children[0];
  const summary = card.querySelector(".remote-job-face-summary");
  const details = card.querySelector(".remote-job-face-details");
  const toggle = card.querySelector(".remote-job-card-toggle");
  const detailsBack = card.querySelector(".remote-job-details-back");
  const refresh = card.querySelector(".remote-job-refresh-button");
  const progress = summary.querySelector(".remote-job-progress");
  const stageViewport = summary.querySelector(".remote-job-stage-viewport");

  assert.equal(toggle.type, "button");
  assert.equal(toggle.tagName, "BUTTON");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.getAttribute("aria-controls"), details.id);
  assert.equal(toggle.getAttribute("aria-label"), "Show details for sandbox-demo-running");
  assert.equal(refresh.type, "button");
  assert.equal(refresh.getAttribute("aria-label"), "Refresh status for sandbox-demo-running");
  assert.equal(summary.getAttribute("aria-hidden"), "false");
  assert.equal(summary.hasAttribute("inert"), false);
  assert.equal(details.getAttribute("aria-hidden"), "true");
  assert.equal(details.hasAttribute("inert"), true);
  assert.equal(summary.querySelector(".remote-job-id").textContent, "sandbox-demo-running");
  assert.equal(summary.querySelector(".remote-job-identity-label").textContent, "Sandbox");
  assert.match(treeText(summary), /Sandbox|Running|42%/);
  assert.equal(summary.querySelector(".remote-job-provider"), null);
  assert.equal(summary.querySelector(".remote-job-detail-row"), null);
  assert.equal(stageViewport.getAttribute("role"), "status");
  assert.equal(stageViewport.getAttribute("aria-live"), "polite");
  assert.equal(stageViewport.getAttribute("aria-label"), "Generic task stage: Execute, 4 of 6");
  assert.equal(stageViewport.querySelector(".remote-job-stage-track").dataset.stageIndex, "3");
  assert.equal(progress.getAttribute("aria-valuenow"), "42");
  assert.equal(progress.getAttribute("aria-valuetext"), "Execute · 42%");
  assert.match(treeText(details), /Task config|demo-template|2 h|1 variable/);
  assert.doesNotMatch(treeText(details), /never-render-this-value/);

  summary.dispatch("click");

  assert.equal(card.classList.contains("is-flipped"), true);
  assert.equal(fixture.list.classList.contains("has-expanded-card"), true);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(toggle.getAttribute("aria-label"), "Show details for sandbox-demo-running");
  assert.equal(summary.getAttribute("aria-hidden"), "true");
  assert.equal(summary.hasAttribute("inert"), true);
  assert.equal(details.getAttribute("aria-hidden"), "false");
  assert.equal(details.hasAttribute("inert"), false);
  assert.equal(details.querySelectorAll(".remote-job-action").length, 3);
  assert.deepEqual(
    details.querySelectorAll(".remote-job-action").map((button) => button.getAttribute("aria-label")),
    ["Return to summary for sandbox-demo-running", "Pause job", "Terminate job"],
  );

  details.dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), true);

  detailsBack.dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), false);
  assert.equal(fixture.list.classList.contains("has-expanded-card"), false);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");

  refresh.dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fixture.list.children[0].classList.contains("is-flipped"), false);
  assert.equal(fixture.list.children[0].querySelector(".remote-job-refresh-button") !== null, true);
  fixture.controller.destroy();
});

test("Rack Lab alone renders sharp-content liquid-glass layers without a pointer hotspot", async () => {
  const rackFixture = createFixture({ skin: "rack-lab" });
  await rackFixture.controller.load("session-1", "owner-1");
  const rackCard = rackFixture.list.children[0];
  const summary = rackCard.querySelector(".remote-job-face-summary");
  const details = rackCard.querySelector(".remote-job-face-details");

  assert.equal(rackCard.dataset.visualMaterial, "liquid-glass");
  [summary, details].forEach((face) => {
    assert.equal(face.querySelectorAll(".remote-job-glass-warp").length, 1);
    assert.equal(face.querySelectorAll(".remote-job-glass-edge").length, 1);
    assert.equal(face.querySelector(".remote-job-glass-warp").getAttribute("aria-hidden"), "true");
    assert.equal(face.querySelector(".remote-job-glass-edge").getAttribute("aria-hidden"), "true");
  });
  assert.equal(
    rackFixture.document.body.children.filter(
      (child) => child.getAttribute("class") === "rack-remote-job-liquid-glass-defs",
    ).length,
    1,
  );

  rackCard.getBoundingClientRect = () => ({ left: 100, top: 40, width: 200, height: 100 });
  rackCard.dispatch("pointermove", { clientX: 250, clientY: 65 });
  assert.equal(rackCard.classList.contains("is-liquid-glass-engaged"), true);
  assert.equal(rackCard.style["--rack-glass-x"], undefined);
  assert.equal(rackCard.style["--rack-glass-y"], undefined);
  assert.equal(rackCard.style["--rack-glass-tilt-y"], "1.05deg");

  rackCard.dispatch("pointerout", { relatedTarget: null });
  assert.equal(rackCard.classList.contains("is-liquid-glass-engaged"), false);
  assert.equal(rackCard.style["--rack-glass-x"], undefined);
  assert.equal(rackCard.style["--rack-glass-tilt-y"], "0deg");

  rackFixture.document.body.dataset.skin = "matcreator-default";
  rackFixture.controller.render();
  assert.equal(rackFixture.list.children[0].querySelector(".remote-job-glass-warp"), null);
  rackFixture.document.body.dataset.skin = "rack-lab";
  rackFixture.controller.render();
  const rerenderedRackCard = rackFixture.list.children[0];
  [
    rerenderedRackCard.querySelector(".remote-job-face-summary"),
    rerenderedRackCard.querySelector(".remote-job-face-details"),
  ].forEach((face) => {
    assert.equal(face.querySelectorAll(".remote-job-glass-warp").length, 1);
    assert.equal(face.querySelectorAll(".remote-job-glass-edge").length, 1);
  });
  assert.equal(
    rackFixture.document.body.children.filter(
      (child) => child.getAttribute("class") === "rack-remote-job-liquid-glass-defs",
    ).length,
    1,
  );
  rackFixture.controller.destroy();
  assert.equal(
    rackFixture.document.body.children.filter(
      (child) => child.getAttribute("class") === "rack-remote-job-liquid-glass-defs",
    ).length,
    0,
  );

  const defaultFixture = createFixture({ skin: "matcreator-default" });
  await defaultFixture.controller.load("session-1", "owner-1");
  const defaultCard = defaultFixture.list.children[0];
  assert.equal(defaultCard.dataset.visualMaterial, undefined);
  assert.equal(defaultCard.querySelector(".remote-job-glass-warp"), null);
  assert.equal(
    defaultFixture.document.body.children.some(
      (child) => child.getAttribute("class") === "rack-remote-job-liquid-glass-defs",
    ),
    false,
  );
  defaultFixture.controller.destroy();
});

test("the projected stage contract alone controls integrated progress", () => {
  const fixture = createFixture();
  fixture.controller.setPresentationJobs([
    { job_id: "queued", status: "queued", progress_percent: 76, view: { workload_kind: "md" } },
    { job_id: "preparing", status: "running", progress_percent: 76, view: { workload_kind: "md", current_phase: "preparing" } },
    { job_id: "collecting", status: "collecting", progress_percent: 76, view: { workload_kind: "vasp", current_phase: "collecting" } },
    { job_id: "validating", status: "running", progress_percent: 76, view: { workload_kind: "training", current_phase: "validating" } },
    { job_id: "collected", status: "collected", progress_percent: 100, view: { workload_kind: "generic", current_phase: "collected" } },
    {
      job_id: "explicitly-disabled-relaxation",
      status: "running",
      view: {
        workload_kind: "vasp",
        current_phase: "execute",
        phase_label: "Relaxation",
        show_progress: false,
        progress: { current: 37, total: 100 },
      },
    },
    {
      job_id: "relaxing",
      status: "running",
      view: {
        workload_kind: "vasp",
        current_phase: "execute",
        phase_label: "Relaxation",
        show_progress: true,
        phase_plan: [
          { key: "prepare", label: "Prepare", progress_applicable: false },
          { key: "execute", label: "Relaxation", progress_applicable: true },
          { key: "validate", label: "Verify", progress_applicable: false },
        ],
        progress: { current: 37, total: 100, unit: "ionic steps" },
      },
    },
  ]);

  fixture.list.children.slice(0, 6).forEach((card) => {
    assert.equal(card.querySelector(".remote-job-progress"), null);
    assert.equal(card.querySelector(".remote-job-face-summary").classList.contains("has-execution-progress"), false);
  });
  const relaxationCard = fixture.list.children[6];
  assert.equal(relaxationCard.dataset.workloadKind, "vasp");
  assert.equal(relaxationCard.dataset.currentPhase, "execute");
  assert.equal(relaxationCard.querySelector(".remote-job-stage-viewport").getAttribute("aria-label"), "VASP task stage: Relaxation, 2 of 3");
  assert.equal(relaxationCard.querySelector(".remote-job-progress").getAttribute("aria-valuenow"), "37");
  assert.equal(relaxationCard.querySelector(".remote-job-face-summary").classList.contains("has-execution-progress"), true);
  fixture.controller.destroy();
});

test("Rack Lab running cards render task progress while waiting and stale snapshots stay non-numeric", () => {
  const fixture = createFixture({ skin: "rack-lab" });
  fixture.controller.setPresentationJobs([
    {
      job_id: "rack-task-progress",
      external_id: "sandbox-rack-progress",
      provider: "bohr_sandbox",
      status: "running",
      view: { workload_kind: "md", current_phase: "execute", show_progress: true },
      snapshot: {
        provider_status: "reachable",
        task_progress: { current: 3, total: 8, unit: "steps" },
      },
    },
    ...["waiting", "stale"].map((progressStatus) => ({
      job_id: `rack-task-${progressStatus}`,
      external_id: `sandbox-rack-${progressStatus}`,
      provider: "bohr_sandbox",
      status: "running",
      view: { workload_kind: "md", current_phase: "execute", show_progress: true },
      snapshot: {
        provider_status: "reachable",
        progress_status: progressStatus,
        task_progress: { current: 7, total: 8, unit: "steps" },
      },
    })),
  ]);

  const determinate = fixture.list.children[0].querySelector(".remote-job-progress");
  assert.equal(determinate.dataset.progressMode, "determinate");
  assert.equal(determinate.getAttribute("aria-valuenow"), "38");
  assert.equal(determinate.getAttribute("aria-valuetext"), "Simulate · 38%");
  assert.equal(determinate.querySelector(".remote-job-progress-fill").style.width, "38%");
  assert.match(treeText(fixture.list.children[0]), /Running|38%/);

  for (const [index, progressStatus] of ["waiting", "stale"].entries()) {
    const card = fixture.list.children[index + 1];
    const progress = card.querySelector(".remote-job-progress");
    assert.equal(progress.dataset.progressMode, "indeterminate");
    assert.equal(progress.getAttribute("aria-valuenow"), null);
    assert.equal(progress.querySelector(".remote-job-progress-fill").style.width, undefined);
    assert.match(treeText(card), new RegExp(progressStatus, "i"));
    assert.doesNotMatch(treeText(card), /88%/);
  }
  fixture.controller.destroy();
});

test("Rack Lab separates a live Sandbox resource from terminal workload state", () => {
  const fixture = createFixture({ skin: "rack-lab" });
  const baseJob = {
    provider: "bohr_sandbox",
    status: "running",
    view: { workload_kind: "relaxation", current_phase: "execute", show_progress: true },
  };
  fixture.controller.setPresentationJobs([
    {
      ...baseJob,
      job_id: "rack-workload-failed",
      external_id: "sandbox-workload-failed",
      snapshot: {
        provider_status: "reachable",
        progress_status: "failed",
        workload: { state: "failed", exit_code: 2 },
      },
    },
    {
      ...baseJob,
      job_id: "rack-workload-finished-budget",
      external_id: "sandbox-workload-finished-budget",
      snapshot: {
        provider_status: "reachable",
        progress_status: "finished",
        workload: { state: "finished", exit_code: 0 },
        task_progress: { kind: "iteration", current: 3, total: 8, unit: "iteration" },
      },
    },
    {
      ...baseJob,
      job_id: "rack-workload-finished-no-sample",
      external_id: "sandbox-workload-finished-no-sample",
      snapshot: {
        provider_status: "reachable",
        progress_status: "finished",
        workload: { state: "finished", exit_code: 0 },
      },
    },
  ]);

  const failedCard = fixture.list.children[0];
  assert.equal(failedCard.classList.contains("status-running"), true);
  assert.equal(failedCard.dataset.workloadState, "failed");
  assert.match(treeText(failedCard.querySelector(".remote-job-status")), /Sandbox Running/);
  assert.equal(
    failedCard.querySelector(".remote-job-status").getAttribute("aria-label"),
    "Sandbox resource Running; workload failed",
  );
  assert.match(treeText(failedCard.querySelector(".remote-job-stage-viewport")), /Workload failed/);
  assert.match(
    failedCard.querySelector(".remote-job-stage-viewport").getAttribute("aria-label"),
    /task stage: Workload failed/,
  );
  assert.equal(failedCard.querySelector(".remote-job-progress").dataset.progressMode, "failed");
  assert.equal(failedCard.querySelector(".remote-job-progress").getAttribute("role"), "status");
  assert.match(treeText(failedCard), /Failed/);

  const budgetCard = fixture.list.children[1];
  const budgetProgress = budgetCard.querySelector(".remote-job-progress");
  assert.equal(budgetCard.classList.contains("status-running"), true);
  assert.equal(budgetCard.dataset.workloadState, "finished");
  assert.match(treeText(budgetCard), /Sandbox Running/);
  assert.match(treeText(budgetCard), /Workload finished/);
  assert.match(treeText(budgetCard), /38% budget/);
  assert.equal(budgetProgress.dataset.progressMode, "determinate");
  assert.equal(budgetProgress.dataset.progressKind, "iteration");
  assert.equal(budgetProgress.dataset.progressUnit, "iteration");
  assert.equal(budgetProgress.getAttribute("aria-valuenow"), "38");
  assert.equal(
    budgetProgress.getAttribute("aria-valuetext"),
    "Workload finished · 38% of iteration budget used, 3 of 8 iterations",
  );

  const finishedCard = fixture.list.children[2];
  const finishedProgress = finishedCard.querySelector(".remote-job-progress");
  assert.equal(finishedCard.classList.contains("status-running"), true);
  assert.equal(finishedCard.dataset.workloadState, "finished");
  assert.match(treeText(finishedCard), /Sandbox Running/);
  assert.match(treeText(finishedCard), /Workload finished/);
  assert.match(treeText(finishedCard), /Finished/);
  assert.equal(finishedProgress.dataset.progressMode, "finished");
  assert.equal(finishedProgress.getAttribute("role"), "status");
  assert.equal(finishedProgress.getAttribute("aria-valuenow"), null);
  assert.equal(
    finishedProgress.getAttribute("aria-valuetext"),
    "Workload finished · no exact iteration-budget sample was recorded",
  );
  fixture.controller.destroy();
});

test("phase updates roll the stage track while reduced motion jumps directly", () => {
  const animationFrames = [];
  const fixture = createFixture({
    windowOverrides: {
      matchMedia: () => ({ matches: false }),
      requestAnimationFrame(callback) {
        animationFrames.push(callback);
        return animationFrames.length;
      },
    },
  });
  const renderPhase = (currentPhase) => fixture.controller.setPresentationJobs([{
    job_id: "rolling-md",
    status: currentPhase === "queued" ? "queued" : "running",
    view: { workload_kind: "md", current_phase: currentPhase },
  }]);

  renderPhase("queued");
  assert.equal(fixture.list.children[0].querySelector(".remote-job-stage-track").dataset.stageIndex, "2");
  assert.equal(animationFrames.length, 0);

  renderPhase("simulate");
  const rollingTrack = fixture.list.children[0].querySelector(".remote-job-stage-track");
  assert.equal(rollingTrack.dataset.stageIndex, "2");
  assert.equal(animationFrames.length, 1);
  animationFrames.shift()();
  assert.equal(rollingTrack.dataset.stageIndex, "3");
  assert.equal(rollingTrack.style.transform, "translateY(-54px)");
  fixture.controller.destroy();

  let reducedFrameCount = 0;
  const reducedFixture = createFixture({
    windowOverrides: {
      matchMedia: () => ({ matches: true }),
      requestAnimationFrame() {
        reducedFrameCount += 1;
        return reducedFrameCount;
      },
    },
  });
  reducedFixture.controller.setPresentationJobs([{
    job_id: "static-md",
    status: "queued",
    view: { workload_kind: "md", current_phase: "queued" },
  }]);
  reducedFixture.controller.setPresentationJobs([{
    job_id: "static-md",
    status: "running",
    view: { workload_kind: "md", current_phase: "simulate" },
  }]);
  assert.equal(reducedFrameCount, 0);
  assert.equal(
    reducedFixture.list.children[0].querySelector(".remote-job-stage-track").dataset.stageIndex,
    "3",
  );
  reducedFixture.controller.destroy();
});

test("native keyboard flip control and job actions do not double-toggle the card", async () => {
  const fixture = createFixture();
  await fixture.controller.load("session-1", "owner-1");

  const card = fixture.list.children[0];
  const toggle = card.querySelector(".remote-job-card-toggle");
  const enterEvent = toggle.dispatch("keydown", { key: "Enter" });
  assert.equal(card.classList.contains("is-flipped"), true);
  assert.equal(enterEvent.defaultPrevented, true);

  const spaceEvent = toggle.dispatch("keydown", { key: " " });
  assert.equal(card.classList.contains("is-flipped"), true);
  assert.equal(spaceEvent.defaultPrevented, true);

  card.querySelector(".remote-job-details-back").dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), false);

  toggle.dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), true);

  card.querySelector(".remote-job-action.pause").dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), true);

  card.querySelector(".remote-job-action.terminate").dispatch("click");
  assert.equal(card.classList.contains("is-flipped"), true);
  fixture.controller.destroy();
});

test("flip state survives polling-style rerenders and reset clears it", async () => {
  const fixture = createFixture();
  await fixture.controller.load("session-1", "owner-1");

  const originalToggle = fixture.list.children[0].querySelector(".remote-job-card-toggle");
  originalToggle.focus();
  originalToggle.dispatch("click");
  fixture.state.remoteJobs[0].status = "paused";
  fixture.controller.render();

  assert.equal(fixture.list.children[0].classList.contains("is-flipped"), true);
  assert.equal(fixture.list.children[1].classList.contains("is-flipped"), false);
  assert.equal(fixture.list.classList.contains("has-expanded-card"), true);
  assert.notEqual(fixture.document.activeElement, originalToggle);
  assert.equal(fixture.document.activeElement, fixture.list.children[0].querySelector(".remote-job-card-toggle"));

  fixture.controller.reset();
  await fixture.controller.load("session-1", "owner-1");
  assert.equal(fixture.list.children[0].classList.contains("is-flipped"), false);
  assert.equal(fixture.list.classList.contains("has-expanded-card"), false);
  fixture.controller.destroy();
});

test("focus falls back to the whole-card flip control when a refreshed action becomes disabled", async () => {
  const fixture = createFixture();
  await fixture.controller.load("session-1", "owner-1");
  const card = fixture.list.children[0];
  card.querySelector(".remote-job-card-toggle").dispatch("click");
  card.querySelector(".remote-job-action.pause").focus();

  fixture.state.remoteJobs[0].status = "paused";
  fixture.controller.render();

  const refreshedCard = fixture.list.children[0];
  assert.equal(refreshedCard.classList.contains("is-flipped"), true);
  assert.equal(
    fixture.document.activeElement,
    refreshedCard.querySelector(".remote-job-card-toggle"),
  );
  fixture.controller.destroy();
});

test("Escape returns the visible card to its summary and keeps whole-card toggle focus", async () => {
  const fixture = createFixture();
  await fixture.controller.load("session-1", "owner-1");
  const card = fixture.list.children[0];
  const toggle = card.querySelector(".remote-job-card-toggle");
  toggle.dispatch("click");

  fixture.document.dispatch("keydown", { key: "Escape" });

  assert.equal(card.classList.contains("is-flipped"), false);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(fixture.document.activeElement, toggle);
  fixture.controller.destroy();
});

test("latest provider identity is visible and unsupported pause is capability-gated", () => {
  const fixture = createFixture();
  fixture.controller.setPresentationJobs([{
    job_id: "mc-job-42",
    external_id: "bohr-batch-314",
    provider: "bohr_job",
    status: "running",
    snapshot: { provider_status: "RUNNING" },
  }]);

  const card = fixture.list.children[0];
  const rows = card.querySelectorAll(".remote-job-detail-row");
  const rowText = rows
    .map((row) => `${row.children[0]?.textContent}${row.children[1]?.textContent}`)
    .join(" | ");
  assert.match(rowText, /Job IDmc-job-42/);
  assert.match(rowText, /Provider IDbohr-batch-314/);
  assert.match(rowText, /Provider statusRUNNING/);
  assert.equal(card.querySelector(".remote-job-action.pause").disabled, true);
  assert.equal(card.querySelector(".remote-job-action.terminate").disabled, false);
  fixture.controller.destroy();
});
