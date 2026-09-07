import assert from "node:assert/strict";
import test from "node:test";

import { revealRate, revealTailStyle } from "../src/features/chat/textReveal.js";

test("text reveal accelerates only as buffered text accumulates", () => {
  assert.equal(revealRate(0), 48);
  assert.ok(revealRate(24) > revealRate(12));
  assert.ok(revealRate(24, true) > revealRate(24));
});

test("text reveal rate is smooth — no abrupt jumps as backlog grows", () => {
  // The whole point of the sub-linear, threshold-free curve: adjacent
  // backlog values must produce adjacent rates (bounded delta), so the head
  // eases forward instead of stalling then lurching. Sample a dense sweep
  // and assert the largest single-step jump stays small.
  let previous = revealRate(0);
  let maxJump = 0;
  for (let backlog = 1; backlog <= 400; backlog += 1) {
    const rate = revealRate(backlog);
    maxJump = Math.max(maxJump, rate - previous);
    previous = rate;
  }
  // 2.2 * (b^0.85 - (b-1)^0.85) is largest at b=1 (~2.2) and decays after;
  // the cap at 260 flattens the tail. Assert no step exceeds a few chars/s.
  assert.ok(maxJump < 3, `rate jump ${maxJump} should be gradual`);
  // Monotonic non-decreasing over the whole sweep.
  for (let backlog = 1; backlog <= 400; backlog += 1) {
    assert.ok(revealRate(backlog) >= revealRate(backlog - 1));
  }
});

test("finished reveal drains fast but stays bounded", () => {
  // After [DONE], remaining buffered text should resolve quickly — never a
  // visible stall — but the rate is still capped so the tail animates rather
  // than snapping to the full text.
  assert.ok(revealRate(0, true) >= 300);
  assert.ok(revealRate(10_000, true) <= 900);
});

test("text reveal tail uses a nonlinear resolving gradient", () => {
  const newest = revealTailStyle(0, 10);
  const early = revealTailStyle(3, 10);
  const oldest = revealTailStyle(9, 10);

  assert.equal(newest.opacity, 0.08);
  assert.equal(newest.blur, 0.8);
  assert.equal(newest.offset, 1);
  assert.equal(oldest.opacity, 1);
  assert.equal(oldest.blur, 0);
  assert.equal(oldest.offset, 0);
  // At one third, smoothstep is deliberately softer than linear opacity.
  assert.ok(early.opacity < 0.08 + 0.92 / 3);
});
