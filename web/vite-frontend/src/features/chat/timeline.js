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

export function upsertTimelineThought(timeline, text) {
  if (!text) return;
  const compacted = compactRepeatedPrefixSnapshots(text);
  const last = timeline[timeline.length - 1];
  if (last?.type === "thought") {
    last.text = compactRepeatedPrefixSnapshots(mergeReplayedText(last.text || "", compacted));
    return;
  }
  timeline.push({ type: "thought", text: compacted });
}

export function upsertTimelineText(timeline, text) {
  for (let index = timeline.length - 1; index >= 0; index--) {
    if (timeline[index].type === "text") timeline.splice(index, 1);
  }
  if (text) timeline.push({ type: "text", text });
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
      timeline[index] = event;
      return;
    }
  }
  const last = timeline[timeline.length - 1];
  if (last && JSON.stringify(last) === JSON.stringify(event)) return;
  timeline.push(event);
}