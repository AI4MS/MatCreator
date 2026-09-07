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
  "meta",
  "object",
  "option",
  "script",
  "select",
  "style",
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
  span: new Set(["aria-hidden", "class", "style"]),
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
const KATEX_CLASSES = new Set([
  "accent", "accent-body", "accent-full", "amsrm", "angl", "anglpad", "arraycolsep", "base", "boldsymbol", "boxpad", "brace-center", "brace-left", "brace-right", "cancel-lap", "cancel-pad", "cd-arrow-pad", "cd-label-left", "cd-label-right", "cd-vert-arrow-pad", "clap", "col-align-c", "col-align-l", "col-align-r", "delim-size1", "delim-size4", "delimcenter", "delimsizing", "eqn-num", "fbox", "fcolorbox", "fix", "fleqn", "fontsize-ensurer", "frac-line", "halfarrow-left", "halfarrow-right", "hbox", "hdashline", "hide-tail", "hline", "inner", "katex", "katex-display", "katex-html", "large-op", "leqno", "llap", "mainrm", "mathbb", "mathbf", "mathboldfrak", "mathboldsf", "mathcal", "mathfrak", "mathit", "mathitsf", "mathnormal", "mathrm", "mathscr", "mathsf", "mathsfit", "mathtt", "mfrac", "mop", "mord", "mopen", "mclose", "mrel", "mbin", "mspace", "msupsub", "mtable", "mtr-glue", "mult", "munder", "newline", "nulldelimiter", "op-limits", "op-symbol", "overlay", "overline", "overline-line", "pstrut", "reset-size1", "reset-size10", "reset-size11", "reset-size2", "reset-size3", "reset-size4", "reset-size5", "reset-size6", "reset-size7", "reset-size8", "reset-size9", "rlap", "root", "rule", "size1", "size10", "size11", "size2", "size3", "size4", "size5", "size6", "size7", "size8", "size9", "sizing", "small-op", "smash", "sout", "sqrt", "stretchy", "strut", "svg-align", "tag", "textbb", "textbf", "textboldfrak", "textboldsf", "textfrak", "textit", "textitsf", "textrm", "textscr", "textsf", "texttt", "thinbox", "ttf", "underline", "underline-line", "vbox", "vertical-separator", "vlist", "vlist-r", "vlist-s", "vlist-t", "vlist-t2", "x-arrow", "x-arrow-pad",
]);
const KATEX_STYLE_PROPERTIES = new Set([
  "border-bottom-width",
  "height",
  "left",
  "margin-left",
  "margin-right",
  "min-width",
  "padding-left",
  "position",
  "top",
  "vertical-align",
  "width",
]);
const KATEX_LENGTH = /^(?:0|[+-]?(?:\d+\.?\d*|\.\d+)(?:em|ex|px|pt|rem|%))$/;

function isSafeKatexStyle(value) {
  if (typeof value !== "string" || !value.trim()) return false;
  return value.split(";").every((declaration) => {
    if (!declaration.trim()) return true;
    const separator = declaration.indexOf(":");
    if (separator < 1) return false;
    const property = declaration.slice(0, separator).trim().toLowerCase();
    const cssValue = declaration.slice(separator + 1).trim().toLowerCase();
    if (!KATEX_STYLE_PROPERTIES.has(property)) return false;
    return property === "position"
      ? cssValue === "relative"
      : KATEX_LENGTH.test(cssValue);
  });
}

function isKatexSvgElement(element) {
  if (element.localName === "svg") {
    return element.parentElement?.classList.contains("hide-tail");
  }
  return element.localName === "path" && element.parentElement?.localName === "svg"
    && element.parentElement.parentElement?.classList.contains("hide-tail");
}

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

function flattenRepeatedInlineFormatting(root) {
  // Markdown produced from long runs of `*`, `_`, or `~` can alternate the
  // same inline tags (for example em > strong > em > strong) many times.
  // Once a tag already exists in its ancestor chain, another instance adds no
  // visual formatting but does add DOM depth and style work. Unwrapping the
  // repeat preserves the rendered emphasis/strike-through while bounding the
  // nesting depth of generated Markdown.
  for (const element of [...root.querySelectorAll("em, strong, del")]) {
    let ancestor = element.parentElement;
    while (ancestor) {
      if (ancestor.localName === element.localName) {
        unwrap(element);
        break;
      }
      ancestor = ancestor.parentElement;
    }
  }
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
    const safeClasses = element.className.split(/\s+/).filter((name) => (
      allowedClass?.test(name) || (tagName === "span" && KATEX_CLASSES.has(name))
    ));
    if (safeClasses.length) element.className = safeClasses.join(" ");
    else element.removeAttribute("class");
  }

  if (tagName === "span" && element.hasAttribute("style") && !isSafeKatexStyle(element.getAttribute("style"))) {
    element.removeAttribute("style");
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

function sanitizeKatexSvg(element) {
  const allowedAttributes = element.localName === "svg"
    ? new Set(["height", "preserveaspectratio", "viewbox", "width", "xmlns"])
    : new Set(["d"]);
  for (const attribute of [...element.attributes]) {
    if (!allowedAttributes.has(attribute.name.toLowerCase())) element.removeAttribute(attribute.name);
  }
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
    } else if (isKatexSvgElement(element)) {
      sanitizeKatexSvg(element);
    } else if (tagName === "svg") {
      element.remove();
    } else if (!ALLOWED_ELEMENTS.has(tagName)) {
      unwrap(element);
    } else {
      sanitizeAttributes(element);
    }
  }

  flattenRepeatedInlineFormatting(template.content);

  return template.innerHTML;
}

export { isSafeKatexStyle };
