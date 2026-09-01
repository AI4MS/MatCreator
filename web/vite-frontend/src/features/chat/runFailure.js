function failureDetail(error) {
  const raw = String(error?.message || error || "Agent run failed").trim();
  return raw.replace(/^Error:\s*/i, "") || "Agent run failed";
}

export function runFailureMarkdown(error) {
  return [
    "⚠️ **The agent could not complete this response.**",
    failureDetail(error),
    "This failure was isolated to this chat. You can retry your message.",
  ].join("\n\n");
}

/** Add a terminal failure to the request-owned assistant model exactly once. */
export function appendRunFailure(message, error) {
  if (!message || message.runFailure) return false;
  const text = runFailureMarkdown(error);
  message.runFailure = failureDetail(error);
  message.items.push({
    type: "text",
    timelineId: `${message.id}:run-failure`,
    text,
    renderRevision: 1,
    runFailure: true,
  });
  message.revision += 1;
  return true;
}

export function markRunFailureView(view) {
  view?.element?.classList?.add("has-run-error");
}
