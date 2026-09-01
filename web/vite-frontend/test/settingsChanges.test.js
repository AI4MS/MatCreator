import assert from "node:assert/strict";
import test from "node:test";

import { buildSettingsSavePlan, settingsValuesEqual } from "../src/features/settings/settingsChanges.js";

function draft(overrides = {}) {
  return {
    username: "Ada",
    defaultWorkdir: "/work",
    extraSkills: ["alpha"],
    disabledSkills: [],
    llm: { executor_cards: { default: "", cards: {} } },
    envValues: { CUSTOM_ENV: {}, LLM_MODEL: "model-a" },
    ...overrides,
  };
}

test("settings equality ignores object key order", () => {
  assert.equal(settingsValuesEqual({ b: 2, a: { d: 4, c: 3 } }, { a: { c: 3, d: 4 }, b: 2 }), true);
});

test("profile-only changes do not write env config or restart the backend", () => {
  const plan = buildSettingsSavePlan(draft(), draft({ defaultWorkdir: "/next" }));

  assert.deepEqual(plan.settingsBody, { workspace: { default_workdir: "/next" } });
  assert.equal(plan.envValues, null);
  assert.equal(plan.restartBackend, false);
  assert.equal(plan.changed, true);
});

test("environment changes only write env config and request a restart", () => {
  const plan = buildSettingsSavePlan(
    draft(),
    draft({ envValues: { CUSTOM_ENV: { TOKEN: "value" }, LLM_MODEL: "model-a" } }),
  );

  assert.equal(plan.settingsBody, null);
  assert.deepEqual(plan.envValues, { CUSTOM_ENV: { TOKEN: "value" }, LLM_MODEL: "model-a" });
  assert.equal(plan.restartBackend, true);
});

test("unchanged settings produce no requests", () => {
  assert.deepEqual(buildSettingsSavePlan(draft(), draft()), {
    changed: false,
    envValues: null,
    restartBackend: false,
    settingsBody: null,
  });
});
