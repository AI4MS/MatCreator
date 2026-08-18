import { marked } from "marked";

const BOX_RE = /[┌┐└┘├┤┬┴┼│━─]/;
const CJK_RE = /[一-鿿㐀-䶿豈-﫿　-〿＀-￯]/;
const AGENT_AVATAR_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="rgba(148,163,184,0.9)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="8" width="18" height="11" rx="2"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/><circle cx="9" cy="14" r="1" fill="rgba(148,163,184,0.9)" stroke="none"/><circle cx="15" cy="14" r="1" fill="rgba(148,163,184,0.9)" stroke="none"/><path d="M7 19v2M17 19v2"/>
</svg>`;
const USER_AVATAR_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="rgba(59,130,246,0.9)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
</svg>`;
// Native scrolling commonly stops a few fractional pixels short of the
// mathematical maximum (and trackpads can finish between scroll events).
// Treat the bottom composer-sized reading zone as attached to the bottom.
const BOTTOM_ATTACH_THRESHOLD = 80;

export function createChatRenderer({ chatArea, bottomOverlay = null }) {
  let asciiWidth = 0;
  let cjkWidth = 0;
  let userScrollIntent = 0;
  let pendingScrollFrame = null;
  let pendingRestoreFrame = null;
  let pendingRestoreSnapshot = null;
  let pendingBottomRequest = null;
  let bottomReserve = 0;
  let observedBottomDialog = null;
  // User intent is the source of truth. Geometry can change underneath the
  // viewport while content streams, so "near bottom" must not double as the
  // follow-mode state.
  let userDetached = false;
  let viewportModeVersion = 0;
  let pointerScrollActive = false;
  let lastUserScrollIntentAt = 0;
  let lastScrollTop = chatArea.scrollTop;
  let scrollTransaction = null;

  function cancelScheduledPlacement() {
    if (pendingScrollFrame !== null) cancelAnimationFrame(pendingScrollFrame);
    if (pendingRestoreFrame !== null) cancelAnimationFrame(pendingRestoreFrame);
    pendingScrollFrame = null;
    pendingRestoreFrame = null;
    pendingBottomRequest = null;
    pendingRestoreSnapshot = null;
  }

  function enterBottomFollow() {
    if (!userDetached) return;
    userDetached = false;
    viewportModeVersion += 1;
    // A detached-mode restore may have been queued by a content update after
    // the user's wheel/pointer event but before its resulting scroll event.
    // Reaching the bottom supersedes that anchor unconditionally.
    if (pendingRestoreFrame !== null) cancelAnimationFrame(pendingRestoreFrame);
    pendingRestoreFrame = null;
    pendingRestoreSnapshot = null;
  }

  const bottomDialogObserver = new ResizeObserver(() => {
    // Dialog growth (streaming text, details, images) changes where the
    // bottom is, but must never change the size of the bottom spacer.
    if (isChatBottomPinned()) scrollToBottom({ preserveUserPosition: true });
  });

  function getBottomDialog() {
    return [...chatArea.children].reverse().find((element) => (
      element.matches?.(".message:not(.is-pending)") && !element.classList.contains("hidden")
    )) || null;
  }

  function observeBottomDialog() {
    const dialog = getBottomDialog();
    if (dialog === observedBottomDialog) return dialog;
    if (observedBottomDialog) bottomDialogObserver.unobserve(observedBottomDialog);
    observedBottomDialog = dialog;
    if (observedBottomDialog) bottomDialogObserver.observe(observedBottomDialog);
    return dialog;
  }

  function syncBottomReserve({ followBottom = true } = {}) {
    const overlayHeight = bottomOverlay?.getBoundingClientRect().height || 0;
    observeBottomDialog();
    // The input area is absolutely positioned 16px above the panel bottom.
    // The message's own bottom margin provides the visual gap.
    // Dialog height is deliberately excluded: adding it creates a large
    // elastic blank area and destabilizes anchors while content streams.
    const reserve = Math.ceil((overlayHeight || 98) + 16);
    if (reserve === bottomReserve) return;
    const shouldStick = followBottom && isChatBottomPinned();
    const readingPosition = shouldStick ? null : captureScrollPosition();
    bottomReserve = reserve;
    chatArea.style.setProperty("--chat-bottom-reserve", `${reserve}px`);
    if (shouldStick) scrollToBottom({ preserveUserPosition: true });
    else restoreScrollPosition(readingPosition);
  }
  if (bottomOverlay) {
    new ResizeObserver(syncBottomReserve).observe(bottomOverlay);
    syncBottomReserve();
  }
  new MutationObserver(() => {
    observeBottomDialog();
    syncBottomReserve();
  }).observe(chatArea, { childList: true, attributes: true, attributeFilter: ["class"], subtree: true });

  ["pointerdown", "touchstart", "wheel", "keydown"].forEach((eventName) => {
    chatArea.addEventListener(eventName, (event) => {
      userScrollIntent += 1;
      // Any real interaction owns the viewport. It invalidates every queued
      // write captured before this gesture; a disclosure may immediately
      // capture a fresh snapshot later in the same event dispatch.
      cancelScheduledPlacement();
      // Pointer presses inside a disclosure are content interactions, not
      // scrollbar drags. Treat only a press targeting the scroll container
      // itself as a possible scrollbar gesture; otherwise our own anchor
      // restore could accidentally re-enable bottom follow before pointerup.
      if (eventName === "pointerdown") {
        pointerScrollActive = event.target === chatArea;
      }
      if (eventName === "touchstart") {
        lastUserScrollIntentAt = performance.now();
      }
      if (eventName === "wheel") {
        lastUserScrollIntentAt = performance.now();
        if (event.deltaY < 0) detachBottomFollow();
        else if (event.deltaY > 0 && isChatNearBottom()) enterBottomFollow();
      }
      if (eventName === "keydown" && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) {
        lastUserScrollIntentAt = performance.now();
        if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
          detachBottomFollow();
        } else if (event.key === "End" || isChatNearBottom()) {
          enterBottomFollow();
        }
      }
    }, { passive: true, capture: true });
  });
  window.addEventListener("pointerup", () => { pointerScrollActive = false; }, { passive: true });
  window.addEventListener("pointercancel", () => { pointerScrollActive = false; }, { passive: true });
  chatArea.addEventListener("scroll", () => {
    // Layout growth and programmatic placement also emit `scroll`. Only an
    // explicit, recent user gesture is allowed to attach/detach bottom mode.
    const currentScrollTop = chatArea.scrollTop;
    if (pointerScrollActive || performance.now() - lastUserScrollIntentAt < 1200) {
      // Upward intent always detaches, even while still inside the generous
      // bottom zone; downward/neutral motion attaches upon entering it. This
      // avoids trapping small wheel steps while retaining reliable attach for
      // scrollbar dragging, momentum and fractional scroll positions.
      if (currentScrollTop < lastScrollTop - 0.5) detachBottomFollow();
      else if (isChatNearBottom()) enterBottomFollow();
      else userDetached = true;
    }
    lastScrollTop = currentScrollTop;
  }, { passive: true });

  function renderMarkdown(text) {
    if (!text) return "";
    let html = marked.parse(text);
    const wrapAsciiArt = (match, inner) => {
      const decoded = inner.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      return BOX_RE.test(decoded) ? `<pre class="ascii-art">${decoded}</pre>` : match;
    };
    html = html.replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/gi, wrapAsciiArt);
    html = html.replace(/<p>([\s\S]*?)<\/p>/gi, wrapAsciiArt);
    return html.replace(
      /<table>([\s\S]*?)<\/table>/gi,
      '<div class="markdown-table-scroll" role="region" aria-label="Scrollable table" tabindex="0"><table>$1</table></div>',
    );
  }

  function unescapeText(text) {
    if (!text) return "";
    return text.replace(/\\\\/g, "\x00").replace(/\\n/g, "\n").replace(/\\t/g, "\t").replace(/\\r/g, "\r").replace(/\\"/g, '"').replace(/\x00/g, "\\");
  }

  function getCharWidths() {
    if (asciiWidth) return { ascii: asciiWidth, cjk: cjkWidth };
    const sample = document.createElement("span");
    sample.style.cssText = "position:absolute;visibility:hidden;font:14px var(--mono);white-space:pre;";
    document.body.appendChild(sample);
    sample.textContent = "x";
    asciiWidth = sample.getBoundingClientRect().width;
    sample.textContent = "中";
    cjkWidth = sample.getBoundingClientRect().width;
    sample.remove();
    return { ascii: asciiWidth, cjk: cjkWidth };
  }

  function measureLine(line) {
    const { ascii, cjk } = getCharWidths();
    return [...line].reduce((width, character) => width + (CJK_RE.test(character) ? cjk : ascii), 0);
  }

  function applyWrapMarkers(pre) {
    const raw = pre.dataset.raw;
    if (!raw) return;
    const containerWidth = pre.clientWidth - 16;
    if (containerWidth <= 0) return;
    const { ascii, cjk } = getCharWidths();
    const markerWidth = ascii * 3;
    const lines = [];
    for (const line of raw.split("\n")) {
      if (measureLine(line) <= containerWidth) {
        lines.push(line);
        continue;
      }
      let width = 0;
      let start = 0;
      for (let index = 0; index < line.length; index += 1) {
        const characterWidth = CJK_RE.test(line[index]) ? cjk : ascii;
        if (width + characterWidth > containerWidth - markerWidth) {
          lines.push(`${line.slice(start, index)} ↵`);
          start = index;
          width = characterWidth;
        } else {
          width += characterWidth;
        }
      }
      if (start < line.length) lines.push(line.slice(start));
    }
    pre.textContent = lines.join("\n");
  }

  function createJsonBlock(content) {
    const pre = document.createElement("pre");
    pre.className = "json-block";
    pre.dataset.raw = unescapeText(content).replace(/^\{\s*/, "").replace(/\s*\}$/, "");
    applyWrapMarkers(pre);
    new ResizeObserver(() => updatePreservingReadingPosition(() => applyWrapMarkers(pre))).observe(pre);
    return pre;
  }

  function markReadingAnchors(root, prefix) {
    if (!root || !prefix) return;
    const blocks = root.matches?.("p, pre, blockquote, li, table, h1, h2, h3, h4, h5, h6")
      ? [root]
      : [...root.querySelectorAll("p, pre, blockquote, li, table, h1, h2, h3, h4, h5, h6")];
    blocks.forEach((block, index) => {
      block.dataset.readingAnchor = `${prefix}:block:${index}`;
    });
  }

  function getUserAvatar() { return localStorage.getItem("user-avatar-url") || null; }
  function applyUserAvatarToEl(element) {
    const url = getUserAvatar();
    element.innerHTML = url ? `<img src="${url}" alt="User">` : USER_AVATAR_SVG;
  }
  function setUserAvatar(dataUrl) {
    localStorage.setItem("user-avatar-url", dataUrl);
    document.querySelectorAll(".user-avatar").forEach(applyUserAvatarToEl);
  }
  function createAgentAvatarEl() {
    const element = document.createElement("div");
    element.className = "message-avatar agent-avatar";
    element.innerHTML = AGENT_AVATAR_SVG;
    return element;
  }
  function createUserAvatarEl() {
    const element = document.createElement("div");
    element.className = "message-avatar user-avatar";
    applyUserAvatarToEl(element);
    return element;
  }
  function scrollToBottom({ preserveUserPosition = false } = {}) {
    // Calling this function is the single transition into bottom-follow mode.
    // Activate synchronously so updates arriving before the next animation
    // frame also follow the bottom. A subsequent user scroll/disclosure
    // interaction atomically cancels the queued placement.
    // Passive render/resize requests may continue following an attached
    // viewport, but they must never reattach a reader who has scrolled away.
    if (preserveUserPosition && userDetached) return;
    enterBottomFollow();
    if (pendingRestoreFrame !== null) cancelAnimationFrame(pendingRestoreFrame);
    pendingRestoreFrame = null;
    pendingRestoreSnapshot = null;
    if (!pendingBottomRequest) {
      pendingBottomRequest = { preserveUserPosition, userIntent: userScrollIntent };
    } else if (!preserveUserPosition) {
      // An explicit placement (for example, the user's own message) wins over
      // passive streaming requests that should yield to user scroll input.
      pendingBottomRequest.preserveUserPosition = false;
      pendingBottomRequest.userIntent = userScrollIntent;
    }
    if (scrollTransaction) {
      scrollTransaction.bottomRequested = true;
      scrollTransaction.preserveUserPosition &&= preserveUserPosition;
      return;
    }
    if (pendingScrollFrame !== null) return;

    // MutationObserver, ResizeObserver and the renderer can all request a
    // placement for the same DOM update. Coalesce them into one layout-frame
    // write so no intermediate scroll position is painted.
    pendingScrollFrame = requestAnimationFrame(() => {
      pendingScrollFrame = null;
      const request = pendingBottomRequest;
      pendingBottomRequest = null;
      if (!request) return;
      if (request.preserveUserPosition && userScrollIntent !== request.userIntent) return;
      syncBottomReserve({ followBottom: false });
      const target = Math.max(0, chatArea.scrollHeight - chatArea.clientHeight);
      if (Math.abs(chatArea.scrollTop - target) > 0.5) {
        chatArea.scrollTop = target;
        lastScrollTop = target;
      }
      enterBottomFollow();
    });
  }
  function isChatNearBottom() {
    return chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight <= BOTTOM_ATTACH_THRESHOLD;
  }
  function isChatBottomPinned() {
    // Geometry alone cannot reattach bottom mode: content growth can put a
    // detached reader inside the threshold without any downward gesture.
    // Reattachment happens only in the user-driven scroll handler above or
    // through an explicit scrollToBottom request.
    return !userDetached;
  }
  function detachBottomFollow() {
    if (!userDetached) viewportModeVersion += 1;
    userDetached = true;
    cancelScheduledPlacement();
  }
  function beginScrollTransaction() {
    if (scrollTransaction) {
      scrollTransaction.depth += 1;
      return;
    }
    const wasBottomPinned = isChatBottomPinned();
    // Full session snapshots use the same stable disclosure/Node anchor as
    // incremental Thinking/IN/OUT and executor updates. An absolute scrollTop
    // drifts whenever content above the reader changes height during rebuild.
    const readingPosition = wasBottomPinned ? null : captureScrollPosition();
    if (pendingScrollFrame !== null) cancelAnimationFrame(pendingScrollFrame);
    if (pendingRestoreFrame !== null) cancelAnimationFrame(pendingRestoreFrame);
    pendingScrollFrame = null;
    pendingRestoreFrame = null;
    pendingBottomRequest = null;
    pendingRestoreSnapshot = null;
    scrollTransaction = {
      depth: 1,
      wasBottomPinned,
      readingPosition,
      userIntent: userScrollIntent,
      viewportModeVersion,
      bottomRequested: false,
      preserveUserPosition: true,
    };
  }
  function endScrollTransaction() {
    if (!scrollTransaction) return;
    scrollTransaction.depth -= 1;
    if (scrollTransaction.depth > 0) return;
    const transaction = scrollTransaction;
    scrollTransaction = null;
    if (userScrollIntent !== transaction.userIntent || viewportModeVersion !== transaction.viewportModeVersion) return;
    if (transaction.wasBottomPinned && transaction.bottomRequested) {
      enterBottomFollow();
      scrollToBottom({ preserveUserPosition: transaction.preserveUserPosition });
      return;
    }
    userDetached = true;
    restoreScrollPosition(transaction.readingPosition);
  }
  function captureScrollPosition(preferredAnchor = null, {
    force = false,
    detachBottom = false,
  } = {}) {
    const followBottom = isChatBottomPinned();
    // Callers decide whether a render should follow the bottom by consulting
    // the same reconciled state. A forced disclosure snapshot records bottom
    // mode explicitly instead of turning it into a stale absolute scrollTop.
    if (!force && followBottom) return null;
    // Detaching must be atomic. A resize/stream update may already have
    // queued a bottom placement before the user clicks a disclosure. Leaving
    // that request alive makes it race the reading-position restore, which is
    // especially visible in polling Node/Input/Conversations cards.
    if (detachBottom && !followBottom) detachBottomFollow();
    if (followBottom) return { followBottom: true, userScrollIntent, viewportModeVersion };
    const chatRect = chatArea.getBoundingClientRect();
    let anchorEl = null;
    if (preferredAnchor?.isConnected) {
      anchorEl = preferredAnchor;
    } else {
      // Node cards rebuild their inner DOM as polling data arrives. Prefer a
      // visible, stable disclosure/node key near the viewport edge so the
      // equivalent element can be found after that rebuild. Falling back to
      // the outer message is sufficient for ordinary timeline updates.
      const stableCandidates = [...chatArea.querySelectorAll("[data-reading-anchor], [data-disclosure-key], [data-step-node-id]")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.bottom > chatRect.top && rect.top < chatRect.bottom;
        });
      anchorEl = stableCandidates
        .filter((element) => element.getBoundingClientRect().top >= chatRect.top)
        .sort((left, right) => left.getBoundingClientRect().top - right.getBoundingClientRect().top)[0]
        || stableCandidates
          .sort((left, right) => right.getBoundingClientRect().top - left.getBoundingClientRect().top)[0]
        || [...chatArea.children].find((element) => element.getBoundingClientRect().bottom > chatRect.top);
    }
    return {
      scrollTop: chatArea.scrollTop,
      anchorEl,
      anchorKey: anchorEl?.dataset.readingAnchor || anchorEl?.dataset.disclosureKey || anchorEl?.dataset.stepNodeId || null,
      anchorKeyType: anchorEl?.dataset.readingAnchor ? "reading" : anchorEl?.dataset.disclosureKey ? "disclosure" : anchorEl?.dataset.stepNodeId ? "step" : null,
      anchorOffset: anchorEl ? anchorEl.getBoundingClientRect().top - chatRect.top : 0,
      userScrollIntent,
      viewportModeVersion,
    };
  }
  function resolveSnapshotAnchor(position) {
    if (position.anchorEl?.isConnected) return position.anchorEl;
    if (!position.anchorKey) return null;
    const selectors = {
      reading: ["readingAnchor", "reading-anchor"],
      disclosure: ["disclosureKey", "disclosure-key"],
      step: ["stepNodeId", "step-node-id"],
    };
    const [attribute, selector] = selectors[position.anchorKeyType] || [];
    if (!attribute) return null;
    return [...chatArea.querySelectorAll(`[data-${selector}]`)]
      .find((element) => element.dataset[attribute] === position.anchorKey) || null;
  }
  function restoreScrollPosition(snapshot) {
    if (!snapshot) return;
    if (scrollTransaction) return;
    if (snapshot.followBottom) {
      if (snapshot.userScrollIntent === userScrollIntent && snapshot.viewportModeVersion === viewportModeVersion) {
        scrollToBottom({ preserveUserPosition: true });
      }
      return;
    }
    if (pendingScrollFrame !== null) cancelAnimationFrame(pendingScrollFrame);
    pendingScrollFrame = null;
    pendingBottomRequest = null;
    // All mutations before the next paint form one visual transaction. Keep
    // its earliest snapshot; replacing it with a later image/observer snapshot
    // would preserve an already-drifted intermediate layout.
    if (!pendingRestoreSnapshot
      || pendingRestoreSnapshot.userScrollIntent !== snapshot.userScrollIntent
      || pendingRestoreSnapshot.viewportModeVersion !== snapshot.viewportModeVersion) {
      pendingRestoreSnapshot = snapshot;
    }
    if (pendingRestoreFrame !== null) return;
    pendingRestoreFrame = requestAnimationFrame(() => {
      pendingRestoreFrame = null;
      const position = pendingRestoreSnapshot;
      pendingRestoreSnapshot = null;
      if (!position
        || position.userScrollIntent !== userScrollIntent
        || position.viewportModeVersion !== viewportModeVersion
        || !userDetached) return;
      const anchorEl = resolveSnapshotAnchor(position);
      if (anchorEl) {
        const currentOffset = anchorEl.getBoundingClientRect().top - chatArea.getBoundingClientRect().top;
        chatArea.scrollTop += currentOffset - position.anchorOffset;
      } else {
        chatArea.scrollTop = position.scrollTop;
      }
      lastScrollTop = chatArea.scrollTop;
    });
  }
  function updatePreservingReadingPosition(update) {
    const followBottom = isChatBottomPinned();
    const readingPosition = followBottom ? null : captureScrollPosition();
    const result = update();
    if (followBottom) scrollToBottom({ preserveUserPosition: true });
    else restoreScrollPosition(readingPosition);
    return result;
  }
  function protectAsyncContentLayout(root) {
    root?.querySelectorAll?.("img:not([data-layout-protected])").forEach((img) => {
      img.dataset.layoutProtected = "true";
      if (img.complete) return;
      img.hidden = true;
      const reveal = () => {
        updatePreservingReadingPosition(() => { img.hidden = false; });
      };
      img.addEventListener("load", reveal, { once: true });
      img.addEventListener("error", reveal, { once: true });
    });
  }
  function appendLiveTurnChild(container, child) {
    if (container === chatArea || !container?.dataset?.stepLiveRegion) return container.appendChild(child);
    const firstStepCard = [...container.children].find((element) => element.dataset.stepStartTime !== undefined);
    return firstStepCard ? container.insertBefore(child, firstStepCard) : container.appendChild(child);
  }
  function addMessage(role, content, msgIndex, container = chatArea) {
    const shouldStick = role === "user" || isChatBottomPinned();
    const message = document.createElement("div");
    message.className = `message ${role}-message`;
    if (msgIndex !== undefined) message.dataset.msgIndex = String(msgIndex);
    message.append(role === "agent" ? createAgentAvatarEl() : createUserAvatarEl());
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const inner = document.createElement("div");
    inner.className = "markdown-content";
    inner.innerHTML = renderMarkdown(content || "");
    markReadingAnchors(inner, `message:${msgIndex ?? "live"}`);
    protectAsyncContentLayout(inner);
    bubble.append(inner);
    message.append(bubble);
    appendLiveTurnChild(container, message);
    if (shouldStick) scrollToBottom({ preserveUserPosition: role !== "user" });
    return message;
  }

  return { addMessage, appendLiveTurnChild, applyUserAvatarToEl, beginScrollTransaction, captureScrollPosition, createAgentAvatarEl, createJsonBlock, endScrollTransaction, isChatBottomPinned, markReadingAnchors, protectAsyncContentLayout, renderMarkdown, restoreScrollPosition, scrollToBottom, setUserAvatar, updatePreservingReadingPosition };
}
