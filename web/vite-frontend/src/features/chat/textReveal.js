const DEFAULT_TAIL_LENGTH = 14;
// Baseline characters/sec while streaming. The adaptive term in revealRate
// smooths out bursty token delivery so the head never visibly stalls then
// jumps; it eases toward the backlog instead of slamming into it.
const NORMAL_REVEAL_RATE = 48;
const FINISHED_REVEAL_RATE = 320;

export function splitGraphemes(text) {
  if (typeof Intl?.Segmenter === "function") {
    return [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)]
      .map(({ segment }) => segment);
  }
  return Array.from(text);
}

// The rate depends only on how far the visual head has fallen behind the
// received buffer. It deliberately has no knowledge of when chunks arrived.
export function revealRate(bufferedCharacters, finished = false) {
  const backlog = Math.max(0, bufferedCharacters);
  if (finished) {
    // Once the stream is done, drain the remaining buffer quickly but still
    // smoothly — no abrupt "snap to full text".
    return Math.min(900, FINISHED_REVEAL_RATE + backlog * 18);
  }
  // Sub-linear (sqrt-ish) catch-up with no hard threshold: the curve is
  // continuous from backlog 0, so the head eases forward instead of stalling
  // below a cutoff then lurching once it is crossed.
  return Math.min(260, NORMAL_REVEAL_RATE + backlog ** 0.85 * 2.2);
}

export function revealTailStyle(distance, tailLength = DEFAULT_TAIL_LENGTH) {
  const progress = Math.max(0, Math.min(1, distance / Math.max(1, tailLength - 1)));
  // smoothstep keeps the beginning especially quiet, then resolves smoothly.
  const eased = progress * progress * (3 - 2 * progress);
  return {
    opacity: 0.08 + 0.92 * eased,
    blur: 0.8 * (1 - eased),
    offset: 1 * (1 - eased),
  };
}

/**
 * Paint a stream as stable text plus a very small, computed reveal tail.
 *
 * append() may be called with arbitrary LLM chunks. Those chunks are only
 * received here; a requestAnimationFrame loop advances the visual head at its
 * own elapsed-time-based rate. The DOM never has more than `tailLength`
 * animated character spans.
 */
