import assert from "node:assert/strict";
import test from "node:test";

import { createMessageRenderScheduler, messageRenderInterval } from "../src/features/chat/messageRenderScheduler.js";

function timingHarness() {
  let nextId = 1;
  const callbacks = new Map();
  const schedule = (callback) => {
    const id = nextId++;
    callbacks.set(id, callback);
    return id;
  };
  const cancel = (id) => callbacks.delete(id);
  const runNext = (now = 0) => {
    const [id, callback] = callbacks.entries().next().value || [];
    if (!callback) return false;
    callbacks.delete(id);
    callback(now);
    return true;
  };
  return { callbacks, schedule, cancel, runNext };
}

test("coalesces rapid message mutations into one affected-message render", () => {
  const timing = timingHarness();
  let renders = 0;
  const scheduler = createMessageRenderScheduler({
    render: () => { renders += 1; },
    clock: () => 100,
    scheduleFrame: timing.schedule,
    cancelFrame: timing.cancel,
    scheduleDelay: timing.schedule,
    cancelDelay: timing.cancel,
  });

  for (let index = 0; index < 1_000; index += 1) scheduler.request();
  assert.equal(timing.callbacks.size, 1);
  timing.runNext(100);
  assert.equal(renders, 1);
  assert.equal(scheduler.pending, false);
});

test("long messages trade render frequency for bounded main-thread work", () => {
  assert.equal(messageRenderInterval(1_000), 80);
  assert.equal(messageRenderInterval(20_000), 120);
  assert.equal(messageRenderInterval(200_000), 200);
});

test("completion schedules one final pass and is idempotent", async () => {
  const timing = timingHarness();
  let renders = 0;
  const scheduler = createMessageRenderScheduler({
    render: () => { renders += 1; },
    clock: () => 100,
    scheduleFrame: timing.schedule,
    cancelFrame: timing.cancel,
    scheduleDelay: timing.schedule,
    cancelDelay: timing.cancel,
  });

  scheduler.request();
  const first = scheduler.finish();
  const second = scheduler.finish();
  assert.strictEqual(first, second);
  assert.equal(timing.callbacks.size, 1);
  timing.runNext();
  await first;
  assert.equal(renders, 1);
});

test("cancelling a scheduled final pass releases completion waiters", async () => {
  const timing = timingHarness();
  const scheduler = createMessageRenderScheduler({
    render: () => assert.fail("cancelled scheduler should not render"),
    clock: () => 100,
    scheduleFrame: timing.schedule,
    cancelFrame: timing.cancel,
    scheduleDelay: timing.schedule,
    cancelDelay: timing.cancel,
  });

  const finished = scheduler.finish();
  scheduler.cancel();
  await finished;
  assert.equal(timing.callbacks.size, 0);
});
