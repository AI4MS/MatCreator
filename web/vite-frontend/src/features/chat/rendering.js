import { marked } from "marked";

const BOX_RE = /[┌┐└┘├┤┬┴┼│━─]/;
const CJK_RE = /[一-鿿㐀-䶿豈-﫿　-〿＀-￯]/;
const AGENT_AVATAR_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="rgba(148,163,184,0.9)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="8" width="18" height="11" rx="2"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/><circle cx="9" cy="14" r="1" fill="rgba(148,163,184,0.9)" stroke="none"/><circle cx="15" cy="14" r="1" fill="rgba(148,163,184,0.9)" stroke="none"/><path d="M7 19v2M17 19v2"/>
</svg>`;
const USER_AVATAR_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="rgba(59,130,246,0.9)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
</svg>`;

export function createChatRenderer({ chatArea }) {
  let asciiWidth = 0;
  let cjkWidth = 0;

  function renderMarkdown(text) {
    if (!text) return "";
    let html = marked.parse(text);
    const wrapAsciiArt = (match, inner) => {
      const decoded = inner.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      return BOX_RE.test(decoded) ? `<pre class="ascii-art">${decoded}</pre>` : match;
    };
    html = html.replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/gi, wrapAsciiArt);
    return html.replace(/<p>([\s\S]*?)<\/p>/gi, wrapAsciiArt);
  }

  function unescapeText(text) {
    if (!text) return "";
    return text.replace(/\\\\/g, "\x00").replace(/\\n/g, "\n").replace(/\\t/g, "\t").replace(/\\r/g, "\r").replace(/\\"/g, '"').replace(/\x00/g, "\\");
  }

  function getCharWidths() {
    if (asciiWidth) return { ascii: asciiWidth, cjk: cjkWidth };
    const sample = document.createElement("span");
    sample.style.cssText = "position:absolute;visibility:hidden;font:14px 'Courier New',Consolas,monospace;white-space:pre;";
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
    new ResizeObserver(() => applyWrapMarkers(pre)).observe(pre);
    return pre;
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
  function scrollToBottom() { chatArea.scrollTop = chatArea.scrollHeight; }
  function isChatNearBottom() { return chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < 80; }
  function appendLiveTurnChild(container, child) {
    if (container === chatArea || !container?.dataset?.stepLiveRegion) return container.appendChild(child);
    const firstStepCard = [...container.children].find((element) => element.dataset.stepStartTime !== undefined);
    return firstStepCard ? container.insertBefore(child, firstStepCard) : container.appendChild(child);
  }
  function addMessage(role, content, msgIndex, container = chatArea) {
    const message = document.createElement("div");
    message.className = `message ${role}-message`;
    if (msgIndex !== undefined) message.dataset.msgIndex = String(msgIndex);
    message.append(role === "agent" ? createAgentAvatarEl() : createUserAvatarEl());
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const inner = document.createElement("div");
    inner.className = "markdown-content";
    inner.innerHTML = renderMarkdown(content || "");
    bubble.append(inner);
    message.append(bubble);
    appendLiveTurnChild(container, message);
    scrollToBottom();
    return message;
  }

  return { addMessage, appendLiveTurnChild, applyUserAvatarToEl, createAgentAvatarEl, createJsonBlock, isChatNearBottom, renderMarkdown, scrollToBottom, setUserAvatar };
}
