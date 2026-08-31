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
    textStreams: new Map(),
    activitySequence: 0,
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
  message.textStreams.clear();
  setAssistantMessageLifecycle(message, "completed");
  return true;
}

export function applyAssistantMessagePart(message, part, { activityId = null } = {}) {
  if (!part || message.lifecycle === "completed" || message.lifecycle === "finalizing") return null;
  if (message.lifecycle === "created") setAssistantMessageLifecycle(message, "streaming");

  if (part.thought) {
    const item = upsertTimelineThought(message.items, part.text || "");
    message.revision += 1;
    return { type: "reasoning", text: part.text || "", item };
  }
  const functionCall = part.functionCall || part.function_call;
  if (functionCall) {
    const normalized = {
      type: "function_call",
      id: functionCall.id,
      name: functionCall.name || "Unknown",
      args: functionCall.args || {},
      actionId: functionCall.action_id || functionCall.actionId,
      activityId,
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
      activityId,
    };
    upsertTimelineEvent(message.items, normalized);
    message.revision += 1;
    return normalized;
  }
  if (part.text) {
    const item = upsertTimelineText(message.items, part.text);
    message.revision += 1;
    return { type: "text", text: part.text, item };
  }
  return null;
}

function textPartKind(part) {
  if (!part?.text) return null;
  return part.thought ? "reasoning" : "text";
}

function setTimelineTextItem(item, kind, text) {
  const nextText = compactRepeatedPrefixSnapshots(text);
  let changed = false;
  if (item.type !== kind) {
    item.type = kind;
    changed = true;
  }
  if (item.text !== nextText) {
    item.text = nextText;
    changed = true;
  }
  if (changed) item.renderRevision = (item.renderRevision || 0) + 1;
  return changed;
}

function replayMatch(stream, finalText) {
  if (!stream?.text || !finalText) return false;
  const current = stream.text.replace(/^\s+/, "");
  const final = finalText.replace(/^\s+/, "");
  return current.startsWith(final) || final.startsWith(current);
}

function finalTextBlocks(parts) {
  const blocks = [];
  for (const part of parts) {
    const kind = textPartKind(part);
    if (!kind) continue;
    const previous = blocks.at(-1);
    if (previous?.kind === kind) {
      previous.text = mergeReplayedText(previous.text, part.text);
    } else {
      blocks.push({ kind, text: part.text, part });
    }
  }
  return blocks;
}

function comparableSnapshot(text) {
  return String(text || "").replace(/^\s+/, "");
}

function commonPrefixLength(left, right) {
  const limit = Math.min(left.length, right.length);
  let index = 0;
  while (index < limit && left[index] === right[index]) index += 1;
  return index;
}

function isRevisedCumulativeSnapshot(current, incoming) {
  const currentText = comparableSnapshot(current);
  const incomingText = comparableSnapshot(incoming);
  const shorterLength = Math.min(currentText.length, incomingText.length);
  if (shorterLength < 32 || incomingText.length < currentText.length / 2) return false;
  // A long common beginning is protocol evidence that `incoming` is a newer
  // cumulative snapshot with a correction later in the message. Ordinary
  // token deltas do not repeat an anchored paragraph-sized prefix.
  const requiredPrefix = Math.min(96, Math.max(24, Math.ceil(shorterLength / 4)));
  return commonPrefixLength(currentText, incomingText) >= requiredPrefix;
}

/**
 * A provider can mark the whole partial stream as `thought`, then partition
 * the authoritative event into reasoning followed by visible Markdown. Treat
 * that final partition as a revision of the provisional slots. Otherwise the
 * answer remains at the end of Agent activity and is also added as a second,
 * rendered text block until durable history replaces the live message.
 */
