import assert from "node:assert/strict";
import test from "node:test";

import {
  HARNESS_WAKEUP_MARKER,
  harnessWakeupSummary,
  isHarnessWakeupMessage,
} from "../src/features/chat/harnessMessages.js";

const WAKEUP_TEXT = [
  "REMOTE JOB STATUS UPDATE from the harness.",
  "Notification ID: notif-1",
  "Tracked job_id: job-abc123",
  "Graph node: node-7",
  "Provider: bohr_batchjob",
  "Event kind: lifecycle",
  "Observed outcome: succeeded; current allocation status: collected",
  "Read get_remote_job_status with this job_id first.",
].join("\n");

test("detects harness wakeup messages and leaves user text alone", () => {
  assert.equal(isHarnessWakeupMessage(WAKEUP_TEXT), true);
  assert.equal(isHarnessWakeupMessage(`  \n${WAKEUP_TEXT}`), true);
  assert.equal(isHarnessWakeupMessage("Please relax this structure"), false);
  assert.equal(isHarnessWakeupMessage(`Tell me about ${HARNESS_WAKEUP_MARKER}`), false);
  assert.equal(isHarnessWakeupMessage(""), false);
  assert.equal(isHarnessWakeupMessage(undefined), false);
});

test("summarizes a wakeup message into a compact folded label", () => {
  assert.equal(
    harnessWakeupSummary(WAKEUP_TEXT),
    "Remote job update — bohr_batchjob job job-abc123 — succeeded · session resumed automatically",
  );
});

test("summary degrades gracefully when fields are missing", () => {
  assert.equal(
    harnessWakeupSummary("REMOTE JOB STATUS UPDATE from the harness."),
    "Remote job update · session resumed automatically",
  );
});
