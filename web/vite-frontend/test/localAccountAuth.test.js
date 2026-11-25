import assert from "node:assert/strict";
import test from "node:test";
import { createAuthController, localAccountStorageKeys } from "../src/features/auth/AuthController.js";

function element() {
  const classes = new Set(["hidden"]);
  const listeners = new Map();
  return {
    classList: {
      add: (value) => classes.add(value), remove: (value) => classes.delete(value), contains: (value) => classes.has(value),
      toggle(value, enabled) { if (enabled) classes.add(value); else classes.delete(value); },
    },
    style: {}, value: "", checked: false, textContent: "", title: "", parentElement: { style: {} },
    replaceChildren() {}, appendChild() {}, focus() {}, setAttribute() {},
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name), listeners,
  };
}

const alice = { id: "a".repeat(8) + "-aaaa-4aaa-8aaa-" + "a".repeat(12), display_name: "Alice", username: "alice" };
const bob = { id: "b".repeat(8) + "-bbbb-4bbb-8bbb-" + "b".repeat(12), display_name: "Bob", username: "bob" };
function harness({ user = null, count = user ? 2 : 0, bootstrapToken = "", legacy = true, stored = new Map(), location, registrationAllowed } = {}) {
  const instance = "c".repeat(32);
  const hostname = `mc-${instance}.localhost`;
  const state = { userId: "", deploymentMode: "local", localAccountMode: true, sessionId: "fresh", sessionReady: false, sessionViewCache: new Map() };
  const names = ["loginModal", "loginView", "registerView", "localTokenView", "localTokenInput", "localTokenError",
    "localTokenSubmit", "localInstanceLabel", "userDisplay", "sessionListElement", "loginInput", "loginPassword", "loginError",
    "registerInput", "registerPassword", "registerConfirm", "registerError", "loginHeading", "registerHeading", "localAccountNotice",
    "registerPasswordHint", "legacyClaimRow", "legacyClaimInput", "legacyClaimHint", "editUserButton", "logoutButton", "passwordSection",
    "settingsLogoutButton", "currentPasswordInput", "newPasswordInput", "confirmPasswordInput", "passwordMessage",
    "registrationAccessHint", "createAccountButton", "cancelAccountCreationButton", "registerSubmit", "switchToRegister"];
  const elements = Object.fromEntries(names.map((name) => [name, element()]));
  const events = element();
  const document = Object.assign(element(), { visibilityState: "visible", createElement: element });
  const requests = [], loads = [], switches = [], redirects = [], clearEvents = [];
  const keys = { sessionId: "mat_sessionId", owner: "mat_sessionOwnerId" };
  let ownerProof = false, serial = 0, statusResponder = null;
  const reply = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const metadata = () => ({ status: "ok", mode: "local", authentication_method: "local_account", instance_id: instance,
    browser_host: hostname, authenticated: Boolean(user), user, account_count: count,
    legacy_available: legacy, can_claim_legacy: legacy && ownerProof,
    registration_allowed: registrationAllowed ?? Boolean(user || ownerProof) });
  const fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    if (url === "/api/auth/status" && statusResponder) return statusResponder(options);
    if (url === "/api/health" || url === "/api/auth/status") return reply(metadata());
    if (url === "/api/local-auth/login") { ownerProof = JSON.parse(options.body).token === "private-launch-proof"; return reply(metadata(), ownerProof ? 200 : 401); }
    if (url === "/api/auth/login") {
      const body = JSON.parse(options.body);
      if (body.password !== "synthetic-password") return reply({ detail: "Invalid username or password" }, 401);
      user = body.display_name === "Alice" ? alice : bob;
      return reply({ user_id: user.id, display_name: user.display_name });
    }
    if (url === "/api/auth/register") {
      user = alice; count++;
      return reply({ user_id: user.id, display_name: user.display_name }, 201);
    }
    if (url === "/api/auth/logout") { user = null; return reply({ status: "ok" }); }
    if (url === "/api/auth/set-password") return reply({ status: "ok" });
    throw new Error(`Unexpected request ${url}`);
  };
  const controller = createAuthController({
    state, elements, sessionStorageKeys: keys, bootstrapToken, eventTarget: events,
    createSessionId: () => `new-${++serial}`,
    clearSessionSelection() { stored.delete(keys.sessionId); stored.delete(keys.owner); },
    updateSendButtonState() {},
    async loadSessions() { loads.push(state.userId); return [{ id: `session-${state.userId}`, userId: state.userId }]; },
    async switchSession(...args) { switches.push(args); },
    onLoggedOut() { clearEvents.push(state.userId); state.sessionViewCache.clear(); }, onSessionApplied() {},
    fetchImpl: fetch, documentRef: document,
    locationRef: { href: location || `http://${hostname}:15173/`, replace: (url) => redirects.push(url) },
    storage: { getItem: (key) => stored.get(key) || null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) },
  });
  return { controller, state, elements, events, document, requests, loads, switches, redirects, stored, keys, instance, hostname, clearEvents,
    setServerUser(value) { user = value; }, setStatusResponder(value) { statusResponder = value; }, metadata, reply };
}

