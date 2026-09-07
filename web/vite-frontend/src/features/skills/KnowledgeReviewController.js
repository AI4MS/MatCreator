const REVIEW_STATUSES = new Set(["idle", "running", "failed", "completed", "completed_with_errors"]);

export function knowledgeReviewPresentation(review = {}) {
  const requestedStatus = String(review.status || "idle").toLowerCase();
  const status = REVIEW_STATUSES.has(requestedStatus) ? requestedStatus : "idle";
  const running = status === "running";
  const progress = review.progress || {};
  const results = Array.isArray(review.results) ? review.results : [];
  const errors = Array.isArray(review.errors) ? review.errors : [];
  const detail = review.summary || errors[0];
  const title = detail
    ? `${detail}${running ? "" : " Click to review memory and graph nodes."}`
    : running ? "Knowledge review is running" : "Click to review memory and graph nodes";

  if (running) {
    const phase = review.phase === "graph" ? "graph nodes" : "memory";
    const total = Number(progress.total || 0);
    return {
      status,
      running,
      title,
      text: total
        ? `Reviewing ${phase}: ${progress.completed || 0}/${total} (${progress.percent || 0}%)`
        : `Starting ${phase} review`,
    };
  }
  if (status === "failed") {
    return {
      status,
      running,
      title,
      text: `Review failed: ${errors[0] || "unknown error"} · click to retry`,
    };
  }
  if (status === "completed" || status === "completed_with_errors") {
    const memoryCount = results.filter((item) => item.phase === "memory").length;
    const graphCount = results.filter((item) => item.phase === "graph").length;
    const warning = errors.length
      ? `, ${errors.length} ${errors.length === 1 ? "error" : "errors"}`
      : "";
    const summary = review.summary?.trim();
    return {
      status,
      running,
      title,
      text: memoryCount === 0 && graphCount === 0 && summary
        ? `${summary}${warning} · click to run again`
        : `Review complete: ${memoryCount} memory, ${graphCount} graph actions${warning} · click to run again`,
    };
  }
  return {
    status,
    running,
    title,
    text: "Review memory and graph · click to start",
  };
}

function createBanner(documentRef) {
  const element = documentRef.createElement("button");
  element.className = "knowledge-review-banner status-idle";
  element.id = "knowledge-review-banner";
  element.type = "button";
  element.setAttribute("aria-live", "polite");
  element.title = "Click to review memory and graph nodes";
  const spinner = documentRef.createElement("span");
  spinner.className = "knowledge-review-spinner hidden";
  spinner.id = "knowledge-review-spinner";
  const text = documentRef.createElement("span");
  text.id = "knowledge-review-text";
  text.textContent = "Review Know-Do Graph";
  element.append(spinner, text);
  return { element, spinner, text };
}

export function createKnowledgeReviewController({
  getSessionId,
  fetchImpl = globalThis.fetch,
  documentRef = globalThis.document,
  windowRef = globalThis.window,
}) {
  const { element, spinner, text } = createBanner(documentRef);
  let initialized = false;
  let pollTimer = null;
  let startRequestController = null;
  let refreshRequest = null;

  function stopPolling() {
    if (pollTimer !== null) windowRef.clearInterval(pollTimer);
    pollTimer = null;
  }

  function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = windowRef.setInterval(refresh, 2000);
  }

  function render(review = {}) {
    const presentation = knowledgeReviewPresentation(review);
    const { status, running } = presentation;
    element.disabled = running;
    element.className = `knowledge-review-banner status-${status}`;
    element.title = presentation.title;
    spinner.classList.toggle("hidden", !running);
    text.textContent = presentation.text;
    if (!running) stopPolling();
  }

  async function refresh() {
    if (refreshRequest) return refreshRequest.promise;
    const controller = new AbortController();
    const request = { controller, promise: null };
    refreshRequest = request;
    request.promise = Promise.resolve().then(async () => {
      try {
        const response = await fetchImpl("/api/knowledge-review/status", {
          signal: controller.signal,
        });
        if (!response.ok) return null;
        const review = await response.json();
        render(review);
        if (review.status === "running") startPolling();
        return review;
      } catch (_) {
        return null;
      } finally {
        if (refreshRequest === request) refreshRequest = null;
      }
    });
    return request.promise;
  }

  async function start() {
    if (element.disabled) return false;
    stopPolling();
    refreshRequest?.controller.abort();
    refreshRequest = null;
    startRequestController?.abort();
    const controller = new AbortController();
    startRequestController = controller;
    render({ status: "running", phase: "memory", message: "Starting Know-Do Graph review." });
    try {
      const response = await fetchImpl("/api/knowledge-review/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: getSessionId() }),
        signal: controller.signal,
      });
      const review = await response.json().catch(() => ({}));
      if (!response.ok) {
        render({ status: "failed", errors: [review.detail || `HTTP ${response.status}`] });
        return false;
      }
      render(review);
      if (review.status === "running") startPolling();
      return true;
    } catch (error) {
      if (error.name !== "AbortError") {
        render({ status: "failed", errors: ["Could not reach the review service"] });
      }
      return false;
    } finally {
      if (startRequestController === controller) startRequestController = null;
    }
  }

  const handleClick = () => void start();

  function init() {
    if (initialized) return;
    initialized = true;
    element.addEventListener("click", handleClick);
    void refresh();
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    startRequestController?.abort();
    refreshRequest?.controller.abort();
    startRequestController = null;
    refreshRequest = null;
    stopPolling();
    element.removeEventListener("click", handleClick);
  }

  return { element, init, destroy, render, refresh, start };
}
