export const ASSISTANT_MESSAGE_LIFECYCLES = Object.freeze([
  "created",
  "streaming",
  "finalizing",
  "completed",
]);

export function createAssistantMessage({
  id,
  lifecycle = "created",
  startedAt = null,
  endedAt = null,
} = {}) {
  if (!ASSISTANT_MESSAGE_LIFECYCLES.includes(lifecycle)) {
    throw new Error(`Unknown assistant message lifecycle: ${lifecycle}`);
  }
  return {
    id: id || `assistant:${startedAt || Date.now()}`,
    role: "assistant",
    lifecycle,
    items: [],
    revision: 0,
    startedAt,
    endedAt,
  };
}

function setAssistantMessageLifecycle(message, lifecycle) {
  if (message.lifecycle === lifecycle) return false;
  const current = ASSISTANT_MESSAGE_LIFECYCLES.indexOf(message.lifecycle);
  const next = ASSISTANT_MESSAGE_LIFECYCLES.indexOf(lifecycle);
  if (current < 0 || next !== current + 1) {
    throw new Error(`Invalid assistant message lifecycle transition: ${message.lifecycle} -> ${lifecycle}`);
  }
  message.lifecycle = lifecycle;
  message.revision += 1;
  return true;
}

export function beginAssistantMessageFinalization(message) {
  if (message.lifecycle === "completed" || message.lifecycle === "finalizing") return false;
  if (message.lifecycle === "created") setAssistantMessageLifecycle(message, "streaming");
  return setAssistantMessageLifecycle(message, "finalizing");
}

export function completeAssistantMessage(message, endedAt = Date.now()) {
  if (message.lifecycle === "completed") return false;
  beginAssistantMessageFinalization(message);
  message.endedAt = endedAt;
  setAssistantMessageLifecycle(message, "completed");
  return true;
}

export function applyAssistantMessagePart(message, part) {
  if (!part || message.lifecycle === "completed" || message.lifecycle === "finalizing") return null;
  if (message.lifecycle === "created") setAssistantMessageLifecycle(message, "streaming");

  if (part.thought) {
    upsertTimelineThought(message.items, part.text || "");
    message.revision += 1;
    return { type: "reasoning", text: part.text || "" };
  }
  const functionCall = part.functionCall || part.function_call;
  if (functionCall) {
    const normalized = {
      type: "function_call",
      id: functionCall.id,
      name: functionCall.name || "Unknown",
      args: functionCall.args || {},
      actionId: functionCall.action_id || functionCall.actionId,
    };
    upsertTimelineEvent(message.items, normalized);
    message.revision += 1;
    return normalized;
  }
  const functionResponse = part.functionResponse || part.function_response;
  if (functionResponse) {
    const normalized = {
      type: "function_response",
      id: functionResponse.id,
      name: functionResponse.name || "Unknown",
      response: functionResponse.response || {},
      actionId: functionResponse.action_id || functionResponse.actionId,
    };
    upsertTimelineEvent(message.items, normalized);
    message.revision += 1;
    return normalized;
  }
  if (part.text) {
    upsertTimelineText(message.items, part.text);
    message.revision += 1;
    return { type: "text", text: part.text };
  }
  return null;
}

export function mergeReplayedText(current, incoming) {
  if (!incoming) return current;
  if (!current) return incoming;
  if (incoming.startsWith(current)) return incoming;
  if (current.endsWith(incoming)) return current;
  // ADK can replay a cumulative text snapshot on a fresh SSE record with a
  // separator newline (or spaces) prepended. That prefix is transport
  // framing, not newly generated prose. Without this check the full snapshot
  // is appended to the streamed text, then disappears when durable history
  // replaces the live turn at completion.
  const replayCandidate = incoming.replace(/^\s+/, "");
  if (replayCandidate !== incoming && replayCandidate.startsWith(current)) return replayCandidate;
  if (replayCandidate !== incoming && current.endsWith(replayCandidate)) return current;
  // Find the longest suffix/prefix overlap in linear time. The previous
  // descending slice/endsWith loop became quadratic for long streamed code
  // blocks when a provider delivered partially overlapping chunks.
  const prefix = incoming.slice(0, Math.min(current.length, incoming.length));
  const combined = `${prefix}\u0000${current.slice(-prefix.length)}`;
  const failure = new Uint32Array(combined.length);
  for (let index = 1; index < combined.length; index += 1) {
    let matched = failure[index - 1];
    while (matched > 0 && combined[index] !== combined[matched]) matched = failure[matched - 1];
    if (combined[index] === combined[matched]) matched += 1;
    failure[index] = matched;
  }
  const overlap = Math.min(prefix.length, failure[combined.length - 1] || 0);
  if (overlap) return current + incoming.slice(overlap);
  return current + incoming;
}

