// Inline code inside rendered markdown often cites a workspace file path.
// One click on such a code span copies it (with a brief confirmation chip)
// and, when the citation matches a file in the session explorer, reveals the
// row there. Code spans that cite neither stay plain selectable text, so the
// copy affordance is applied lazily on hover instead of at render time.
const CHIP_LIFETIME_MS = 1100;
const MAX_COPY_LENGTH = 4096;

let chipElement = null;
let chipTimer = null;

function ensureChip() {
  if (chipElement) return chipElement;
  chipElement = document.createElement("div");
  chipElement.className = "code-copy-chip";
  chipElement.setAttribute("role", "status");
  chipElement.textContent = "Copied ✓";
  document.body.appendChild(chipElement);
  return chipElement;
}

function showChip(anchor) {
  const chip = ensureChip();
  const rect = anchor.getBoundingClientRect();
  chip.style.left = `${Math.round(rect.left + rect.width / 2)}px`;
  chip.style.top = `${Math.round(rect.top - 6)}px`;
  chip.classList.remove("is-visible");
  void chip.offsetWidth; // restart the animation when spam-clicking
  chip.classList.add("is-visible");
  if (chipTimer !== null) window.clearTimeout(chipTimer);
  chipTimer = window.setTimeout(() => {
    chipTimer = null;
    chip.classList.remove("is-visible");
  }, CHIP_LIFETIME_MS);
}

function copyText(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).catch(() => copyTextViaExecCommand(text));
  }
  return Promise.resolve(copyTextViaExecCommand(text));
}

function copyTextViaExecCommand(text) {
  // Clipboard API requires a secure context; local HTTP deployments still
  // deserve a working copy, so fall back to a temporary selection.
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } catch (_) {
    // Nothing else to try; the chip would then report a copy that did not
    // happen, so leave silently rather than mislead.
  }
  textarea.remove();
}

function citedCode(event) {
  if (event.defaultPrevented || event.button !== 0) return null;
  const target = event.target instanceof Element ? event.target : null;
  const code = target?.closest("code");
  if (!code || !code.closest(".markdown-content")) return null;
  // Fenced blocks and linked code have their own selection/link semantics.
  if (code.closest("pre, a, button")) return null;
  const text = (code.textContent || "").trim();
  if (!text || text.length > MAX_COPY_LENGTH) return null;
  return { code, text };
}

function isDragSelectionEnd(event, code) {
  const selection = window.getSelection();
  return Boolean(selection && !selection.isCollapsed && selection.containsNode(code, true));
}

function markCopyable(code, text, isCopyable) {
  // Only positive verdicts are cached: a span cited a file before that file
  // existed in the explorer, and should upgrade once the tree lists it.
  if (code.dataset.copyable === "true") return true;
  if (!isCopyable(text)) return false;
  code.dataset.copyable = "true";
  if (!code.title) code.title = "Click to copy";
  return true;
}

export function attachInlineCodeCopy({ isCopyable, onCopyableClick }) {
  // Hover marks only the spans the policy accepts, so a non-path citation
  // such as `ISIF=2` keeps its normal text cursor and selection behavior.
  document.addEventListener("pointerover", (event) => {
    const citation = citedCode(event);
    if (!citation) return;
    markCopyable(citation.code, citation.text, isCopyable);
  });
  document.addEventListener("click", (event) => {
    const citation = citedCode(event);
    if (!citation) return;
    const { code, text } = citation;
    if (!markCopyable(code, text, isCopyable)) return;
    if (isDragSelectionEnd(event, code)) return;
    void copyText(text);
    onCopyableClick?.(text);
    showChip(code);
  });
}
