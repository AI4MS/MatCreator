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
  if (last?.type === "thought") {
    last.text = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    return;
  }
  timeline.push({ type: "thought", timelineId: nextTimelineItemId(timeline, "thought"), text: compacted });
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

function timelineEventKey(event) {
  if (event.id) return `${event.type}:${event.id}`;
  const payload = event.type === "function_call" ? event.args : event.response;
  return `${event.type}:${event.name || "Unknown"}:${JSON.stringify(payload || {})}`;
}

export function upsertTimelineEvent(timeline, event) {
  const eventKey = timelineEventKey(event);
  for (let index = 0; index < timeline.length; index++) {
    const item = timeline[index];
    if (
      (item.type === "function_call" || item.type === "function_response") &&
      timelineEventKey(item) === eventKey
    ) {
      // Keep the UI identity when an incoming event refreshes an existing
      // call/response. Its expanded state must survive the refresh.
      event.timelineId = item.timelineId;
      timeline[index] = event;
      return;
    }
  }
  const last = timeline[timeline.length - 1];
  if (last && JSON.stringify(last) === JSON.stringify(event)) return;
  event.timelineId ||= nextTimelineItemId(timeline, event.type);
  timeline.push(event);
}