export function compactRepeatedPrefixSnapshots(text) {
  if (!text) return text;
  let compacted = text;
  while (compacted.length >= 8) {
    // A repeated prefix must begin with the same four-character seed. Jump
    // between those native string-search matches instead of allocating every
    // possible prefix and testing all O(n) offsets. Ordinary streamed prose
    // almost always exits after the first seed lookup.
    const seed = compacted.slice(0, 4);
    let repeatedPrefix = 0;
    let size = compacted.lastIndexOf(seed, Math.floor(compacted.length / 2));
    while (size > 3) {
      if (compacted.startsWith(compacted.slice(0, size), size)) {
        repeatedPrefix = size;
        break;
      }
      size = compacted.lastIndexOf(seed, size - 1);
    }
    if (!repeatedPrefix) break;
    compacted = compacted.slice(repeatedPrefix);
  }
  return compacted;
}

function timelineLookup(timeline) {
  if (timeline._lookup) return timeline._lookup;
  const lookup = {
    callById: new Map(),
    callByExecutorKey: new Map(),
    unresolvedByName: new Map(),
    actionByBackendId: new Map(),
  };
  for (const action of timeline) {
    if (action.type !== "activity_action") continue;
    if (action.backendActionId) lookup.actionByBackendId.set(action.backendActionId, action);
    for (const call of action.toolCalls || []) registerTimelineCall(lookup, call);
  }
  Object.defineProperty(timeline, "_lookup", { value: lookup, configurable: true });
  return lookup;
}

function registerTimelineCall(lookup, call) {
  if (call.id) lookup.callById.set(call.id, call);
  if (call.executorTaskKey) lookup.callByExecutorKey.set(call.executorTaskKey, call);
  if (!call.output) {
    const unresolved = lookup.unresolvedByName.get(call.name) || new Set();
    unresolved.add(call);
    lookup.unresolvedByName.set(call.name, unresolved);
  }
}

function resolveTimelineCall(lookup, call) {
  const unresolved = lookup.unresolvedByName.get(call.name);
  unresolved?.delete(call);
  if (unresolved?.size === 0) lookup.unresolvedByName.delete(call.name);
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
    const nextText = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    if (nextText !== last.text) {
      last.text = nextText;
      last.renderRevision = (last.renderRevision || 0) + 1;
    }
    return;
  }
  // Reasoning remains a chronological timeline entry. It is intentionally
  // not inferred to belong to a later tool call without explicit metadata.
  timeline.push({ type: "reasoning", timelineId: nextTimelineItemId(timeline, "reasoning"), text: compacted, renderRevision: 1 });
}

