const PHASES = Object.freeze({
  connecting: ["Connecting to MatCreator…", "thinking"],
  connected: ["Connected — MatCreator is working…", "thinking"],
  saving: ["Saving the response…", "thinking"],
  working: ["MatCreator is working. Please wait…", "thinking"],
  thinking: ["MatCreator is thinking…", "thinking"],
  planning: ["MatCreator is planning the workflow…", "thinking"],
  finalizing_plan: ["Plan validated — preparing it for review…", "thinking"],
  searching: ["MatCreator is searching for information…", "searching"],
  executing: ["MatCreator is executing the workflow…", "computing"],
  computing: ["MatCreator is computing…", "computing"],
});

export function createAgentStatusController({
  chatArea,
  runningIndicator,
  runningText,
  sendButton,
  orbitalIndicator,
  getActiveRequest,
  requestHasActiveRun,
  requestPresentsLiveTurn,
}) {
  function attach(messageView) {
    const message = messageView?.element
      || messageView?.closest?.(".agent-message:not(.step-feed-message)");
    if (!runningIndicator) return;
    if (!message) {
      runningIndicator.setAttribute("aria-hidden", "true");
      return;
    }
    const meta = message.querySelector(".agent-bubble-meta");
    (meta || message).prepend(runningIndicator);
    const timelineElement = messageView?.timelineElement || messageView;
    const hasPresentedContent = timelineElement?.querySelector?.(
      ":scope > .timeline-segment, .delegation-group .step-feed-message",
    );
    if (!hasPresentedContent) message.classList.add("is-waiting");
    message.classList.remove("is-pending");
  }

  function ensureAttached() {
    if (!runningIndicator || runningIndicator.isConnected) return;
    const message = [...chatArea.querySelectorAll(
      ".agent-message:not(.step-feed-message)",
    )].at(-1);
    if (!message) {
      runningIndicator.setAttribute("aria-hidden", "true");
      return;
    }
    const meta = message.querySelector(".agent-bubble-meta");
    (meta || message).prepend(runningIndicator);
  }

  function updatePhase(phase = "working") {
    const [label, orbitalState] = PHASES[phase] || PHASES.working;
    if (runningText) runningText.textContent = label;
    orbitalIndicator?.render(orbitalState);
  }

  function updateSendButton() {
    const request = getActiveRequest();
    const running = requestHasActiveRun(request);
    const presenting = requestPresentsLiveTurn(request);
    const activeAgentMessage = [...chatArea.querySelectorAll(
      ".agent-message:not(.step-feed-message)",
    )].at(-1);
    if (presenting && activeAgentMessage) ensureAttached();
    const indicatorVisible = Boolean(
      presenting && runningIndicator?.closest(".agent-message:not(.step-feed-message)"),
    );
    runningIndicator?.setAttribute("aria-hidden", String(!indicatorVisible));
    if (!running) updatePhase();
    if (!sendButton) return;
    const queued = Boolean(request?.followupQueued);
    sendButton.disabled = queued;
    sendButton.textContent = queued ? "…" : running ? "■" : "➜";
    sendButton.title = queued ? "Sending after message sync…" : running ? "Stop" : "Send";
    sendButton.setAttribute("aria-label", sendButton.title);
    sendButton.classList.toggle("is-stopping", running);
    sendButton.classList.toggle("is-finalizing", queued);
  }

  function destroy() {
    orbitalIndicator?.unmount?.();
  }

  return { attach, ensureAttached, updatePhase, updateSendButton, destroy };
}
