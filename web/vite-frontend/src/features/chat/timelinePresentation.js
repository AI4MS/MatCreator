import { classifyPath } from "../session/fileTree.js";
import { deduplicateDelegationToolCalls } from "./timeline.js";

/** Normalizes protocol variants at the chat feature boundary. */
export function getFunctionCall(part) {
  return part?.functionCall || part?.function_call || null;
}

export function getFunctionResponse(part) {
  return part?.functionResponse || part?.function_response || null;
}

export function getPlotPaths(response) {
  const paths = [];
  const add = (path) => {
    if (typeof path === "string" && path && !paths.includes(path)) paths.push(path);
  };
  add(response?.plot_path);
  if (Array.isArray(response?.plot_paths)) response.plot_paths.forEach(add);
  return paths;
}

/** Finds structure artifacts in both current and legacy tool payloads. */
export function getStructurePaths(payload) {
  const paths = [];
  const add = (path) => {
    if (typeof path === "string" && path && !paths.includes(path)) paths.push(path);
  };
  const visit = (value, key = "") => {
    if (!value) return;
    if (key === "structure_path") return add(value);
    if (key === "structure_paths" && Array.isArray(value)) return value.forEach(add);
    if ((key === "artifacts" || key === "artifact_paths") && Array.isArray(value)) {
      return value.forEach((path) => {
        if (classifyPath(String(path)) === "structure") add(path);
      });
    }
    if (Array.isArray(value)) return value.forEach((item) => visit(item, key));
    if (typeof value === "object") Object.entries(value).forEach(([childKey, childValue]) => visit(childValue, childKey));
  };
  visit(payload);
  return paths;
}

export function isExecutorLauncherTool(name) {
  return ["run_flash_step", "run_node_executor", "run_sub_agent"].includes(name || "");
}

export function executorNodeId(call) {
  const input = call?.input || {};
  return input.node_id || input.step_id || input.step_number || "";
}

export function activityToolCalls(action) {
  return (action.toolCalls || []).filter((call) => !isExecutorLauncherTool(call.name));
}

export function delegationToolCalls(items) {
  return deduplicateDelegationToolCalls(items
    .filter((item) => item.type === "activity_action")
    .flatMap((action) => action.toolCalls || [])
    .filter((call) => isExecutorLauncherTool(call.name)));
}

export function formatToolDuration(toolCall) {
  const duration = toolCall.durationMs ?? (toolCall.startedAt ? Date.now() - toolCall.startedAt : null);
  if (!Number.isFinite(duration)) return toolCall.status === "running" ? "running…" : "";
  return duration < 1000 ? `${Math.round(duration)} ms` : `${(duration / 1000).toFixed(1)}s`;
}

export function toolStatusIcon(toolCall) {
  if (toolCall.status === "failed") return "!";
  return toolCall.status === "running" ? "◌" : "✓";
}

export function normalizeAgentTimestamp(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = new Date(value || "").getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatAgentDuration(elapsedMs) {
  const elapsedSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const hours = Math.floor(elapsedSeconds / 3600);
  const minutes = Math.floor((elapsedSeconds % 3600) / 60);
  const seconds = elapsedSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}