test("first local account startup shows registration and never asks for a token", async () => {
  const app = harness();
  await app.controller.start();
  assert.deepEqual(app.requests.map((r) => r.url), ["/api/health", "/api/auth/status"]);
  assert.equal(app.elements.registerView.classList.contains("hidden"), false);
  assert.equal(app.elements.localTokenView.classList.contains("hidden"), true);
  assert.match(app.elements.registerHeading.textContent, /local account/);
  assert.deepEqual(app.loads, []);
  assert.equal(app.elements.legacyClaimRow.classList.contains("hidden"), true);
  assert.equal(app.elements.registerSubmit.disabled, true);
  assert.match(app.elements.registrationAccessHint.textContent, /local launcher or ask a signed-in user/);
});

test("private launch proof permits legacy claim without signing into an account", async () => {
  const app = harness({ bootstrapToken: "private-launch-proof" });
  await app.controller.start();
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.state.userId, "");
  assert.equal(app.elements.legacyClaimRow.classList.contains("hidden"), false);
  assert.equal(app.elements.legacyClaimInput.checked, true);
  assert.equal(app.elements.registerSubmit.disabled, false);
  assert.deepEqual(app.loads, []);
  assert.ok(!JSON.stringify([...app.stored]).includes("private-launch-proof"));
});

test("registration confirms the cookie identity before loading personal data", async () => {
  const app = harness({ bootstrapToken: "private-launch-proof" });
  await app.controller.start();
  assert.equal(await app.controller.register("Alice", "synthetic-password", "synthetic-password"), true);
  const registration = app.requests.find((r) => r.url === "/api/auth/register");
  assert.deepEqual(JSON.parse(registration.body), { display_name: "Alice", password: "synthetic-password", claim_existing_data: true });
  assert.equal(app.state.userId, alice.id);
  assert.equal(app.elements.userDisplay.textContent, "Alice");
  assert.deepEqual(app.loads, [alice.id]);
  assert.equal(app.elements.loginModal.classList.contains("hidden"), true);
  assert.ok(!JSON.stringify([...app.stored]).includes("synthetic-password"));
});

test("login uses username and password, and wrong credentials never load data", async () => {
  const app = harness({ count: 2 });
  await app.controller.start();
  assert.equal(await app.controller.login("Alice", "wrong-password"), false);
  assert.match(app.elements.loginError.textContent, /Invalid username/);
  assert.deepEqual(app.loads, []);
  assert.equal(await app.controller.login("Alice", "synthetic-password"), true);
  assert.equal(app.state.userId, alice.id);
  assert.deepEqual(app.loads, [alice.id]);
  assert.equal(app.requests.some((r) => r.url === "/api/local-auth/login"), false);
});

test("account switching clears old view and only logs out the browser session", async () => {
  const app = harness({ user: alice });
  await app.controller.start();
  const aliceStorageKey = app.keys.sessionId;
  app.state.sessionViewCache.set("private", { text: "Alice's synthetic content" });
  assert.equal(await app.controller.switchAccount(), true);
  assert.equal(app.state.userId, "");
  assert.equal(app.state.sessionViewCache.size, 0);
  assert.equal(app.elements.loginView.classList.contains("hidden"), false);
  assert.equal(await app.controller.login("Bob", "synthetic-password"), true);
  assert.equal(app.state.userId, bob.id);
  assert.notEqual(app.keys.sessionId, aliceStorageKey);
  assert.deepEqual(app.loads, [alice.id, bob.id]);
  assert.equal(app.requests.filter((r) => r.url === "/api/auth/logout").length, 1);
  assert.equal(app.requests.some((r) => /cancel|stop|terminate/.test(r.url)), false);
});

