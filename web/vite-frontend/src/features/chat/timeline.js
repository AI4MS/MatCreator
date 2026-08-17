export function mergeReplayedText(current, incoming) {
  if (!incoming) return current;
  if (!current) return incoming;
  if (incoming.startsWith(current)) return incoming;
  if (current.endsWith(incoming)) return current;
  const maxOverlap = Math.min(current.length, incoming.length);
  for (let overlap = maxOverlap; overlap > 0; overlap--) {
    if (current.endsWith(incoming.slice(0, overlap))) {
      return current + incoming.slice(overlap);
    }
  }
  return current + incoming;
}

export function compactRepeatedPrefixSnapshots(text) {
  if (!text) return text;
  let compacted = text;
  let changed = true;
  while (changed) {
    changed = false;
    const maxPrefix = Math.floor(compacted.length / 2);
    for (let size = maxPrefix; size > 3; size--) {
      const prefix = compacted.slice(0, size);
      const rest = compacted.slice(size);
      if (rest.startsWith(prefix)) {
        compacted = rest;
        changed = true;
        break;
      }
    }
  }
  return compacted;
}

function nextTimelineItemId(timeline, prefix) {
  const nextId = timeline._nextItemId || 0;
  timeline._nextItemId = nextId + 1;
  return `${prefix}:${nextId}`;
}

export function upsertTimelineThought(timeline, text) {
  if (!text) return;
  const compacted = compactRepeatedPrefixSnapshots(text);
  const last = timeline[timeline.length - 1];
  if (last?.type === "reasoning") {
    last.text = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    return;
  }
  // Reasoning is retained for inspection, but is not a peer of an action in
  // the default execution trace. A following tool call makes it transient.
  timeline.push({ type: "reasoning", timelineId: nextTimelineItemId(timeline, "reasoning"), text: compacted });
}

export function upsertTimelineText(timeline, text) {
  if (!text) return;
  const compacted = compactRepeatedPrefixSnapshots(text);
  const last = timeline[timeline.length - 1];
  if (last?.type === "text") {
    // Only the currently streaming, contiguous text block is mutable. Text
    // before a Thinking/IN/OUT item is historical content and must retain its
    // position and DOM identity when a later text block arrives.
    last.text = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    return;
  }
  timeline.push({ type: "text", timelineId: nextTimelineItemId(timeline, "text"), text: compacted });
}

