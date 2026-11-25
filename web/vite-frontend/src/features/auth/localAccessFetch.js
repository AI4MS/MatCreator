function protectedLocalPath(pathname) {
  return (pathname.startsWith("/api/") || pathname.startsWith("/apps/")
    || ["/run_sse", "/list-apps"].includes(pathname))
    && !pathname.startsWith("/api/local-auth/")
    && !["/api/health", "/api/auth/login", "/api/auth/register", "/api/auth/status", "/api/auth/me"].includes(pathname);
}

// Native links and images bypass fetch. Bind them at creation so an old tab's
// URL cannot silently select the account from a newly replaced browser cookie.
export function localAccountUrl(input, accountId, baseUrl = globalThis.location?.href) {
  if (!accountId || !input || !baseUrl) return input;
  try {
    const target = new URL(input, baseUrl);
    if (target.origin !== new URL(baseUrl).origin || !protectedLocalPath(target.pathname)) return input;
    // A previously bound URL retains its original account even if reused later.
    if (!target.searchParams.get("expected_account_id")) target.searchParams.set("expected_account_id", accountId);
    return String(input).startsWith("/") && !String(input).startsWith("//")
      ? target.pathname + target.search + target.hash : target.href;
  } catch (_) { return input; }
}

// Parse already-sanitized markup in an inert template. Bind URLs before its
// images enter the live DOM and start native requests outside the fetch guard.
export function bindNativeAccountElements(root, bindAccountUrl) {
  for (const element of root.querySelectorAll("[href], [src]")) {
    for (const attribute of ["href", "src"]) {
      if (element.hasAttribute(attribute)) element.setAttribute(attribute, bindAccountUrl(element.getAttribute(attribute)));
    }
  }
}

export function bindNativeAccountUrls(html, bindAccountUrl, documentRef = globalThis.document) {
  if (!bindAccountUrl || !/\b(?:href|src)=/i.test(html)) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  bindNativeAccountElements(template.content, bindAccountUrl);
  return template.innerHTML;
}

/** Keep an expired local login from leaving a stale, apparently live UI. */
export function createLocalAccessFetch(fetchImpl, {
  baseUrl,
  isAuthenticatedLocal,
  onAccessRequired,
  getLocalAccountId = () => "",
}) {
  return async function localAccessFetch(input, options) {
    let target;
    try { target = new URL(typeof input === "string" || input instanceof URL ? input : input.url, baseUrl); }
    catch (_) { return fetchImpl(input, options); }
    const localProtected = target.origin === new URL(baseUrl).origin && protectedLocalPath(target.pathname);
    const accountId = localProtected && isAuthenticatedLocal() ? getLocalAccountId() : "";
    let requestOptions = options;
    if (accountId) {
      const headers = new Headers(options?.headers ?? (typeof input === "object" ? input.headers : undefined));
      headers.set("X-MC-Account-ID", accountId);
      requestOptions = { ...options, headers };
    }
    const response = await fetchImpl(input, requestOptions);
    function assertCurrentAccount() {
      if (accountId && (!isAuthenticatedLocal() || getLocalAccountId() !== accountId)) {
        throw new DOMException("The local account changed while the request was running", "AbortError");
      }
    }
    // A response started under A must not repopulate B's UI after a switch,
    // including when response headers arrived before a slow JSON/file body.
    assertCurrentAccount();
    if (accountId) {
      for (const method of ["json", "text", "blob", "arrayBuffer"]) {
        const read = response[method]?.bind(response);
        if (!read) continue;
        response[method] = async (...args) => {
          const value = await read(...args);
          assertCurrentAccount();
          return value;
        };
      }
    }
    if (response.status !== 401 || !isAuthenticatedLocal() || !localProtected) return response;
    // A model or external provider can also return 401. Only the local gate's
    // exact response should lock the instance; preserve the original response.
    try {
      const body = await response.clone().json();
      if (["Local instance access token required", "Local account login required"].includes(body.detail)
          && isAuthenticatedLocal()) onAccessRequired();
    } catch (_) { /* Other 401 response formats remain ordinary API errors. */ }
    return response;
  };
}