test("selected sessions are scoped by instance and account together", async () => {
  const app = harness({ user: bob });
  const a = localAccountStorageKeys(app.instance, alice.id);
  const b = localAccountStorageKeys(app.instance, bob.id);
  app.stored.set(a.sessionId, `session-${alice.id}`); app.stored.set(a.owner, alice.id);
  app.stored.set(b.sessionId, `session-${bob.id}`); app.stored.set(b.owner, bob.id);
  await app.controller.start();
  assert.equal(app.switches[0][0], `session-${bob.id}`);
  assert.equal(app.keys.sessionId, b.sessionId);
  assert.notEqual(a.sessionId, b.sessionId);
});

test("local password change derives user identity from session, not a submitted ID", async () => {
  const app = harness({ user: alice });
  await app.controller.start();
  app.elements.currentPasswordInput.value = "old-synthetic-password";
  app.elements.newPasswordInput.value = app.elements.confirmPasswordInput.value = "new-synthetic-password";
  globalThis.window ||= { clearTimeout, setTimeout };
  assert.equal(await app.controller.savePassword(), true);
  const request = app.requests.find((r) => r.url === "/api/auth/set-password");
  assert.deepEqual(JSON.parse(request.body), { old_password: "old-synthetic-password", new_password: "new-synthetic-password" });
  assert.ok(!JSON.stringify([...app.stored]).includes("synthetic-password"));
  app.controller.destroy();
});

test("canonical redirect occurs before any account password request", async () => {
  const app = harness({ location: "http://127.0.0.1:15173/" });
  await app.controller.start();
  assert.deepEqual(app.requests.map((r) => r.url), ["/api/health"]);
  assert.deepEqual(app.redirects, [`http://${app.hostname}:15173/`]);
  assert.equal(await app.controller.login("Alice", "synthetic-password"), false);
  assert.equal(app.requests.some((r) => r.url === "/api/auth/login"), false);
});

test("another tab's account switch locks stale private views without cancellation", async () => {
  const app = harness({ user: alice });
  app.controller.init();
  await app.controller.start();
  app.state.sessionViewCache.set("private", { text: "private" });
  app.events.listeners.get("storage")({ key: `mat_localAccount:${app.instance}`, newValue: bob.id });
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.state.userId, "");
  assert.equal(app.state.sessionViewCache.size, 0);
  assert.match(app.elements.loginError.textContent, /expired or changed/);
  assert.equal(app.requests.some((r) => /logout|cancel|stop|terminate/.test(r.url)), false);
  app.controller.destroy();
});

test("short or oversized account passwords are rejected before registration", async () => {
  const app = harness({ bootstrapToken: "private-launch-proof" }); await app.controller.start();
  assert.equal(await app.controller.register("Alice", "short", "short"), false);
  const oversized = "x".repeat(1025);
  assert.equal(await app.controller.register("Alice", oversized, oversized), false);
  assert.equal(app.requests.some((r) => r.url === "/api/auth/register"), false);
});

test("an unknown visitor cannot register or see a manual token entry path", async () => {
  const app = harness({ count: 2, registrationAllowed: false });
  await app.controller.start();
  assert.equal(app.elements.loginView.classList.contains("hidden"), false);
  assert.equal(app.elements.switchToRegister.parentElement.style.display, "none");
  assert.equal(app.elements.registrationAccessHint.classList.contains("hidden"), false);
  app.controller.showRegister();
  assert.equal(app.elements.registerSubmit.disabled, true);
  assert.equal(app.elements.registerInput.disabled, true);
  assert.equal(app.elements.localTokenView.classList.contains("hidden"), true);
  assert.equal(await app.controller.register("Untrusted", "synthetic-password", "synthetic-password"), false);
  assert.match(app.elements.registerError.textContent, /Open the local launcher or sign in/);
  assert.equal(app.requests.some((r) => r.url === "/api/auth/register"), false);
});

