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

/**
 * Only root executor launches own rows in the message-level Delegated tasks
 * group. `run_sub_agent` is rendered by StepExecutionFeed beneath the parent
 * executor card, so giving it another group row renders the same sub-agent
 * twice.
 */
export function isDelegatedTaskRootTool(name) {
  return ["run_flash_step", "run_node_executor"].includes(name || "");
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
    .filter((call) => isDelegatedTaskRootTool(call.name)));
}

/**
 * Project the canonical event timeline into renderable segments without
 * changing its order. Delegations are first-class segments at the invocation
 * position; graph updates later populate that fixed slot instead of moving a
 * message-level task container through newer text and activity.
 */
export function timelineSegments(timeline) {
  const segments = [];
  let activityItems = [];
  const flushActivity = () => {
    if (!activityItems.length) return;
    segments.push({
      type: "activity",
      key: `activity:${activityItems[0].timelineId}`,
      items: activityItems,
      revision: activityItems.map((item) => `${item.timelineId}:${item.renderRevision || 0}`).join("|"),
    });
    activityItems = [];
  };
  const appendDelegation = (item, call, callSlot) => {
    const activityId = item.activityId || null;
    const delegationItem = { ...item, timelineId: callSlot, toolCalls: [call] };
    const revision = `${callSlot}:${item.renderRevision || 0}`;
    const previous = segments.at(-1);
    if (activityId && previous?.type === "delegation" && previous.activityId === activityId) {
      previous.items.push(delegationItem);
      previous.calls.push(call);
      previous.revision += `|${revision}`;
      return;
    }
    segments.push({
      type: "delegation",
      key: `delegation:${activityId || callSlot}`,
      activityId,
      items: [delegationItem],
      calls: [call],
      revision,
    });
  };

  for (const item of timeline || []) {
    if (item.type === "reasoning") {
      activityItems.push(item);
      continue;
    }
    if (item.type === "activity_action") {
      const calls = item.toolCalls || [];
      calls.forEach((call, callIndex) => {
        const callSlot = calls.length === 1
          ? item.timelineId
          : `${item.timelineId}:tool:${call.timelineId || call.id || callIndex}`;
        if (isDelegatedTaskRootTool(call.name)) {
          flushActivity();
          appendDelegation(item, call, callSlot);
        } else if (!isExecutorLauncherTool(call.name)) {
          activityItems.push({ ...item, timelineId: callSlot, toolCalls: [call] });
        }
      });
      continue;
    }
    flushActivity();
    if (item.type === "text") {
      segments.push({
        type: "text",
        key: `text:${item.timelineId}`,
        items: [item],
        revision: `${item.timelineId}:${item.renderRevision || 0}`,
      });
    }
  }
  flushActivity();
  return segments;
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
