import assert from "node:assert/strict";
import test from "node:test";
import { createLocalAccessFetch, localAccountUrl, bindNativeAccountElements, bindNativeAccountUrls } from "../src/features/auth/localAccessFetch.js";
import { parseHTML } from "linkedom";

test("sanitized Markdown links and images are account-bound before entering the live DOM", () => {
  const { document } = parseHTML("<html><body></body></html>");
  const bind = url => localAccountUrl(url, "account-a", "http://mc-instance.localhost:15173/");
  const output = document.createElement("template");
  output.innerHTML = '<a href="/api/workspace/files?path=result.cif">Download</a><img src="/api/workspace/files?path=result.png"><img src="https://example.org/image.png">';
  // Linkedom's template.innerHTML does not reflect content mutations. Inspect
  // the inert fragment itself; browsers serialize this same content via HTML.
  const fragment = output.content;
  bindNativeAccountElements(fragment, bind);
  for (const element of [fragment.querySelector("a"), fragment.querySelector("img")]) {
    const url = element.getAttribute(element.tagName === "A" ? "href" : "src");
    assert.equal(new URL(url, "http://localhost/").searchParams.get("expected_account_id"), "account-a");
  }
  assert.equal(fragment.querySelectorAll("img")[1].getAttribute("src"), "https://example.org/image.png");
  assert.equal(bindNativeAccountUrls("<p>Plain text</p>", bind, document), "<p>Plain text</p>");
});

test("native account URLs preserve their original account across cookie switches", () => {
  const base = "http://mc-instance.localhost:15173/";
  const oldUrl = localAccountUrl("/api/xrd/workspaces/demo/archive?name=a.zip#download", "account-a", base);
  const parsed = new URL(oldUrl, base);
  assert.equal(parsed.searchParams.get("expected_account_id"), "account-a");
  assert.equal(parsed.searchParams.get("name"), "a.zip");
  assert.equal(parsed.hash, "#download");
  assert.equal(localAccountUrl(oldUrl, "account-b", base), oldUrl);
  assert.equal(new URL(localAccountUrl(base + "api/workspace/files?path=a.png", "account-a", base)).searchParams.get("expected_account_id"), "account-a");
});

test("native account URL binding leaves external, local-data and public URLs unchanged", () => {
  const base = "http://mc-instance.localhost:15173/";
  for (const url of ["https://external.example/api/image", "data:image/png;base64,AAAA", "blob:http://mc-instance.localhost:15173/image", "/assets/icon.svg", "/api/auth/status", "/api/health"]) {
    assert.equal(localAccountUrl(url, "account-a", base), url);
  }
  assert.equal(localAccountUrl("/api/workspace/files", "", base), "/api/workspace/files");
});

function guard({ detail = "Local instance access token required", authenticated = true } = {}) {
  let locked = 0;
  const fetch = createLocalAccessFetch(async () => new Response(JSON.stringify({ detail }), { status: 401 }), {
    baseUrl: "http://mc-instance.localhost:15173/",
    isAuthenticatedLocal: () => authenticated,
    onAccessRequired: () => { locked++; authenticated = false; },
  });
  return { fetch, locks: () => locked };
}

test("a local gate 401 clears the frontend login without consuming its response", async () => {
  const app = guard();
  const response = await app.fetch("/api/users/user/sessions");
  assert.equal(app.locks(), 1);
  assert.deepEqual(await response.json(), { detail: "Local instance access token required" });
  await app.fetch("/api/knowledge-review/status");
  assert.equal(app.locks(), 1);
});

test("provider errors and token validation failures do not trigger the expired-session guard", async () => {
  const provider = guard({ detail: "Invalid provider API key" });
  await provider.fetch("/api/runs");
  assert.equal(provider.locks(), 0);
  const login = guard();
  await login.fetch("/api/local-auth/login");
  assert.equal(login.locks(), 0);
});

test("external responses and server-mode responses cannot lock a local instance", async () => {
  const app = guard();
  await app.fetch("https://other.example/api/data");
  assert.equal(app.locks(), 0);
  const server = guard({ authenticated: false });
  await server.fetch("/api/users/user/sessions");
  assert.equal(server.locks(), 0);
});

test("local account requests carry an expected account and reject a changed cookie identity", async () => {
  const calls = [];
  let locks = 0;
  const fetch = createLocalAccessFetch(async (input, options) => {
    calls.push({ input, options });
    return new Response(JSON.stringify({ detail: "Local account login required" }), { status: 401 });
  }, {
    baseUrl: "http://mc-instance.localhost:15173/",
    isAuthenticatedLocal: () => true,
    getLocalAccountId: () => "account-a",
    onAccessRequired: () => { locks++; },
  });
  await fetch("/api/env-config", { headers: { Accept: "application/json" } });
  assert.equal(calls[0].options.headers.get("X-MC-Account-ID"), "account-a");
  assert.equal(calls[0].options.headers.get("Accept"), "application/json");
  assert.equal(locks, 1);
  await fetch("/api/auth/login", { method: "POST", body: "{}" });
  assert.equal(calls[1].options.headers, undefined);
  assert.equal(locks, 1);
  await fetch("https://external.example/api/upload", { method: "POST" });
  assert.equal(calls[2].options.headers, undefined);
  assert.equal(locks, 1);
});

test("logout and password changes are bound to the account displayed in this tab", async () => {
  const calls = [];
  const fetch = createLocalAccessFetch(async (_input, options) => {
    calls.push(options.headers.get("X-MC-Account-ID"));
    return new Response("{}", { status: 200 });
  }, {
    baseUrl: "http://mc-instance.localhost:15173/", isAuthenticatedLocal: () => true,
    getLocalAccountId: () => "account-a", onAccessRequired() {},
  });
  await fetch("/api/auth/logout", { method: "POST" });
  await fetch("/api/auth/set-password", { method: "POST" });
  assert.deepEqual(calls, ["account-a", "account-a"]);
});

test("an in-flight response from a previous account cannot populate the new account", async () => {
  let account = "account-a";
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const fetch = createLocalAccessFetch(() => pending, {
    baseUrl: "http://mc-instance.localhost:15173/", isAuthenticatedLocal: () => true,
    getLocalAccountId: () => account, onAccessRequired() {},
  });
  const response = fetch("/api/env-config");
  account = "account-b";
  release(new Response(JSON.stringify({ private: "account-a-config" }), { status: 200 }));
  await assert.rejects(response, { name: "AbortError" });
});

test("a slow body cannot return old account data after response headers already arrived", async () => {
  let account = "account-a";
  let body;
  const stream = new ReadableStream({ start(controller) { body = controller; } });
  const fetch = createLocalAccessFetch(async () => new Response(stream), {
    baseUrl: "http://mc-instance.localhost:15173/", isAuthenticatedLocal: () => true,
    getLocalAccountId: () => account, onAccessRequired() {},
  });
  const response = await fetch("/api/env-config");
  const parsed = response.json();
  account = "account-b";
  body.enqueue(new TextEncoder().encode('{"private":"account-a-config"}'));
  body.close();
  await assert.rejects(parsed, { name: "AbortError" });
});