test("a signed-in user may open another-account creation without logging out a worker", async () => {
  const app = harness({ user: alice });
  app.controller.init();
  await app.controller.start();
  assert.equal(app.elements.createAccountButton.classList.contains("hidden"), false);
  app.elements.createAccountButton.listeners.get("click")();
  assert.equal(app.elements.registerView.classList.contains("hidden"), false);
  assert.equal(app.elements.registerSubmit.disabled, false);
  assert.equal(app.state.userId, alice.id);
  assert.equal(app.requests.some((r) => /logout|cancel|terminate/.test(r.url)), false);
  app.elements.cancelAccountCreationButton.listeners.get("click")();
  assert.equal(app.elements.loginModal.classList.contains("hidden"), true);
  assert.equal(app.state.userId, alice.id);
  await app.controller.logout();
  assert.equal(app.elements.createAccountButton.classList.contains("hidden"), true);
  assert.equal(app.elements.switchToRegister.parentElement.style.display, "none");
  app.controller.destroy();
});

test("resume events revalidate once without resetting an unchanged account or live view", async () => {
  const app = harness({ user: alice });
  app.controller.init(); await app.controller.start();
  const sessionId = app.state.sessionId;
  app.state.sessionViewCache.set("private", { text: "Alice" });
  const checks = [app.events.listeners.get("pageshow")(), app.events.listeners.get("focus")(),
    app.document.listeners.get("visibilitychange")()];
  await Promise.all(checks);
  assert.equal(app.requests.filter(r => r.url === "/api/auth/status").length, 2);
  assert.equal(app.requests.at(-1).cache, "no-store");
  assert.equal(app.state.localAuthenticated, true);
  assert.equal(app.state.sessionId, sessionId);
  assert.equal(app.state.sessionViewCache.size, 1);
  assert.deepEqual(app.loads, [alice.id]);
  assert.deepEqual(app.clearEvents, []);
  app.controller.destroy();
  assert.equal(app.events.listeners.has("pageshow"), false);
  assert.equal(app.events.listeners.has("focus"), false);
  assert.equal(app.document.listeners.has("visibilitychange"), false);
});

test("resuming a stale page clears private state when the browser cookie now belongs to another account", async () => {
  const app = harness({ user: alice });
  app.controller.init(); await app.controller.start();
  app.state.sessionViewCache.set("private", { text: "Alice" });
  app.setServerUser(bob);
  await app.events.listeners.get("pageshow")({ persisted: true });
  assert.equal(app.state.userId, "");
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.state.sessionViewCache.size, 0);
  assert.deepEqual(app.loads, [alice.id]);
  assert.equal(app.clearEvents.length, 1);
  assert.equal(app.requests.some(r => /logout|cancel|stop|terminate/.test(r.url)), false);
  app.controller.destroy();
});

test("hidden pages do not revalidate, but visibility restoration detects revoked sessions", async () => {
  const app = harness({ user: alice });
  app.controller.init(); await app.controller.start();
  app.document.visibilityState = "hidden";
  await app.document.listeners.get("visibilitychange")();
  assert.equal(app.requests.filter(r => r.url === "/api/auth/status").length, 1);
  app.setServerUser(null); app.document.visibilityState = "visible";
  await app.document.listeners.get("visibilitychange")();
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.clearEvents.length, 1);
  app.controller.destroy();
});

test("a resume identity check cannot clear a newer explicit login", async () => {
  const app = harness({ user: alice });
  app.controller.init(); await app.controller.start();
  let release;
  app.setStatusResponder(() => new Promise(resolve => { release = resolve; }));
  const checking = app.events.listeners.get("focus")();
  app.setStatusResponder(null);
  assert.equal(await app.controller.login("Bob", "synthetic-password"), true);
  release(app.reply({ ...app.metadata(), authenticated: false, user: null }));
  await checking;
  assert.equal(app.state.userId, bob.id);
  assert.equal(app.state.localAuthenticated, true);
  assert.deepEqual(app.loads, [alice.id, bob.id]);
  app.controller.destroy();
});

test("unverifiable resume identity locks the old view instead of trusting cached login", async () => {
  const app = harness({ user: alice });
  app.controller.init(); await app.controller.start();
  app.setStatusResponder(() => { throw new TypeError("offline"); });
  await app.events.listeners.get("focus")();
  assert.equal(app.state.localAuthenticated, false);
  assert.equal(app.clearEvents.length, 1);
  assert.deepEqual(app.loads, [alice.id]);
  app.controller.destroy();
});