function titleizeToolName(name = "") {
  return String(name).replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function responseError(response = {}) {
  if (!response || typeof response !== "object") return "";
  const error = response.error || response.exception || response.traceback;
  if (error) return typeof error === "string" ? error : JSON.stringify(error);
  if (["error", "failed", "failure", "cancelled", "blocked"].includes(String(response.status || "").toLowerCase())) {
    return response.message || response.detail || `Tool returned ${response.status}`;
  }
  return "";
}

function readCount(payload, keys) {
  for (const key of keys) {
    const value = payload?.[key];
    if (Array.isArray(value)) return value.length;
    if (Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

function semanticSummary(name, input = {}, output = null, status = "running", error = "") {
  const tool = String(name || "").toLowerCase();
  const done = status === "success";
  const failed = status === "failed";
  const node = input.node_id || input.step_id || output?.node_id || output?.step_id || output?.name;
  if (tool.includes("resume_execution") || tool.includes("confirm_plan_and_start_execution")) {
    const pending = readCount(output, ["pending_nodes", "pending", "nodes"]);
    return {
      action: failed ? "Could not resume execution" : done ? "Resumed execution" : "Resuming execution",
      detail: pending === null ? (error || "Execution resumed") : `Execution resumed · ${pending} pending ${pending === 1 ? "node" : "nodes"}`,
    };
  }
  if (tool.includes("get_ready_nodes")) {
    const count = readCount(output, ["ready_nodes", "nodes", "ready"]);
    const nodes = output?.ready_nodes || output?.nodes || output?.ready;
    const nodeNames = Array.isArray(nodes)
      ? nodes.map((item) => typeof item === "string" ? item : item?.node_id || item?.id || item?.name).filter(Boolean)
      : [];
    return {
      action: failed ? "Could not find ready tasks" : done ? `Found ${count ?? ""} ready ${count === 1 ? "task" : "tasks"}`.replace("  ", " ") : "Finding ready tasks",
      detail: nodeNames.join(", ") || (count === null ? (error || "Returned ready nodes") : `Returned ${count} ${count === 1 ? "node" : "nodes"}`),
    };
  }
  if (tool.includes("run_node_executor") || tool.includes("run_flash_step") || tool.includes("run_sub_agent")) {
    const label = node || "task";
    return {
      action: failed ? `${label} failed` : done ? `${label} completed` : `${label} running`,
      detail: failed ? (error || `${label} failed`) : done ? `${label} completed successfully` : `${label} running…`,
    };
  }
  if (tool.includes("validate_plan") || tool.includes("validate_graph")) {
    return { action: failed ? "Plan validation failed" : done ? "Validated plan" : "Validating plan", detail: error || (done ? "Plan is ready for review" : "Checking the plan") };
  }
  if (tool.includes("set_node_status")) {
    const from = input.status_from || input.previous_status || output?.previous_status || output?.from_status;
    const to = input.status || input.new_status || output?.status || output?.new_status;
    return {
      action: failed ? "Could not update execution state" : done ? "Updated execution state" : "Updating execution state",
      detail: node && to ? `${node}${from ? ` · ${from} → ${to}` : ` → ${to}`}` : error || (done ? "Execution state updated" : "Updating execution state…"),
    };
  }
  if (tool.includes("mark_dependents_blocked") || tool.includes("block_dependents")) {
    const count = readCount(output, ["blocked_nodes", "dependents", "nodes", "blocked"]);
    return {
      action: failed ? "Could not check downstream dependencies" : done && count !== null ? `Blocked ${count} dependent ${count === 1 ? "task" : "tasks"}` : done ? "Checked downstream dependencies" : "Checking downstream dependencies",
      detail: error || (count === null ? (done ? "No runnable dependents" : "Checking dependent tasks…") : `${count} dependent ${count === 1 ? "task" : "tasks"} blocked`),
    };
  }
  if (tool.includes("to_planner") || tool.includes("return_to_planner")) {
    return { action: failed ? "Could not return to planner" : done ? "Returned to planner" : "Returning to planner", detail: error || (done ? "Execution control returned to planning" : "Preparing the next plan") };
  }
  const text = output?.summary || output?.message || output?.result_summary || error;
  return { action: failed ? `${titleizeToolName(name)} failed` : done ? titleizeToolName(name) : `${titleizeToolName(name)}…`, detail: text || (done ? "Completed" : "Running…") };
}

function durationMs(call) {
  const output = call.output || {};
  const explicit = output.duration_ms ?? output.durationMs ?? output.elapsed_ms;
  if (Number.isFinite(Number(explicit))) return Number(explicit);
  if (call.completedAt && call.startedAt) return Math.max(0, call.completedAt - call.startedAt);
  return null;
}

function enrichToolCall(call) {
  call.error = responseError(call.output);
  call.status = call.error ? "failed" : call.output ? "success" : "running";
  call.durationMs = durationMs(call);
  const summary = semanticSummary(call.name, call.input, call.output, call.status, call.error);
  call.semanticAction = summary.action;
  call.semanticSummary = summary.detail;
  return call;
}

function findToolCall(timeline, event) {
  const actions = timeline.filter((item) => item.type === "activity_action");
  if (event.id) return actions.flatMap((action) => action.toolCalls).find((call) => call.id === event.id);
  // Backends occasionally omit an id on the response. Pair it with the most
  // recent unresolved invocation of the same tool instead of creating IN/OUT
  // rows that users have to mentally join.
  return actions.flatMap((action) => action.toolCalls).reverse()
    .find((call) => call.name === event.name && !call.output);
}

function actionMetadata(event) {
  const payload = event.type === "function_call" ? event.args : event.response;
  return payload?.action_id || payload?.actionId || event.action_id || event.actionId || null;
}

function refreshAction(action) {
  const calls = action.toolCalls;
  const primary = calls.at(-1) || {};
  action.status = calls.some((call) => call.status === "failed") ? "failed"
    : calls.some((call) => call.status === "running") ? "running" : "success";
  action.title = primary.semanticAction || "Working";
  action.summary = primary.semanticSummary || "";
  action.durationMs = calls.reduce((total, call) => total + (call.durationMs || 0), 0) || null;
  return action;
}

function findActionForNewCall(timeline, event) {
  const metadataId = actionMetadata(event);
  if (!metadataId) return null;
  return timeline.find((item) => item.type === "activity_action" && item.backendActionId === metadataId) || null;
}

function attachPendingReasoning(timeline, action) {
  const index = timeline.findLastIndex((item) => item.type === "reasoning");
  if (index < 0) return;
  // Only reasoning immediately adjacent to this action is pending. Earlier
  // reasoning belongs to an already-normalized action or planner state.
  if (index < timeline.length - 2) return;
  action.reasoning.push(timeline[index]);
  timeline.splice(index, 1);
}

/**
 * Adapter from backend protocol events to the presentation model. Components
 * only receive unified ToolCall objects; input and output remain available as
 * raw payloads behind the final disclosure level.
 */
export function upsertTimelineEvent(timeline, event) {
  const isInput = event.type === "function_call";
  let call = findToolCall(timeline, event);
  let action = call?.action;
  if (!call) {
    action = findActionForNewCall(timeline, event) || {
      type: "activity_action",
      timelineId: nextTimelineItemId(timeline, "action"),
      id: event.id || nextTimelineItemId(timeline, "action-id"),
      backendActionId: actionMetadata(event),
      toolCalls: [],
      reasoning: [],
      rawEvents: [],
    };
    call = {
      type: "tool_call",
      timelineId: nextTimelineItemId(timeline, "tool"),
      id: event.id,
      name: event.name || "Unknown",
      input: {},
      output: null,
      startedAt: Date.now(),
      action,
    };
    action.toolCalls.push(call);
    if (!timeline.includes(action)) timeline.push(action);
    attachPendingReasoning(timeline, action);
  }
  if (isInput) {
    call.id ||= event.id;
    call.name = event.name || call.name;
    call.input = event.args || {};
  } else {
    call.id ||= event.id;
    call.name = event.name || call.name;
    call.output = event.response || {};
    call.completedAt = Date.now();
  }
  enrichToolCall(call);
  action.rawEvents.push(event);
  refreshAction(action);
  return action;
}