function reconcileFinalTextSnapshot(message, authorStreams, parts) {
  const blocks = finalTextBlocks(parts);
  if (!blocks.length || !authorStreams.size) return null;

  const streams = [...authorStreams.values()]
    .filter((stream) => stream?.item)
    .sort((left, right) => message.items.indexOf(left.item) - message.items.indexOf(right.item));
  const streamItems = [...new Set(streams.map((stream) => stream.item))];
  const indices = streamItems.map((item) => message.items.indexOf(item));
  if (!indices.length || indices.some((index) => index < 0)) return null;
  const firstIndex = indices[0];
  if (indices.some((index, offset) => index !== firstIndex + offset)) return null;

  const streamed = comparableSnapshot(streams.map((stream) => stream.text).join(""));
  const finalized = comparableSnapshot(blocks.map((block) => block.text).join(""));
  const sameSnapshot = streamed.startsWith(finalized)
    || finalized.startsWith(streamed)
    || streamed.endsWith(finalized)
    || isRevisedCumulativeSnapshot(streamed, finalized);
  if (!streamed || !finalized || !sameSnapshot) {
    return null;
  }

  let changed = streamItems.length !== blocks.length;
  const items = blocks.map((block, index) => {
    const item = streamItems[index] || {
      type: block.kind,
      timelineId: nextTimelineItemId(message.items, block.kind),
      text: "",
      renderRevision: 0,
    };
    changed = setTimelineTextItem(item, block.kind, block.text) || changed;
    return item;
  });
  message.items.splice(firstIndex, streamItems.length, ...items);
  if (changed) message.revision += 1;
  return blocks.map((block, index) => ({
    part: block.part,
    normalized: { type: block.kind, text: items[index].text, item: items[index] },
  }));
}

function eventActivityId(message, event, parts) {
  const hasActivity = parts.some((part) => (
    part?.functionCall || part?.function_call || part?.functionResponse || part?.function_response
  ));
  if (!hasActivity) return null;
  const explicit = event?.id ?? event?.event_id ?? event?.eventId;
  if (explicit !== undefined && explicit !== null && String(explicit)) return String(explicit);
  const sequence = message.activitySequence || 0;
  message.activitySequence = sequence + 1;
  return `${message.id}:activity:${sequence}`;
}

/**
 * Reduce one provider event into the assistant timeline.
 *
 * Partial tokens and their final snapshot are two revisions of the same
 * content slot, not two display events. The first token fixes the slot's
 * chronological position; the final event updates that slot in place. This
 * also handles providers that correct a streamed `thought` flag in the final
 * snapshot: the existing slot changes presentation kind instead of appearing
 * once as raw activity text and again as rendered Markdown.
 */
