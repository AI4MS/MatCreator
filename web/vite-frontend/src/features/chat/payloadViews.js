export function createPayloadView(payload) {
  if (payload === null || payload === undefined) {
    const empty = document.createElement("span");
    empty.className = "payload-value payload-value-empty";
    empty.textContent = payload === null ? "null" : "Not available";
    return empty;
  }

  if (Array.isArray(payload)) {
    const list = document.createElement("div");
    list.className = "payload-list";
    payload.forEach((item) => {
      const row = document.createElement("div");
      row.className = "payload-list-item";
      row.appendChild(createPayloadView(item));
      list.appendChild(row);
    });
    if (!payload.length) list.textContent = "Empty list";
    return list;
  }

  if (typeof payload === "object") {
    const fields = document.createElement("div");
    fields.className = "payload-fields";
    Object.entries(payload).forEach(([key, value]) => {
      const row = document.createElement("div");
      row.className = "payload-field";
      const label = document.createElement("span");
      label.className = "payload-key";
      label.textContent = key;
      row.append(label, createPayloadView(value));
      fields.appendChild(row);
    });
    if (!fields.childElementCount) fields.textContent = "Empty object";
    return fields;
  }

  const value = document.createElement("span");
  value.className = `payload-value payload-value-${typeof payload}`;
  value.textContent = typeof payload === "string" ? payload : String(payload);
  return value;
}

export function createPayloadBlock(payload, empty = "Not available") {
  const block = document.createElement("div");
  block.className = "payload-block";
  block.appendChild(createPayloadView(payload === undefined ? empty : payload));
  return block;
}

function deferredDetailUrl(detail) {
  return `/api/users/${encodeURIComponent(detail.user_id)}`
    + `/sessions/${encodeURIComponent(detail.session_id)}`
    + `/events/${detail.row_id}/parts/${detail.part_index}/detail`;
}

export function createToolCallRawView(toolCall, { fetchImpl = globalThis.fetch } = {}) {
  const body = document.createElement("div");
  body.className = "tool-call-raw";

  const addPayload = (label, payload, empty = "Not available") => {
    const section = document.createElement("section");
    const heading = document.createElement("div");
    heading.className = "tool-call-raw-label";
    heading.textContent = label;
    section.appendChild(heading);

    const deferred = payload?._matcreator_deferred_detail;
    if (!deferred) {
      section.appendChild(createPayloadBlock(payload ?? empty));
      body.appendChild(section);
      return;
    }

    const preview = { ...payload };
    delete preview._matcreator_deferred_detail;
    if (Object.keys(preview).length) section.appendChild(createPayloadBlock(preview));
    const loadButton = document.createElement("button");
    loadButton.type = "button";
    loadButton.className = "ghost tool-detail-load";
    loadButton.textContent = `Load full output (${Math.ceil((deferred.byte_size || 0) / 1024)} KB)`;
    loadButton.addEventListener("click", async () => {
      loadButton.disabled = true;
      loadButton.textContent = "Loading output…";
      try {
        const response = await fetchImpl(deferredDetailUrl(deferred));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const detail = await response.json();
        section.replaceChildren(heading, createPayloadBlock(detail.response));
      } catch (error) {
        loadButton.disabled = false;
        loadButton.textContent = `Retry full output (${error.message})`;
      }
    });
    section.appendChild(loadButton);
    body.appendChild(section);
  };

  if (toolCall.error) addPayload("Error", toolCall.error);
  addPayload("Input", toolCall.input, "No input payload");
  addPayload("Output", toolCall.output, "Awaiting output");
  return body;
}

export function createActionRawView(action, options) {
  const body = document.createElement("div");
  body.className = "activity-action-raw";
  action.toolCalls.forEach((call) => {
    const section = document.createElement("section");
    if (action.toolCalls.length > 1) {
      const heading = document.createElement("div");
      heading.className = "tool-call-raw-label";
      heading.textContent = call.name;
      section.appendChild(heading);
    }
    section.appendChild(createToolCallRawView(call, options));
    body.appendChild(section);
  });
  return body;
}
