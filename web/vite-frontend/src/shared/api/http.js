const JSON_MEDIA_TYPE = /\b(?:application\/json|[^;\s]+\/[^;\s]+\+json)\b/i;

function queryValue(value) {
  if (value instanceof Date) return value.toISOString();
  if (["string", "number", "boolean", "bigint"].includes(typeof value)) {
    return String(value);
  }
  throw new TypeError(`Unsupported query parameter value: ${Object.prototype.toString.call(value)}`);
}

function appendQueryValue(params, key, value) {
  if (value === undefined || value === null) return;
  if (Array.isArray(value)) {
    value.forEach((item) => appendQueryValue(params, key, item));
    return;
  }
  params.append(key, queryValue(value));
}

/** Add encoded query parameters without losing an existing query or fragment. */
export function buildUrl(url, query) {
  if (!query) return String(url);

  const source = String(url);
  const hashIndex = source.indexOf("#");
  const fragment = hashIndex >= 0 ? source.slice(hashIndex) : "";
  const withoutFragment = hashIndex >= 0 ? source.slice(0, hashIndex) : source;
  const queryIndex = withoutFragment.indexOf("?");
  const pathname = queryIndex >= 0 ? withoutFragment.slice(0, queryIndex) : withoutFragment;
  const params = new URLSearchParams(queryIndex >= 0 ? withoutFragment.slice(queryIndex + 1) : "");

  if (query instanceof URLSearchParams) {
    query.forEach((value, key) => params.append(key, value));
  } else {
    Object.entries(query).forEach(([key, value]) => appendQueryValue(params, key, value));
  }

  const search = params.toString();
  return `${pathname}${search ? `?${search}` : ""}${fragment}`;
}

function formatLocation(location) {
  if (!Array.isArray(location)) return String(location || "");
  return location.reduce((result, part) => (
    typeof part === "number"
      ? `${result}[${part}]`
      : `${result}${result ? "." : ""}${String(part)}`
  ), "");
}

function formatValidationIssue(issue) {
  if (!issue || typeof issue !== "object") return String(issue);
  const location = formatLocation(issue.loc);
  const message = issue.msg || issue.message || issue.type || JSON.stringify(issue);
  return location ? `${location}: ${message}` : String(message);
}

function responseDetail(body) {
  if (body === undefined || body === null || body === "") return "";
  if (typeof body === "string") return body;

  const detail = body.detail ?? body.message ?? body.error;
  if (Array.isArray(detail)) return detail.map(formatValidationIssue).join("; ");
  if (detail && typeof detail === "object") return formatValidationIssue(detail);
  if (detail !== undefined && detail !== null) return String(detail);

  try {
    return JSON.stringify(body);
  } catch (_) {
    return String(body);
  }
}

function statusLabel(status, statusText) {
  return [status || "", statusText || ""].filter(Boolean).join(" ");
}

export class HttpError extends Error {
  constructor(message, {
    status = 0,
    statusText = "",
    method = "GET",
    url = "",
    body = null,
    code = "HTTP_ERROR",
    cause,
  } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "HttpError";
    this.status = status;
    this.statusText = statusText;
    this.method = method;
    this.url = url;
    this.body = body;
    this.code = code;
  }
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text.trim()) return { value: null, text: "" };

  const contentType = response.headers?.get?.("content-type") || "";
  if (JSON_MEDIA_TYPE.test(contentType) || /^[\[{]/.test(text.trim())) {
    try {
      return { value: JSON.parse(text), text };
    } catch (_) {
      return { value: text, text };
    }
  }
  return { value: text, text };
}

/** Create a JSON client. Pass fetchImpl to make callers deterministic in tests. */
export function createHttpClient({ fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") {
    throw new TypeError("createHttpClient requires a fetch implementation");
  }

  async function requestJson(url, options = {}) {
    const {
      query,
      json,
      headers: initialHeaders,
      ...initialRequest
    } = options;
    const requestUrl = buildUrl(url, query);
    const headers = new Headers(initialHeaders);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");

    const request = { ...initialRequest, headers };
    if (json !== undefined) {
      if (request.body !== undefined) {
        throw new TypeError("Use either json or body, not both");
      }
      if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
      request.body = JSON.stringify(json);
      request.method ||= "POST";
    }
    const method = String(request.method || "GET").toUpperCase();

    let response;
    try {
      response = await fetchImpl(requestUrl, request);
    } catch (cause) {
      const detail = cause?.message ? `: ${cause.message}` : "";
      throw new HttpError(`${method} ${requestUrl} failed${detail}`, {
        method,
        url: requestUrl,
        code: "NETWORK_ERROR",
        cause,
      });
    }

    let parsed;
    try {
      parsed = await readResponseBody(response);
    } catch (cause) {
      throw new HttpError(`${method} ${requestUrl} response could not be read`, {
        status: response.status,
        statusText: response.statusText,
        method,
        url: requestUrl,
        code: "RESPONSE_READ_ERROR",
        cause,
      });
    }

    if (!response.ok) {
      const status = statusLabel(response.status, response.statusText);
      const detail = responseDetail(parsed.value);
      throw new HttpError(`${method} ${requestUrl} failed${status ? ` (${status})` : ""}${detail ? `: ${detail}` : ""}`, {
        status: response.status,
        statusText: response.statusText,
        method,
        url: requestUrl,
        body: parsed.value,
      });
    }

    if (method === "HEAD" || response.status === 204 || response.status === 205 || !parsed.text) {
      return null;
    }

    try {
      return JSON.parse(parsed.text);
    } catch (cause) {
      const status = statusLabel(response.status, response.statusText);
      throw new HttpError(`${method} ${requestUrl} returned invalid JSON${status ? ` (${status})` : ""}`, {
        status: response.status,
        statusText: response.statusText,
        method,
        url: requestUrl,
        body: parsed.text,
        code: "INVALID_JSON",
        cause,
      });
    }
  }

  return Object.freeze({
    requestJson,
    getJson: (url, options = {}) => requestJson(url, { ...options, method: "GET" }),
  });
}

export const httpClient = createHttpClient();
