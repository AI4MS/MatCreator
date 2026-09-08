/**
 * Harness-injected wakeup messages are stored as durable user events but are
 * not something the user typed. Detect them by the stable first-line marker
 * the middleware emits (`_resume_remote_job_session` in `web/main.py`) so the
 * transcript can fold them instead of showing a wall-of-text user bubble.
 */
export const HARNESS_WAKEUP_MARKER = "REMOTE JOB STATUS UPDATE";

export function isHarnessWakeupMessage(text) {
  return String(text || "").trimStart().startsWith(HARNESS_WAKEUP_MARKER);
}

function fieldValue(lines, label) {
  const prefix = `${label}:`;
  const line = lines.find((candidate) => candidate.startsWith(prefix));
  return line ? line.slice(prefix.length).trim() : "";
}

/** Compact one-line description for the folded harness notice. */
export function harnessWakeupSummary(text) {
  const lines = String(text || "").split("\n").map((line) => line.trim());
  const jobId = fieldValue(lines, "Tracked job_id");
  const provider = fieldValue(lines, "Provider");
  const outcome = fieldValue(lines, "Observed outcome").split(";")[0].trim();
  const parts = ["Remote job update"];
  const identity = [provider, jobId ? `job ${jobId}` : ""].filter(Boolean).join(" ");
  if (identity) parts.push(identity);
  if (outcome) parts.push(outcome);
  return `${parts.join(" — ")} · session resumed automatically`;
}