export function upsertTimelineText(timeline, text) {
  if (!text) return;
  const compacted = compactRepeatedPrefixSnapshots(text);
  const last = timeline[timeline.length - 1];
  if (last?.type === "text") {
    // Only the currently streaming, contiguous text block is mutable. Text
    // before a Thinking/IN/OUT item is historical content and must retain its
    // position and DOM identity when a later text block arrives.
    const nextText = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    if (nextText !== last.text) {
      last.text = nextText;
      last.renderRevision = (last.renderRevision || 0) + 1;
    }
    return;
  }
  // A provider can replay the cumulative assistant snapshot after a thought
  // or tool event. In that shape the last timeline item is not text, so the
  // normal contiguous merge above cannot see the replay and creates a second
  // copy of the answer. Update the originating text block instead. A real
  // follow-up message cannot begin with the entire earlier block.
  const earlierText = [...timeline].reverse().find((item) => item.type === "text");
  const replayCandidate = compacted.replace(/^\s+/, "");
  const earlierCandidate = String(earlierText?.text || "").replace(/^\s+/, "");
  if (earlierText && earlierCandidate.length >= 8 && replayCandidate.startsWith(earlierCandidate)) {
    const nextText = compactRepeatedPrefixSnapshots(mergeReplayedText(earlierText.text || "", compacted));
    if (nextText !== earlierText.text) {
      earlierText.text = nextText;
      earlierText.renderRevision = (earlierText.renderRevision || 0) + 1;
    }
    return;
  }
  timeline.push({ type: "text", timelineId: nextTimelineItemId(timeline, "text"), text: compacted, renderRevision: 1 });
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

function isExecutorLauncher(name = "") {
  return ["run_flash_step", "run_node_executor", "run_sub_agent"].includes(String(name));
}

function executorTaskKey(name, input = {}) {
  if (!isExecutorLauncher(name)) return null;
  const tool = String(name);
  const nodeId = input?.node_id || input?.step_id;
  if (nodeId) return `${tool}:node:${nodeId}`;

  // Child executors have no graph-node id. Their step number is unique within
  // the parent invocation; include the action so an incomplete/malformed
  // payload cannot collapse unrelated work into the same card.
  if (tool === "run_sub_agent" && input?.step_number !== undefined && input?.action) {
    return `${tool}:step:${input.step_number}:action:${input.action}`;
  }
  return null;
}

function mergeExecutorReplay(call, event) {
  // Stream retries can repeat one logical executor call with a fresh transient
  // function-call id. Keep the original id (so its normal response still
  // pairs), but retain any newer input fields from the replay.
  if (event.args && Object.keys(event.args).length) {
    call.input = { ...call.input, ...event.args };
  }
  return call;
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
  const lookup = timelineLookup(timeline);
  if (event.id) {
    const exact = lookup.callById.get(event.id);
    if (exact) return exact;
  }

  // `run_node_executor` and `run_sub_agent` represent durable units of work.
  // Some streaming providers replay an invocation with a new call id while a
  // connection is live.  The graph node (or child step/action) is the stable
  // identity, so merge that replay instead of adding a second task row.
  const taskKey = event.type === "function_call" ? executorTaskKey(event.name, event.args) : null;
  if (taskKey) {
    const replayed = lookup.callByExecutorKey.get(taskKey);
    if (replayed) return mergeExecutorReplay(replayed, event);
  }

  // Responses with a provider-generated id cannot be matched exactly. Only
  // infer a pair when there is one unresolved call of that name; choosing the
  // most recent call here used to create a phantom delegation when a replay
  // arrived during a live stream.
  if (event.type === "function_response") {
    const unresolved = lookup.unresolvedByName.get(event.name);
    return unresolved?.size === 1 ? unresolved.values().next().value : undefined;
  }

  // A call without an id or durable executor identity can only be paired by
  // order. This keeps the existing best-effort behavior for ordinary tools.
  const unresolved = lookup.unresolvedByName.get(event.name);
  if (!unresolved) return undefined;
  let latest;
  for (const call of unresolved) latest = call;
  return latest;
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
  action.renderRevision = (action.renderRevision || 0) + 1;
  return action;
}

function findActionForNewCall(timeline, event) {
  const metadataId = actionMetadata(event);
  if (!metadataId) return null;
  return timelineLookup(timeline).actionByBackendId.get(metadataId) || null;
}

/**
 * Adapter from backend protocol events to the presentation model. Components
 * only receive unified ToolCall objects; input and output remain available as
 * raw payloads behind the final disclosure level.
 */
export function upsertTimelineEvent(timeline, event) {
  const isInput = event.type === "function_call";
  const lookup = timelineLookup(timeline);
  let call = findToolCall(timeline, event);
  let action = call?.action;
  if (!call) {
    action = findActionForNewCall(timeline, event) || {
      type: "activity_action",
      timelineId: nextTimelineItemId(timeline, "action"),
      id: event.id || nextTimelineItemId(timeline, "action-id"),
      backendActionId: actionMetadata(event),
      toolCalls: [],
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
      executorTaskKey: event.type === "function_call" ? executorTaskKey(event.name, event.args) : null,
    };
    action.toolCalls.push(call);
    registerTimelineCall(lookup, call);
    if (!timeline.includes(action)) {
      timeline.push(action);
      if (action.backendActionId) lookup.actionByBackendId.set(action.backendActionId, action);
    }
  }
  if (isInput) {
    call.id ||= event.id;
    call.name = event.name || call.name;
    call.input = event.args || {};
    if (call.id) lookup.callById.set(call.id, call);
    if (call.executorTaskKey) lookup.callByExecutorKey.set(call.executorTaskKey, call);
  } else {
    call.id ||= event.id;
    call.name = event.name || call.name;
    call.output = event.response || {};
    call.completedAt = Date.now();
    resolveTimelineCall(lookup, call);
  }
  enrichToolCall(call);
  action.rawEvents.push(event);
  refreshAction(action);
  return action;
}

/**
 * The live stream is allowed to be at-least-once, while a restored session is
 * reconstructed from durable graph nodes. Keep the Delegated tasks list on
 * that same stable identity as a final rendering guard.
 */
export function deduplicateDelegationToolCalls(calls) {
  const byTask = new Map();
  for (const call of calls || []) {
    const key = call.executorTaskKey || executorTaskKey(call.name, call.input)
      || `call:${call.id || call.timelineId || byTask.size}`;
    const previous = byTask.get(key);
    if (!previous) {
      byTask.set(key, call);
      continue;
    }
    // A completed record is richer than a repeated "running" invocation;
    // otherwise retain the original card to prevent visual reordering.
    if (!previous.output && call.output) byTask.set(key, call);
  }
  return [...byTask.values()];
}
