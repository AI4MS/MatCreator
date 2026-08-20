import assert from "node:assert/strict";
import test from "node:test";

import { createEvaluationController } from "../src/features/evaluation/EvaluationController.js";

function fakeElement() {
  const classes = new Set();
  const attributes = new Map();
  const listeners = new Map();
  return {
    classes,
    attributes,
    listeners,
    classList: {
      toggle(name, force) {
        if (force) classes.add(name);
        else classes.delete(name);
      },
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    addEventListener(name, listener) {
      listeners.set(name, listener);
    },
  };
}

test("application mode owns evaluation polling and workspace visibility", async () => {
  const elements = new Map([
    ["workspace-mode-btn", fakeElement()],
    ["evaluation-mode-btn", fakeElement()],
    ["evaluation-pane", fakeElement()],
    ["tab-evaluation", fakeElement()],
    ["evaluation-tab-panel", fakeElement()],
  ]);
  const workspacePanes = [fakeElement(), fakeElement(), fakeElement()];
  const activatedTabs = [];
  const fetches = [];
  const intervals = [];
  const clearedIntervals = [];
  const state = {
    userId: "",
    appMode: "workspace",
    evaluationCatalog: [],
    evaluationCatalogTotal: null,
    evaluationQuestionSets: [],
    evaluationGeneratedQuestions: [],
    evaluationQuestionTemplates: [],
    evaluationQuestionGenerators: [],
    activeEvaluationQuestionTemplateId: "default",
    activeEvaluationQuestionGeneratorId: "",
    activeEvaluationQuestionSetId: "",
    selectedEvaluationQuestions: new Set(),
    activeEvaluationCampaign: null,
  };

  const controller = createEvaluationController({
    state,
    activateCenterTab: (tabId) => activatedTabs.push(tabId),
    switchSession: () => {},
    removeOverlayWithMotion: () => Promise.resolve(),
    document: {
      getElementById: (id) => elements.get(id) || null,
      querySelectorAll: () => workspacePanes,
    },
    fetch: async (url) => {
      fetches.push(url);
      return {
        ok: true,
        json: async () => ({ questions: [], facets: {}, generators: [] }),
      };
    },
    setInterval: (callback, delay) => {
      intervals.push({ callback, delay });
      return intervals.length;
    },
    clearInterval: (intervalId) => clearedIntervals.push(intervalId),
    FormData,
    URLSearchParams,
  });

  assert.equal(typeof controller.showSessionQuestionGeneratorPicker, "function");
  controller.setApplicationMode("evaluation");
  await Promise.resolve();

  assert.equal(state.appMode, "evaluation");
  assert.deepEqual(activatedTabs, ["evaluation"]);
  assert.equal(intervals[0].delay, 2000);
  assert.equal(fetches.includes("/api/evaluations/catalog?limit=500"), true);
  assert.equal(elements.get("evaluation-mode-btn").classes.has("active"), true);
  assert.equal(workspacePanes.every((element) => element.classes.has("hidden")), true);

  controller.setApplicationMode("workspace");
  assert.equal(state.appMode, "workspace");
  assert.deepEqual(activatedTabs, ["evaluation", "chat"]);
  assert.deepEqual(clearedIntervals, [1]);
  assert.equal(workspacePanes.every((element) => !element.classes.has("hidden")), true);
});
