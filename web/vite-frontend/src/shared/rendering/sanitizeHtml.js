const ALLOWED_ELEMENTS = new Set([
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "div",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "img",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
]);

const DROP_WITH_CONTENT = new Set([
  "base",
  "button",
  "embed",
  "form",
  "iframe",
  "input",
  "link",
  "math",
  "meta",
  "object",
  "option",
  "script",
  "select",
  "style",
  "svg",
  "template",
  "textarea",
]);

const ELEMENT_ATTRIBUTES = {
  a: new Set(["href", "rel", "target", "title"]),
  code: new Set(["class"]),
  div: new Set(["aria-label", "class", "role", "tabindex"]),
  img: new Set(["alt", "loading", "src", "title"]),
  ol: new Set(["start"]),
  pre: new Set(["class"]),
  td: new Set(["align", "colspan", "rowspan"]),
  th: new Set(["align", "colspan", "rowspan", "scope"]),
};
const URL_ATTRIBUTES = new Set(["href", "src"]);
const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"]);
const SAFE_IMAGE_PROTOCOLS = new Set(["blob:", "http:", "https:"]);
const SAFE_CLASSES = {
  code: /^language-[a-z\d_-]+$/i,
  div: /^markdown-table-scroll$/,
  pre: /^ascii-art$/,
};

/**
 * Return whether a URL is safe for a rendered Markdown link or image.
 * Relative and fragment URLs are allowed; executable and inline-data schemes
 * are rejected even when their scheme contains control characters.
 */
export function isSafeRenderedUrl(value, { image = false } = {}) {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed) return false;

  const compact = trimmed.replace(/[\u0000-\u0020\u007f-\u009f]/g, "");
  const scheme = compact.match(/^([a-z][a-z\d+.-]*):/i)?.[1]?.toLowerCase();
  if (!scheme) return true;
  const protocol = `${scheme}:`;
  return (image ? SAFE_IMAGE_PROTOCOLS : SAFE_LINK_PROTOCOLS).has(protocol);
}

function unwrap(element) {
  element.replaceWith(...element.childNodes);
}

function sanitizeAttributes(element) {
  const tagName = element.localName;
  const allowedForElement = ELEMENT_ATTRIBUTES[tagName] || new Set();

  for (const attribute of [...element.attributes]) {
    const name = attribute.name.toLowerCase();
    if (!allowedForElement.has(name)) {
      element.removeAttribute(attribute.name);
      continue;
    }
    if (URL_ATTRIBUTES.has(name) && !isSafeRenderedUrl(attribute.value, { image: tagName === "img" })) {
      element.removeAttribute(attribute.name);
    }
  }

  if (element.hasAttribute("class")) {
    const allowedClass = SAFE_CLASSES[tagName];
    const safeClasses = element.className.split(/\s+/).filter((name) => allowedClass?.test(name));
    if (safeClasses.length) element.className = safeClasses.join(" ");
    else element.removeAttribute("class");
  }

  if (tagName === "div") {
    const isTableWrapper = element.classList.contains("markdown-table-scroll");
    if (!isTableWrapper) {
      element.removeAttribute("aria-label");
      element.removeAttribute("role");
      element.removeAttribute("tabindex");
    } else {
      element.setAttribute("aria-label", "Scrollable table");
      element.setAttribute("role", "region");
      element.setAttribute("tabindex", "0");
    }
  }

  if (tagName === "a") {
    if (element.getAttribute("target") === "_blank") {
      element.setAttribute("rel", "noopener noreferrer");
    } else {
      element.removeAttribute("target");
      element.removeAttribute("rel");
    }
  }
  if (tagName === "img") element.setAttribute("loading", "lazy");
}

/**
 * Sanitize generated Markdown HTML with a deliberately small allowlist.
 *
 * The HTML is parsed in an inert template before it is ever attached to the
 * live document. Unsupported formatting containers are unwrapped so their
 * text remains readable; active/embedded elements are removed completely.
 */
export function sanitizeRenderedHtml(html, documentRef = globalThis.document) {
  if (!html || !documentRef?.createElement) return "";
  const template = documentRef.createElement("template");
  template.innerHTML = String(html);

  for (const element of [...template.content.querySelectorAll("*")]) {
    const tagName = element.localName;
    if (DROP_WITH_CONTENT.has(tagName)) {
      element.remove();
    } else if (!ALLOWED_ELEMENTS.has(tagName)) {
      unwrap(element);
    } else {
      sanitizeAttributes(element);
    }
  }

  return template.innerHTML;
}
