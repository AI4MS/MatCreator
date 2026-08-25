import assert from "node:assert/strict";
import test from "node:test";

import {
  buildUrl,
  createHttpClient,
  HttpError,
} from "../src/shared/api/http.js";

test("buildUrl safely appends scalar and repeated query values", () => {
  assert.equal(
    buildUrl("/api/items?existing=yes#result", {
      user_id: "a user&admin=true",
      page: 0,
      active: false,
      tag: ["one", "two words"],
      omitted: null,
    }),
    "/api/items?existing=yes&user_id=a+user%26admin%3Dtrue&page=0&active=false&tag=one&tag=two+words#result",
  );
});

test("buildUrl rejects ambiguous object query values", () => {
  assert.throws(
    () => buildUrl("/api/items", { filter: { owner: "me" } }),
    /Unsupported query parameter value/,
  );
});

test("getJson uses the injected fetch and parses JSON", async () => {
  let request;
  const client = createHttpClient({
    fetchImpl: async (url, options) => {
      request = { url, options };
      return new Response(JSON.stringify({ items: [1, 2] }), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  assert.deepEqual(await client.getJson("/api/items", { query: { owner: "Ada Lovelace" } }), { items: [1, 2] });
  assert.equal(request.url, "/api/items?owner=Ada+Lovelace");
  assert.equal(request.options.method, "GET");
  assert.equal(request.options.headers.get("Accept"), "application/json");
});

test("requestJson serializes JSON bodies with predictable headers", async () => {
  let request;
  const client = createHttpClient({
    fetchImpl: async (url, options) => {
      request = { url, options };
      return new Response(JSON.stringify({ created: true }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  assert.deepEqual(await client.requestJson("/api/items", { json: { name: "sample" } }), { created: true });
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers.get("Content-Type"), "application/json");
  assert.equal(request.options.body, '{"name":"sample"}');
});

test("requestJson returns null for empty successful responses", async () => {
  const noContent = createHttpClient({
    fetchImpl: async () => new Response(null, { status: 204 }),
  });
  const emptyBody = createHttpClient({
    fetchImpl: async () => new Response("", { status: 200 }),
  });

  assert.equal(await noContent.getJson("/api/empty"), null);
  assert.equal(await emptyBody.getJson("/api/empty"), null);
});

test("requestJson accepts every valid JSON top-level value", async () => {
  const client = createHttpClient({
    fetchImpl: async () => new Response('"ready"', {
      headers: { "Content-Type": "application/json" },
    }),
  });

  assert.equal(await client.getJson("/api/status"), "ready");
});

test("HttpError includes normalized FastAPI validation details", async () => {
  const client = createHttpClient({
    fetchImpl: async () => new Response(JSON.stringify({
      detail: [
        { loc: ["body", "items", 0, "name"], msg: "Field required", type: "missing" },
        { loc: ["query", "limit"], msg: "Input should be greater than 0", type: "greater_than" },
      ],
    }), {
      status: 422,
      statusText: "Unprocessable Entity",
      headers: { "Content-Type": "application/json" },
    }),
  });

  await assert.rejects(
    client.getJson("/api/items"),
    (error) => {
      assert.ok(error instanceof HttpError);
      assert.equal(error.status, 422);
      assert.equal(error.method, "GET");
      assert.equal(error.url, "/api/items");
      assert.match(error.message, /422 Unprocessable Entity/);
      assert.match(error.message, /body\.items\[0\]\.name: Field required/);
      assert.match(error.message, /query\.limit: Input should be greater than 0/);
      return true;
    },
  );
});

test("HttpError preserves plain-text error responses", async () => {
  const client = createHttpClient({
    fetchImpl: async () => new Response("backend unavailable", {
      status: 503,
      statusText: "Service Unavailable",
    }),
  });

  await assert.rejects(
    client.getJson("/api/status"),
    (error) => error instanceof HttpError
      && error.status === 503
      && error.body === "backend unavailable"
      && error.message.includes("backend unavailable"),
  );
});

test("invalid success bodies and network failures become HttpError instances", async () => {
  const invalidJson = createHttpClient({
    fetchImpl: async () => new Response("not-json", {
      headers: { "Content-Type": "application/json" },
    }),
  });
  const networkFailure = createHttpClient({
    fetchImpl: async () => { throw new Error("offline"); },
  });

  await assert.rejects(
    invalidJson.getJson("/api/data"),
    (error) => error instanceof HttpError && error.code === "INVALID_JSON",
  );
  await assert.rejects(
    networkFailure.getJson("/api/data"),
    (error) => error instanceof HttpError
      && error.code === "NETWORK_ERROR"
      && error.message.includes("offline"),
  );
});
