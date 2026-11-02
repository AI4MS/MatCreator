import assert from "node:assert/strict";
import test from "node:test";
import {
  createAuthController,
  localInstanceStorageKeys,
  takeLocalTokenFragment,
} from "../src/features/auth/AuthController.js";

function element() {
  const classes = new Set(["hidden"]);
  const listeners = new Map();
  return {
    classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name), contains: (name) => classes.has(name) },
    style: {}, value: "", textContent: "", title: "", parentElement: { style: {} },
    replaceChildren() {}, appendChild() {}, focus() {}, setAttribute() {},
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name) => listeners.delete(name),
  };
}

function harness({ authenticated = false, instanceId = "instance-a", stored = new Map(), bootstrapToken = "", mode = "local", browserHost, locationHref = "http://127.0.0.1:15173/" } = {}) {
  const state = { userId: "", deploymentMode: mode, sessionId: "fresh", sessionReady: false, sessionViewCache: new Map() };
  const elements = Object.fromEntries([
    "loginModal", "loginView", "registerView", "localTokenView", "localTokenInput", "localTokenError",
    "localTokenSubmit", "localInstanceLabel", "userDisplay", "sessionListElement",
  ].map((name) => [name, element()]));
  const requests = [];
  const loads = [];
  const switches = [];
  const redirects = [];
  const sessionStorageKeys = { sessionId: "mat_sessionId", owner: "mat_sessionOwnerId" };
  let unlocked = authenticated;
  let serial = 0;
  const reply = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url, ...options });
    if (url === "/api/health") return reply({ status: "ok", mode, instance_id: instanceId, browser_host: browserHost, authentication_required: true, authenticated: unlocked });
    if (url === "/api/local-auth/login") {
      unlocked = JSON.parse(options.body).token === "correct-secret";
      return reply({ authenticated: unlocked }, unlocked ? 200 : 401);
    }
    if (url === "/api/local-auth/logout") { unlocked = false; return reply({ status: "ok" }); }
    if (url.startsWith("/api/session-access/")) return reply({ is_admin: false });
    throw new Error(`Unexpected request ${url}`);
  };
  const controller = createAuthController({
    state, elements, sessionStorageKeys, bootstrapToken,
    createSessionId: () => `new-${++serial}`,
    clearSessionSelection() { stored.delete(sessionStorageKeys.sessionId); stored.delete(sessionStorageKeys.owner); },
    updateSendButtonState() {},
    async loadSessions() {
      loads.push(instanceId);
      return [{ id: `session-${instanceId}`, userId: mode === "local" ? "user" : state.userId }];
    },
    async switchSession(...args) { switches.push(args); },
    onLoggedOut() {}, onSessionApplied() {}, fetchImpl, documentRef: { createElement: element },
    locationRef: { href: locationHref, replace: (url) => redirects.push(url) },
    storage: { getItem: (key) => stored.get(key) || null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) },
  });
  return { controller, state, elements, requests, loads, switches, stored, sessionStorageKeys, redirects };
}

test("locked local startup requests health only and never loads a transcript", async () => {
  const app = harness({ stored: new Map([["mat_userId", "user"], ["mat_sessionId", "someone-else"]]) });
  await app.controller.start();
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health"]);
  assert.deepEqual(app.loads, []);
  assert.equal(app.state.userId, "");
  assert.equal(app.controller.canWriteActiveSession(), false);
  assert.equal(app.elements.localTokenView.classList.contains("hidden"), false);
  assert.equal(app.elements.loginView.classList.contains("hidden"), true);
});

test("valid instance token must establish a cookie-backed session before loading data", async () => {
  const app = harness();
  await app.controller.start();
  assert.equal(await app.controller.loginLocal("correct-secret"), true);
  assert.deepEqual(app.requests.map((request) => request.url), [
    "/api/health", "/api/local-auth/login", "/api/health", "/api/session-access/user",
  ]);
  assert.equal(app.state.localAuthenticated, true);
  assert.equal(app.state.userId, "user");
  assert.equal(app.controller.canWriteActiveSession(), true);
  assert.deepEqual(app.loads, ["instance-a"]);
  assert.equal(app.elements.loginModal.classList.contains("hidden"), true);
  assert.ok(!JSON.stringify([...app.stored]).includes("correct-secret"));
  assert.equal(app.elements.localTokenInput.value, "");
});

test("wrong token cannot trigger session or access requests", async () => {
  const app = harness();
  await app.controller.start();
  assert.equal(await app.controller.loginLocal("wrong-secret"), false);
  assert.deepEqual(app.loads, []);
  assert.equal(app.state.userId, "");
  assert.match(app.elements.localTokenError.textContent, /does not unlock this instance/);
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health", "/api/local-auth/login"]);
});