export function applyAssistantMessageEvent(message, event) {
  const parts = event?.content?.parts || [];
  const author = String(event?.author || "assistant");
  const authorStreams = message.textStreams.get(author) || new Map();
  const activityId = eventActivityId(message, event, parts);
  const applyPart = (part) => applyAssistantMessagePart(message, part, { activityId });
  const applied = [];

  if (event?.partial === true) {
    for (const part of parts) {
      const normalized = applyPart(part);
      if (!normalized) continue;
      applied.push({ part, normalized });
      const kind = textPartKind(part);
      if (!kind) continue;
      const previous = authorStreams.get(kind);
      authorStreams.set(kind, {
        item: normalized.item,
        text: previous?.item === normalized.item
          ? mergeReplayedText(previous.text, part.text)
          : part.text,
      });
    }
    if (authorStreams.size) message.textStreams.set(author, authorStreams);
    return applied;
  }

  if (!authorStreams.size) {
    for (const part of parts) {
      const normalized = applyPart(part);
      if (normalized) applied.push({ part, normalized });
    }
    return applied;
  }

  const reconciledSnapshot = reconcileFinalTextSnapshot(message, authorStreams, parts);
  if (reconciledSnapshot) {
    applied.push(...reconciledSnapshot);
    for (const part of parts) {
      if (textPartKind(part)) continue;
      const normalized = applyPart(part);
      if (normalized) applied.push({ part, normalized });
    }
    message.textStreams.delete(author);
    return applied;
  }

  const finalTextByKind = new Map();
  for (const part of parts) {
    const kind = textPartKind(part);
    if (!kind) continue;
    finalTextByKind.set(kind, mergeReplayedText(finalTextByKind.get(kind) || "", part.text));
  }

  const matches = new Map();
  const unmatchedStreams = new Set(authorStreams.keys());
  for (const [kind, finalText] of finalTextByKind) {
    let streamKind = replayMatch(authorStreams.get(kind), finalText) ? kind : null;
    if (!streamKind) {
      streamKind = [...unmatchedStreams].find((candidate) => replayMatch(authorStreams.get(candidate), finalText)) || null;
    }
    if (streamKind) {
      matches.set(kind, authorStreams.get(streamKind));
      unmatchedStreams.delete(streamKind);
    }
  }

  const emittedTextKinds = new Set();
  for (const part of parts) {
    const kind = textPartKind(part);
    if (!kind) {
      const normalized = applyPart(part);
      if (normalized) applied.push({ part, normalized });
      continue;
    }
    if (emittedTextKinds.has(kind)) continue;
    emittedTextKinds.add(kind);
    const stream = matches.get(kind);
    if (!stream) {
      const normalized = applyPart({ ...part, text: finalTextByKind.get(kind) });
      if (normalized) applied.push({ part, normalized });
      continue;
    }
    const nextText = mergeReplayedText(stream.text, finalTextByKind.get(kind));
    if (setTimelineTextItem(stream.item, kind, nextText)) message.revision += 1;
    applied.push({ part, normalized: { type: kind, text: nextText, item: stream.item } });
  }
  // A tool-only non-partial event is not a text-stream boundary. Keep the
  // provisional identity until an authoritative text snapshot reconciles it
  // or the assistant message completes.
  if (finalTextByKind.size) message.textStreams.delete(author);
  return applied;
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
  // Some providers periodically replace an in-progress cumulative snapshot
  // after correcting Markdown structure (most visibly an unfinished table or
  // formula delimiter). The new snapshot shares a substantial anchored
  // prefix but is not a byte-for-byte extension, so appending it would render
  // the whole answer twice inside one Markdown element.
  if (isRevisedCumulativeSnapshot(current, replayCandidate)) return replayCandidate;
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
  };
  for (const action of timeline) {
    if (action.type !== "activity_action") continue;
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
  if (!text) return null;
  const compacted = compactRepeatedPrefixSnapshots(text);
  const last = timeline[timeline.length - 1];
  if (last?.type === "reasoning") {
    const nextText = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    if (nextText !== last.text) {
      last.text = nextText;
      last.renderRevision = (last.renderRevision || 0) + 1;
    }
    return last;
  }
  // Reasoning remains a chronological timeline entry. It is intentionally
  // not inferred to belong to a later tool call without explicit metadata.
  const item = { type: "reasoning", timelineId: nextTimelineItemId(timeline, "reasoning"), text: compacted, renderRevision: 1 };
  timeline.push(item);
  return item;
}

export function upsertTimelineText(timeline, text) {
  if (!text) return null;
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
    return last;
  }
  const item = { type: "text", timelineId: nextTimelineItemId(timeline, "text"), text: compacted, renderRevision: 1 };
  timeline.push(item);
  return item;
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

  // A provider id identifies one invocation, even when several invocations
  // share the same tool name in one model turn. Falling through to the
  // name-based compatibility path here collapses parallel executor calls into
  // the first unresolved call and leaves their responses as phantom rows.
  if (event.id) return undefined;

  // Only legacy calls without an id or durable executor identity can be
  // paired by order. This keeps the best-effort behavior scoped to the
  // protocol shape that actually needs it.
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
    // Every invocation owns its chronological slot. Backend action ids remain
    // metadata, but never merge a later invocation into an earlier slot: text
    // or another activity may have appeared between the two calls.
    action = {
      type: "activity_action",
      timelineId: nextTimelineItemId(timeline, "action"),
      id: event.id || nextTimelineItemId(timeline, "action-id"),
      backendActionId: actionMetadata(event),
      activityId: event.activityId || null,
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
    timeline.push(action);
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
