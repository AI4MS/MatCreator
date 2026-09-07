const defaultClock = () => performance.now();
const defaultFrame = (callback) => requestAnimationFrame(callback);
const defaultCancelFrame = (frame) => cancelAnimationFrame(frame);
const defaultDelay = (callback, delay) => window.setTimeout(callback, delay);
const defaultCancelDelay = (timer) => window.clearTimeout(timer);

export function messageRenderInterval(textLength) {
  const length = Math.max(0, Number(textLength) || 0);
  if (length <= 8_000) return 80;
  if (length <= 32_000) return 120;
  return 200;
}

/** Coalesces all mutations for one message into one affected-message render. */
export function createMessageRenderScheduler({
  render,
  intervalMs = 80,
  clock = defaultClock,
  scheduleFrame = defaultFrame,
  cancelFrame = defaultCancelFrame,
  scheduleDelay = defaultDelay,
  cancelDelay = defaultCancelDelay,
} = {}) {
  let dirty = false;
  let frame = null;
  let timer = null;
  let lastRenderAt = -Infinity;
  let finishPromise = null;
  let resolveFinish = null;
  let cancelled = false;

  const clearScheduled = () => {
    if (timer !== null) cancelDelay(timer);
    if (frame !== null) cancelFrame(frame);
    timer = null;
    frame = null;
  };

  const flush = () => {
    clearScheduled();
    if (cancelled || !dirty) return false;
    dirty = false;
    lastRenderAt = clock();
    render();
    return true;
  };

  const queueFrame = () => {
    timer = null;
    frame = scheduleFrame(() => {
      frame = null;
      flush();
    });
  };

  const request = () => {
    if (cancelled) return;
    dirty = true;
    if (frame !== null || timer !== null || finishPromise) return;
    const cadence = typeof intervalMs === "function" ? intervalMs() : intervalMs;
    const delay = Math.max(0, cadence - (clock() - lastRenderAt));
    if (delay > 0) timer = scheduleDelay(queueFrame, delay);
    else queueFrame();
  };

  const finish = () => {
    if (finishPromise) return finishPromise;
    dirty = true;
    clearScheduled();
    // Completion state is committed by the caller first. Defer the guaranteed
    // final Markdown pass to a fresh task so status teardown is not owned by
    // Markdown parsing or layout work.
    finishPromise = new Promise((resolve) => {
      resolveFinish = resolve;
      timer = scheduleDelay(() => {
        timer = null;
        flush();
        resolveFinish = null;
        resolve();
      }, 0);
    });
    return finishPromise;
  };

  const cancel = () => {
    if (cancelled) return;
    cancelled = true;
    dirty = false;
    clearScheduled();
    resolveFinish?.();
    resolveFinish = null;
  };

  return { request, flush, finish, cancel, get pending() { return dirty; } };
}
