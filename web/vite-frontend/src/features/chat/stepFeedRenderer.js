import { getStructurePaths } from "./timelinePresentation.js";
import { createPayloadBlock } from "./payloadViews.js";

export function formatStepDuration(node, now = Date.now()) {
  if (!node.start_time) return "—";
  const startedAt = new Date(node.start_time).getTime();
  if (!Number.isFinite(startedAt)) return "—";
  if (!node.end_time) {
    const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1000));
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    return hours > 0
      ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      : `${minutes}:${String(seconds).padStart(2, "0")}`;
  }
  const endedAt = new Date(node.end_time).getTime();
  if (!Number.isFinite(endedAt)) return "—";
  return `${(Math.max(0, endedAt - startedAt) / 1000).toFixed(1)}s`;
}

export function stepFeedTitle(node) {
  const input = node.input || {};
  return {
    action: input.action || node.label || input.node_id || input.step_id || node.id || "Task",
    identifier: input.node_id || input.step_id || node.id || "",
  };
}

export function stepFeedStatusIcon(status) {
  if (status === "running") return "◌";
  if (["failed", "cancelled", "blocked"].includes(status)) return "!";
  if (status === "needs_replanning") return "↻";
  if (["success", "completed"].includes(status)) return "✓";
  return "•";
}

export function createStepFeedRenderer({
  activityRenderer,
  createStructureViewButtonGroup,
}) {
  const { createTimelineReasoning, createActivityAction } = activityRenderer;

  function renderStepInput(input) {
    const details = document.createElement("details");
    details.className = "step-feed-nested";
    const summary = document.createElement("summary");
    summary.textContent = "Input";
    details.append(summary, createPayloadBlock(input));
    return details;
  }

  function renderStepConversationEvent(event, { collapsed = false, timelineId } = {}) {
    if (["thought", "text"].includes(event.type)) {
      return createTimelineReasoning({
        timelineId: timelineId
          || `step-conversation:${event.timestamp || ""}:${event.author || ""}:${event.type}`,
        text: String(event.content || ""),
      }, () => {}, collapsed);
    }
    const details = document.createElement("details");
    details.className = "agent-activity-action step-feed-conversation";
    const summary = document.createElement("summary");
    const status = document.createElement("span");
    status.className = "agent-activity-status";
    status.textContent = event.type === "function_call" ? "🔧" : "↩";
    const heading = document.createElement("span");
    heading.className = "activity-action-heading";
    const title = document.createElement("span");
    title.className = "activity-action-title";
    title.textContent = `[${event.author || "step_executor"}] ${event.type || "event"}`;
    heading.appendChild(title);
    summary.append(status, heading);
    details.appendChild(summary);
    const body = document.createElement("div");
    body.className = "activity-action-body";
    body.appendChild(createPayloadBlock(event.content));
    details.appendChild(body);
    return details;
  }

  function renderStepToolCall(toolCallData) {
    const status = toolCallData.status || (
      toolCallData.error
        ? "failed"
        : toolCallData.end_time || toolCallData.result_summary ? "success" : "running"
    );
    const startedAt = toolCallData.start_time ? new Date(toolCallData.start_time).getTime() : null;
    const endedAt = toolCallData.end_time ? new Date(toolCallData.end_time).getTime() : null;
    const durationMs = Number.isFinite(startedAt) && Number.isFinite(endedAt)
      ? Math.max(0, endedAt - startedAt)
      : null;
    const toolCall = {
      ...toolCallData,
      status,
      startedAt: Number.isFinite(startedAt) ? startedAt : null,
      durationMs,
      input: toolCallData.input ?? toolCallData.args ?? toolCallData.args_summary,
      output: toolCallData.output ?? toolCallData.result ?? toolCallData.result_summary,
      semanticSummary: toolCallData.result_summary || toolCallData.error
        || (status === "running" ? "Running…" : "Completed"),
    };
    const details = createActivityAction({
      timelineId: `step-tool:${toolCallData.id || `${toolCallData.name || "tool"}:${toolCallData.start_time || ""}`}`,
      toolCalls: [toolCall],
    }, () => {}, { includeExecutorTools: true });
    const raw = details?.querySelector(".tool-call-raw");
    const structurePaths = getStructurePaths(toolCallData);
    if (structurePaths.length) raw?.appendChild(createStructureViewButtonGroup(structurePaths));
    return details;
  }

  return { renderStepInput, renderStepConversationEvent, renderStepToolCall };
}