export function createSmoothTextReveal(element, { tailLength = DEFAULT_TAIL_LENGTH, cursor = false, onComplete = null } = {}) {
  let completeCallback = onComplete;
  const characters = [];
  const staticText = document.createTextNode("");
  const tail = document.createElement("span");
  tail.className = "text-reveal-tail";
  tail.setAttribute("aria-hidden", "true");
  element.replaceChildren(staticText, tail);
  element.classList.add("text-reveal");
  if (cursor) element.classList.add("text-reveal-cursor");

  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  let committed = 0;
  let head = -1;
  let finished = false;
  let frameId = null;
  let lastFrameAt = null;
  let cancelled = false;
  // Reused tail spans: paint() only mutates style of existing nodes and
  // appends/removes at the edges, instead of rebuilding the whole tail.
  const tailSpans = [];

  function visibleCount() {
    return Math.min(characters.length, Math.max(0, Math.floor(head) + 1));
  }

  function paint() {
    const visible = visibleCount();
    const nextCommitted = Math.min(
      visible,
      Math.max(committed, Math.max(0, Math.floor(head) - tailLength + 1)),
    );
    if (nextCommitted > committed) {
      staticText.appendData(characters.slice(committed, nextCommitted).join(""));
      committed = nextCommitted;
    }

    // Shrink: the head moved past some tail spans.
    while (tailSpans.length && tailSpans[0]._index < committed) {
      tailSpans.shift().remove();
    }
    // Grow: newly visible characters need spans.
    const fragment = document.createDocumentFragment();
    for (let index = committed + tailSpans.length; index < visible; index += 1) {
      const span = document.createElement("span");
      span.className = "text-reveal-char";
      span.textContent = characters[index];
      span._index = index;
      tailSpans.push(span);
      fragment.append(span);
    }
    if (fragment.childNodes.length) tail.append(fragment);
    // Update styles in place — cheap, no layout. The spans stay inline so
    // whitespace/newlines behave naturally; only opacity + blur animate.
    for (const span of tailSpans) {
      const style = revealTailStyle(head - span._index, tailLength);
      span.style.opacity = style.opacity.toFixed(3);
      span.style.filter = style.blur > 0.02 ? `blur(${style.blur.toFixed(3)}px)` : "none";
    }

    // The tail is hidden from assistive tech so changing its spans does not
    // announce each character. The accessible value follows the visible text.
    element.setAttribute("aria-label", characters.slice(0, visible).join(""));
  }

  function complete() {
    staticText.data = characters.join("");
    committed = characters.length;
    tailSpans.length = 0;
    tail.replaceChildren();
    tail.remove();
    element.removeAttribute("aria-label");
    element.classList.remove("text-reveal", "text-reveal-cursor");
    frameId = null;
    lastFrameAt = null;
    completeCallback?.();
  }

  function schedule() {
    if (cancelled || reducedMotion || frameId !== null) return;
    frameId = requestAnimationFrame(step);
  }

  function step(now) {
    frameId = null;
    if (cancelled) return;
    if (lastFrameAt === null) lastFrameAt = now;
    const deltaTime = Math.min(100, Math.max(0, now - lastFrameAt));
    lastFrameAt = now;

    const lastCharacter = characters.length - 1;
    if (lastCharacter < 0) {
      if (finished) complete();
      return;
    }
    // After completion, advance the head through an invisible tail-length
    // runway. This resolves the last visible characters using the same
    // distance-to-head rule rather than adding per-character finish timers.
    //
    // While streaming, the same runway applies once delivery pauses: when the
    // buffer runs dry (e.g. the model stops to call a tool), let the head
    // walk the tail runway so the trailing characters fully resolve instead
    // of freezing mid-fade. While backlog exists the head stays capped at
    // lastCharacter, preserving the typewriter's trailing-fade rhythm.
    const backlogDry = lastCharacter - head < 1;
    const targetHead = (finished || backlogDry) ? lastCharacter + tailLength : lastCharacter;
    if (head < targetHead) {
      const bufferedCharacters = Math.max(0, lastCharacter - head);
      head = Math.min(targetHead, head + revealRate(bufferedCharacters, finished) * deltaTime / 1000);
      paint();
    }

    if (head < targetHead) schedule();
    else if (finished) complete();
    else lastFrameAt = null;
  }

  function append(chunk) {
    if (cancelled || finished || !chunk) return;
    characters.push(...splitGraphemes(String(chunk)));
    if (reducedMotion) {
      head = characters.length - 1;
      staticText.appendData(characters.slice(committed).join(""));
      committed = characters.length;
      return;
    }
    // After a delivery pause the head may have walked the tail runway past
    // the old end. Pull it back to just behind the newly appended text so
    // the fresh content fades in with the normal rhythm instead of appearing
    // instantly fully-resolved (distance > tailLength renders opacity 1).
    const lastCharacter = characters.length - 1;
    if (head > lastCharacter) head = Math.max(committed - 1, lastCharacter - tailLength + 1);
    schedule();
  }

  function finish() {
    if (cancelled || finished) return;
    finished = true;
    if (reducedMotion) {
      complete();
      return;
    }
    schedule();
  }

  function cancel() {
    cancelled = true;
    if (frameId !== null) cancelAnimationFrame(frameId);
    frameId = null;
    lastFrameAt = null;
    tailSpans.length = 0;
    element.classList.remove("text-reveal", "text-reveal-cursor");
    element.removeAttribute("aria-label");
  }

  // Replace the received buffer with a new cumulative snapshot while keeping
  // the visual state (committed text node + animated tail + head position)
  // untouched. Call this when the provider replays a snapshot that is not a
  // clean append of what was received before — e.g. it carries a transport
  // separator prefix or mid-text compaction. The caller must guarantee that
  // the first `visibleCount()` graphemes of `nextText` match what is already
  // on screen; otherwise the tail spans would show stale characters.
  //
  // Returns true when the reset was applied, false when the snapshot is
  // incompatible with the visible text and the caller should rebuild.
  function resetReceived(nextText) {
    if (cancelled) return false;
    const nextCharacters = splitGraphemes(String(nextText || ""));
    const visible = visibleCount();
    for (let index = 0; index < visible; index += 1) {
      if (nextCharacters[index] !== characters[index]) return false;
    }
    characters.length = 0;
    characters.push(...nextCharacters);
    // If the new snapshot is shorter than the head had already revealed,
    // clamp the head so the typewriter does not run past the new end. The
    // visible characters themselves stay — they were verified identical.
    const lastCharacter = characters.length - 1;
    if (head > lastCharacter) head = lastCharacter;
    return true;
  }

  return {
    append,
    finish,
    cancel,
    resetReceived,
    set onComplete(callback) { completeCallback = callback; },
    get cancelled() { return cancelled; },
    get received() { return characters.join(""); },
    get receivedLength() { return characters.length; },
    get visibleLength() { return visibleCount(); },
  };
}