test("same browser origin restores only the current instance's selected session", async () => {
  const stored = new Map([
    ["mat_sessionId", "legacy-selection"],
    [localInstanceStorageKeys("instance-a").sessionId, "session-instance-a"],
    [localInstanceStorageKeys("instance-a").owner, "user"],
    [localInstanceStorageKeys("instance-b").sessionId, "session-instance-b"],
    [localInstanceStorageKeys("instance-b").owner, "user"],
  ]);
  const a = harness({ authenticated: true, instanceId: "instance-a", stored });
  const b = harness({ authenticated: true, instanceId: "instance-b", stored });
  await a.controller.start();
  await b.controller.start();
  assert.equal(a.switches[0][0], "session-instance-a");
  assert.equal(b.switches[0][0], "session-instance-b");
  assert.notEqual(a.sessionStorageKeys.sessionId, b.sessionStorageKeys.sessionId);
  assert.equal(b.elements.userDisplay.title, "Local instance: instance-b");
});

test("fragment bootstrap removes the credential from the address immediately", () => {
  const replaced = [];
  const token = takeLocalTokenFragment({ hash: "#token=example-secret&view=chat", pathname: "/", search: "?theme=dark" }, {
    replaceState: (...args) => replaced.push(args),
  });
  assert.equal(token, "example-secret");
  assert.equal(replaced[0][2], "/?theme=dark#view=chat");
  assert.ok(!replaced[0][2].includes("example-secret"));
});

test("fragment token is exchanged once and never persisted", async () => {
  const app = harness({ bootstrapToken: "correct-secret" });
  await app.controller.start();
  assert.equal(app.requests.filter((request) => request.url === "/api/local-auth/login").length, 1);
  assert.deepEqual(app.loads, ["instance-a"]);
  assert.ok(!JSON.stringify([...app.stored]).includes("correct-secret"));
});

test("a supplied bootstrap token is checked even when the browser has a previous session", async () => {
  const app = harness({ authenticated: true, bootstrapToken: "wrong-instance-token" });
  await app.controller.start();
  assert.equal(app.requests.filter((request) => request.url === "/api/local-auth/login").length, 1);
  assert.deepEqual(app.loads, []);
  assert.equal(app.state.userId, "");
});

test("server startup retains the existing account identity flow", async () => {
  const user = "1d8b87de-22c2-4e4e-84e1-8ded12be3400";
  const app = harness({ mode: "server", stored: new Map([["mat_userId", user], ["mat_displayName", "Alice"], ["mat_deploymentMode", "server"]]) });
  await app.controller.start();
  assert.equal(app.state.userId, user);
  assert.equal(app.state.displayName, "Alice");
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health", `/api/session-access/${user}`]);
  assert.equal(app.sessionStorageKeys.sessionId, "mat_sessionId");
});

test("shared localhost redirects to the instance host before token exchange", async () => {
  const instanceId = "a".repeat(32);
  const browserHost = `mc-${instanceId}.localhost`;
  const app = harness({ instanceId, browserHost, bootstrapToken: "correct-secret", locationHref: "http://127.0.0.1:15173/?view=chat" });
  await app.controller.start();
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health"]);
  assert.deepEqual(app.loads, []);
  assert.deepEqual(app.redirects, [`http://${browserHost}:15173/?view=chat#token=correct-secret`]);
  assert.ok(!JSON.stringify([...app.stored]).includes("correct-secret"));
});

test("the canonical instance host may exchange its bootstrap token", async () => {
  const instanceId = "a".repeat(32);
  const browserHost = `mc-${instanceId}.localhost`;
  const app = harness({ instanceId, browserHost, bootstrapToken: "correct-secret", locationHref: `http://${browserHost}:15173/` });
  await app.controller.start();
  assert.deepEqual(app.redirects, []);
  assert.equal(app.requests.filter((request) => request.url === "/api/local-auth/login").length, 1);
  assert.deepEqual(app.loads, [instanceId]);
});

test("a mismatched canonical hostname cannot receive a token", async () => {
  const app = harness({ instanceId: "a".repeat(32), browserHost: "other.example.com", bootstrapToken: "correct-secret" });
  await app.controller.start();
  assert.deepEqual(app.redirects, []);
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health"]);
  assert.deepEqual(app.loads, []);
  assert.match(app.elements.localTokenError.textContent, /invalid instance address/);
});

test("a missing or expired local session locks and clears the active identity once", async () => {
  const app = harness({ authenticated: true });
  await app.controller.start();
  app.state.sessionReady = true;
  app.state.sessionViewCache.set("old", { messages: ["private"] });
  app.controller.requireLocalAccess();
  assert.equal(app.state.userId, "");
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.state.sessionReady, false);
  assert.equal(app.state.sessionViewCache.size, 0);
  assert.equal(app.elements.localTokenView.classList.contains("hidden"), false);
  assert.match(app.elements.localTokenError.textContent, /expired or changed/);
  const sessionId = app.state.sessionId;
  app.controller.requireLocalAccess();
  assert.equal(app.state.sessionId, sessionId);
});

test("manual token entry before successful startup checks the canonical host first", async () => {
  const instanceId = "a".repeat(32);
  const browserHost = `mc-${instanceId}.localhost`;
  const app = harness({ instanceId, browserHost });
  await app.controller.loginLocal("correct-secret");
  assert.deepEqual(app.requests.map((request) => request.url), ["/api/health"]);
  assert.deepEqual(app.redirects, [`http://${browserHost}:15173/#token=correct-secret`]);
});
