import { marked } from "marked";
import { sanitizeRenderedHtml } from "../../shared/rendering/sanitizeHtml.js";

const BOX_RE = /[┌┐└┘├┤┬┴┼│━─]/;
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
// A transcript can contain generated tables, file listings, or long reports.
// Keeping every element from those responses mounted turns one chat message
// into thousands of live nodes. Defer only genuinely large completed blocks;
// short replies retain the normal, immediately visible chat treatment.
const DEFERRED_MARKDOWN_ELEMENT_LIMIT = 300;
const CHAT_RESIZE_MOTION_MS = 240;

export function createChatRenderer({ chatArea, bottomOverlay = null }) {
  const markdownCache = new Map();
  const markdownCacheMaxWeight = 3_000_000;
  let markdownCacheWeight = 0;
  let pendingScrollFrame = null;
  let bottomReserve = 0;
  let bottomOverlayHeight = 0;
  function syncBottomReserve() {
    const reserve = Math.ceil((bottomOverlayHeight || 98) + 16);
    if (reserve === bottomReserve) return;
    bottomReserve = reserve;
    chatArea.style.setProperty("--chat-bottom-reserve", `${reserve}px`);
  }
  if (bottomOverlay) {
    new ResizeObserver((entries) => {
      const entry = entries.at(-1);
      // ResizeObserver delivers dimensions after layout. Keep that value for
      // every later reserve/scroll update instead of forcing layout with a
      // getBoundingClientRect() read for each streamed DOM mutation.
      const borderBox = Array.isArray(entry?.borderBoxSize)
        ? entry.borderBoxSize[0]
        : entry?.borderBoxSize;
      bottomOverlayHeight = borderBox?.blockSize ?? entry?.contentRect?.height ?? 0;
      syncBottomReserve();
    }).observe(bottomOverlay);
    syncBottomReserve();
  }

  function renderMarkdown(text, { cache = true } = {}) {
    if (!text) return "";
    const cached = cache ? markdownCache.get(text) : null;
    if (cached !== undefined && cached !== null) {
      markdownCache.delete(text);
      markdownCache.set(text, cached);
      return cached.html;
    }
    let html = marked.parse(text);
    const wrapAsciiArt = (match, inner) => {
      const decoded = inner.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      return BOX_RE.test(decoded) ? `<pre class="ascii-art">${escapeHtml(decoded)}</pre>` : match;
    };
    html = html.replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/gi, wrapAsciiArt);
    html = html.replace(/<p>([\s\S]*?)<\/p>/gi, wrapAsciiArt);
    html = html.replace(
      /<table>([\s\S]*?)<\/table>/gi,
      '<div class="markdown-table-scroll" role="region" aria-label="Scrollable table" tabindex="0"><table>$1</table></div>',
    );
    html = sanitizeRenderedHtml(html);
    // Completed transcript content is immutable and commonly revisited when
    // switching sessions. Cache the sanitized string (not DOM nodes/listeners)
    // with a strict weight bound so streaming snapshots cannot leak memory.
    if (cache && text.length <= 50_000) {
      const weight = text.length + html.length;
      markdownCache.set(text, { html, weight });
      markdownCacheWeight += weight;
      while (markdownCacheWeight > markdownCacheMaxWeight && markdownCache.size > 1) {
        const oldestKey = markdownCache.keys().next().value;
        const oldest = markdownCache.get(oldestKey);
        markdownCache.delete(oldestKey);
        markdownCacheWeight -= oldest?.weight || 0;
      }
    }
    return html;
  }

  function markdownElementCount(html) {
    // The generated HTML is sanitized before reaching this point. Counting
    // opening tags is cheap and accurately captures the DOM pressure from
    // lists and tables, unlike a source-character threshold (a long code
    // block is one inexpensive DOM subtree).
    return (String(html).match(/<(?!\/)[a-z][^>]*>/gi) || []).length;
  }

  function setMarkdownContent(element, text, {
    cache = true,
    defer = true,
    anchorPrefix = "",
  } = {}) {
    const source = String(text || "");
    const renderKey = `${source}\u0000${defer ? 1 : 0}\u0000${anchorPrefix}`;
    if (element._markdownRenderKey === renderKey) return false;
    const html = renderMarkdown(source, { cache });
    element.replaceChildren();
    element.classList.remove("is-markdown-deferred");

    const finishMount = (target) => {
      target.innerHTML = html;
      if (anchorPrefix) markReadingAnchors(target, anchorPrefix);
      protectAsyncContentLayout(target);
    };

    if (!defer || markdownElementCount(html) < DEFERRED_MARKDOWN_ELEMENT_LIMIT) {
      finishMount(element);
      element._markdownRenderKey = renderKey;
      return true;
    }

    element.classList.add("is-markdown-deferred");
    const details = document.createElement("details");
    details.className = "markdown-deferred";
    const summary = document.createElement("summary");
    summary.textContent = "Show full response";
    const body = document.createElement("div");
    body.className = "markdown-deferred-body";
    details.append(summary, body);
    let clearTimer = null;
    details.addEventListener("toggle", () => {
      if (details.open) {
        if (clearTimer !== null) window.clearTimeout(clearTimer);
        if (!body.childNodes.length) finishMount(body);
      } else {
        // Keep the subtree around until the collapse transition finishes.
        // The sanitized HTML remains in the bounded Markdown cache, so
        // reopening does not repeat Markdown parsing.
        if (clearTimer !== null) window.clearTimeout(clearTimer);
        clearTimer = window.setTimeout(() => {
          clearTimer = null;
          if (!details.open) body.replaceChildren();
        }, CHAT_RESIZE_MOTION_MS);
      }
    });
    element.appendChild(details);
    element._markdownRenderKey = renderKey;
    return true;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
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
    element.replaceChildren();
    if (url) {
      const image = document.createElement("img");
      image.src = url;
      image.alt = "User";
      element.appendChild(image);
    } else {
      element.innerHTML = USER_AVATAR_SVG;
    }
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
    // VirtualTranscript is the sole owner of transcript scroll position. In
    // particular, do not leave a queued rAF that can override a user wheel
    // gesture after the viewport has transitioned to DETACHED.
    if (chatArea.dataset.transcriptViewport === "virtual") return;
    if (preserveUserPosition && !isChatNearBottom()) return;
    if (pendingScrollFrame !== null) return;
    pendingScrollFrame = requestAnimationFrame(() => {
      pendingScrollFrame = null;
      if (chatArea.dataset.transcriptViewport === "virtual") return;
      chatArea.scrollTop = Math.max(0, chatArea.scrollHeight - chatArea.clientHeight);
    });
  }
  function isChatNearBottom() {
    return chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight <= BOTTOM_ATTACH_THRESHOLD;
  }
  function isChatBottomPinned() {
    return isChatNearBottom();
  }
  function updatePreservingReadingPosition(update) {
    if (chatArea.dataset.transcriptViewport === "virtual") return update();
    const followBottom = isChatBottomPinned();
    const result = update();
    if (followBottom) scrollToBottom({ preserveUserPosition: true });
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
    return container.appendChild(child);
  }
  function addMessage(role, content, msgIndex, container = chatArea, options = {}) {
    const shouldStick = role === "user" || isChatBottomPinned();
    const message = document.createElement("div");
    message.className = `message ${role}-message${msgIndex === undefined ? " is-entering" : ""}`;
    if (msgIndex !== undefined) message.dataset.msgIndex = String(msgIndex);
    if (options.messageKey) message.dataset.readingAnchor = `message:${options.messageKey}`;
    message.append(role === "agent" ? createAgentAvatarEl() : createUserAvatarEl());
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const inner = document.createElement("div");
    inner.className = "markdown-content";
    setMarkdownContent(inner, content || "", {
      // A user's just-sent prompt should remain visible in full. Assistant
      // reports can be deferred once they cross the DOM safety threshold.
      defer: role === "agent",
      anchorPrefix: options.messageKey || `message:${msgIndex ?? "live"}`,
    });
    bubble.append(inner);
    message.append(bubble);
    appendLiveTurnChild(container, message);
    if (shouldStick) scrollToBottom({ preserveUserPosition: role !== "user" });
    return message;
  }

  return { addMessage, appendLiveTurnChild, applyUserAvatarToEl, createAgentAvatarEl, isChatBottomPinned, markReadingAnchors, protectAsyncContentLayout, renderMarkdown, scrollToBottom, setMarkdownContent, setUserAvatar, updatePreservingReadingPosition };
}
