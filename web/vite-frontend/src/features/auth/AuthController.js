const USER_ID_KEY = "mat_userId";
const DISPLAY_NAME_KEY = "mat_displayName";
const DEPLOYMENT_MODE_KEY = "mat_deploymentMode";

export function localInstanceStorageKeys(instanceId) {
  const scope = encodeURIComponent(instanceId);
  return {
    sessionId: `mat_sessionId:${scope}`,
    owner: `mat_sessionOwnerId:${scope}`,
    userId: `${USER_ID_KEY}:${scope}`,
    displayName: `${DISPLAY_NAME_KEY}:${scope}`,
  };
}

export function localAccountStorageKeys(instanceId, userId) {
  return localInstanceStorageKeys(`${instanceId}:${userId}`);
}

// Fragments never travel in HTTP requests. Remove the bootstrap credential
// before application requests and never persist it in browser storage.
export function takeLocalTokenFragment(location, history) {
  if (!location?.hash) return "";
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("token") || "";
  if (!fragment.has("token")) return "";
  fragment.delete("token");
  const remaining = fragment.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}${remaining ? `#${remaining}` : ""}`);
  return token;
}

function safeStorage(storage) {
  return {
    get(key) {
      try { return storage?.getItem(key) || ""; } catch (_) { return ""; }
    },
    set(key, value) {
      try { storage?.setItem(key, value); } catch (_) { /* non-critical preference */ }
    },
    remove(key) {
      try { storage?.removeItem(key); } catch (_) { /* non-critical preference */ }
    },
  };
}

export function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

export function isValidIdentity(value) {
  return value === "user" || isUuid(value);
}

export function validatedStoredSession(sessions, {
  sessionId,
  storedOwner,
  userId,
  deploymentMode,
  isAdmin,
}) {
  if (!sessionId || !Array.isArray(sessions)) return null;
  let owner = userId;
  if (deploymentMode === "server" && isAdmin) {
    if (!storedOwner) return null;
    owner = storedOwner;
  } else if (deploymentMode === "server" && storedOwner && storedOwner !== userId) {
    return null;
  }
  const found = sessions.find((session) => (
    session.id === sessionId && (session.userId || userId) === owner
  ));
  return found ? {
    sessionId,
    owner,
    knownRunning: String(found.status || found.phase || "").toLowerCase() === "running",
    knownRun: found.activeRun || null,
  } : null;
}

export function createAuthController({
  state,
  elements,
  sessionStorageKeys,
  createSessionId,
  clearSessionSelection,
  updateSendButtonState,
  loadSessions,
  switchSession,
  onLoggedOut,
  onSessionApplied,
  onAuthenticated = () => {},
  onRegistrationRequested = () => {},
  fetchImpl = globalThis.fetch,
  storage = globalThis.localStorage,
  documentRef = globalThis.document,
  locationRef = globalThis.location,
  eventTarget = globalThis.window,
  bootstrapToken = "",
}) {
  const stored = safeStorage(storage);
  const {
    loginModal,
    loginInput,
    loginPassword,
    loginError,
    loginUuidDisplay,
    loginSubmit,
    loginView,
    registerView,
    registerInput,
    registerPassword,
    registerConfirm,
    registerError,
    registerSubmit,
    switchToRegister,
    switchToLogin,
    userDisplay,
    editUserButton,
    logoutButton,
    settingsLogoutButton,
    savePasswordButton,
    passwordMessage,
    currentPasswordInput,
    newPasswordInput,
    confirmPasswordInput,
    passwordSection,
    sessionIdElement,
    sessionListElement,
    localTokenView,
    localTokenInput,
    localTokenSubmit,
    localTokenError,
    localInstanceLabel,
    loginHeading,
    registerHeading,
    localAccountNotice,
    registerPasswordHint,
    legacyClaimRow,
    legacyClaimInput,
    legacyClaimHint,
    registrationAccessHint,
    createAccountButton,
    cancelAccountCreationButton,
  } = elements;
  let initialized = false;
  let startupPromise = null;
  let authRequestController = null;
  let identityCheckController = null;
  let identityCheckPromise = null;
  let passwordMessageTimer = null;
  let pendingBootstrapToken = bootstrapToken;
  bootstrapToken = "";
  const serverSessionStorageKeys = { ...sessionStorageKeys };

  function accountKeys(userId = state.userId) {
    return state.localAccountMode
      ? localAccountStorageKeys(state.instanceId, userId)
      : localInstanceStorageKeys(state.instanceId);
  }

  function accountRevisionKey() {
    return `mat_localAccount:${state.instanceId}`;
  }

  function onAccountStorageChange(event) {
    if (state.localAccountMode && state.localAuthenticated
        && event.key === accountRevisionKey() && event.newValue !== state.userId) requireLocalAccess();
  }

  function cancelIdentityCheck() {
    identityCheckController?.abort();
    identityCheckController = null;
    identityCheckPromise = null;
  }

  // Returning from a suspended/background page may miss a storage event. Check
  // the server cookie without restarting sessions or interrupting live work
  // when the identity is unchanged. Coalesce focus/pageshow/visibility events.
  function revalidateLocalIdentity() {
    if (!state.localAccountMode || !state.localAuthenticated || !state.localAuthReady
        || state.deploymentMode !== "local" || documentRef?.visibilityState === "hidden") return Promise.resolve(false);
    if (identityCheckPromise) return identityCheckPromise;
    const accountId = state.userId;
    const instanceId = state.instanceId;
    const controller = new AbortController();
    identityCheckController = controller;
    const stillCurrent = () => !controller.signal.aborted && state.localAuthenticated
      && state.userId === accountId && state.instanceId === instanceId;
    identityCheckPromise = (async () => {
      try {
        const response = await fetchImpl("/api/auth/status", {
          signal: controller.signal, cache: "no-store", credentials: "same-origin",
        });
        const status = response.ok ? await response.json() : null;
        if (!stillCurrent()) return false;
        if (!status?.authenticated || status.user?.id !== accountId || status.instance_id !== instanceId
            || status.authentication_method !== "local_account") {
          requireLocalAccess();
          return false;
        }
        return true;
      } catch (error) {
        if (error.name !== "AbortError" && stillCurrent()) requireLocalAccess();
        return false;
      } finally {
        if (identityCheckController === controller) {
          identityCheckController = null;
          identityCheckPromise = null;
        }
      }
    })();
    return identityCheckPromise;
  }

  function onPageResume() {
    return revalidateLocalIdentity();
  }

  function setAccountControls() {
    if (loginHeading) loginHeading.textContent = state.localAccountMode ? "Sign in to your local account" : "Sign in to continue";
    if (registerHeading) registerHeading.textContent = state.localAccountMode ? "Create a local account" : "Create a new account";
    if (localAccountNotice) {
      localAccountNotice.textContent = "Accounts and personal data stay on this machine. No cloud account is created.";
      localAccountNotice.classList.toggle("hidden", !state.localAccountMode);
    }
    if (registerPasswordHint) registerPasswordHint.classList.toggle("hidden", !state.localAccountMode);
    const registrationBlocked = state.localAccountMode && state.registrationAllowed !== true;
    if (registrationAccessHint) {
      registrationAccessHint.textContent = "To create a local account, open MC from its local launcher or ask a signed-in user.";
      registrationAccessHint.classList.toggle("hidden", !registrationBlocked);
    }
    for (const input of [registerInput, registerPassword, registerConfirm, legacyClaimInput]) {
      if (input) input.disabled = registrationBlocked;
    }
    if (registerSubmit) {
      registerSubmit.disabled = registrationBlocked;
      registerSubmit.textContent = registrationBlocked ? "Registration unavailable"
        : state.localAccountMode ? "Create local account →" : "Register →";
    }
    const registerLink = switchToRegister?.parentElement;
    if (registerLink && state.localAccountMode) registerLink.style.display = registrationBlocked ? "none" : "";
    if (createAccountButton) createAccountButton.classList.toggle("hidden",
      !(state.localAccountMode && state.localAuthenticated && !registrationBlocked));
    if (cancelAccountCreationButton) cancelAccountCreationButton.classList.toggle("hidden",
      !(state.localAccountMode && state.localAuthenticated));
    if (registerPassword) registerPassword.minLength = state.localAccountMode ? 10 : 0;
    if (legacyClaimRow) legacyClaimRow.classList.toggle("hidden", !(state.localAccountMode && state.canClaimLegacy));
    if (legacyClaimHint) {
      legacyClaimHint.textContent = state.canClaimLegacy
        ? "Only this account will receive the existing conversations, files and settings. They will not be copied to new accounts."
        : "Existing installation data is preserved. Use the private launcher on this machine to claim it during account creation.";
      legacyClaimHint.classList.toggle("hidden", !(state.localAccountMode && state.legacyAvailable));
    }
    if (state.localAccountMode) {
      [editUserButton, passwordSection, logoutButton, settingsLogoutButton].forEach((element) => {
        if (element) element.style.display = "";
      });
      if (editUserButton) {
        editUserButton.title = "Switch account";
        editUserButton.setAttribute?.("aria-label", "Switch account");
      }
      if (logoutButton) {
        logoutButton.title = "Log out";
        logoutButton.setAttribute?.("aria-label", "Log out");
      }
      if (settingsLogoutButton) settingsLogoutButton.textContent = "Log out";
    }
  }

  function redirectToInstanceHost(browserHost, token = "") {
    if (!browserHost) return false;
    const destination = new URL(locationRef.href);
    if (destination.hostname === browserHost) return false;
    destination.hostname = browserHost;
    if (token) {
      const fragment = new URLSearchParams(destination.hash.slice(1));
      fragment.set("token", token);
      destination.hash = fragment.toString();
    }
    locationRef.replace(destination.href);
    return true;
  }

  function showLocalLogin() {
    if (state.localAccountMode) return showLogin();
    loginModal?.classList.remove("hidden");
    loginView?.classList.add("hidden");
    registerView?.classList.add("hidden");
    localTokenView?.classList.remove("hidden");
    if (localTokenInput) localTokenInput.value = "";
    if (localInstanceLabel) localInstanceLabel.textContent = state.instanceId
      ? `Instance: ${state.instanceId}` : "Connecting to local instance…";
    localTokenInput?.focus();
  }

  function showLogin() {
    if (state.deploymentMode !== "server" && !state.localAccountMode) {
      showLocalLogin();
      return;
    }
    loginModal?.classList.remove("hidden");
    loginView?.classList.remove("hidden");
    registerView?.classList.add("hidden");
    localTokenView?.classList.add("hidden");
    setAccountControls();
    if (loginInput) loginInput.value = state.displayName || "";
    if (loginPassword) loginPassword.value = "";
    if (loginError) loginError.textContent = "";
    if (loginUuidDisplay) loginUuidDisplay.textContent = state.userId ? `UUID: ${state.userId}` : "";
    const registerLink = switchToRegister?.parentElement;
    if (registerLink) registerLink.style.display = state.localAccountMode
      ? (state.registrationAllowed === true ? "" : "none")
      : state.userId ? "none" : "";
    loginInput?.focus();
  }

  function showRegister() {
    if (state.deploymentMode !== "server" && !state.localAccountMode) return showLocalLogin();
    onRegistrationRequested();
    loginModal?.classList.remove("hidden");
    loginView?.classList.add("hidden");
    registerView?.classList.remove("hidden");
    localTokenView?.classList.add("hidden");
    setAccountControls();
    if (registerInput) registerInput.value = "";
    if (registerPassword) registerPassword.value = "";
    if (registerConfirm) registerConfirm.value = "";
    if (registerError) registerError.textContent = "";
    if (legacyClaimInput) legacyClaimInput.checked = Boolean(state.localAccountMode && state.canClaimLegacy);
    registerInput?.focus();
  }

  function hideModal() {
    loginModal?.classList.add("hidden");
  }

  function renderUser() {
    if (!userDisplay) return;
    const label = state.displayName || state.userId;
    userDisplay.textContent = state.deploymentMode === "local" && state.instanceId && !state.localAccountMode
      ? `Local · ${state.instanceId.slice(0, 8)}`
      : state.isAdmin ? `${label} (admin)` : label;
    userDisplay.title = state.localAccountMode
      ? `${label} · Local account`
      : state.deploymentMode === "local" && state.instanceId
      ? `Local instance: ${state.instanceId}` : label;
  }

  function canWriteActiveSession() {
    if (!state.userId || (state.deploymentMode === "local" && !state.localAuthenticated)) return false;
    return state.deploymentMode === "local"
      || !state.activeSessionUserId
      || state.activeSessionUserId === state.userId;
  }

  function activeSessionBackendUserId() {
    return state.deploymentMode === "local"
      ? (state.activeSessionUserId || state.userId)
      : state.userId;
  }

  function clearStoredIdentity() {
    const keys = state.deploymentMode === "local" && state.instanceId
      ? accountKeys() : { userId: USER_ID_KEY, displayName: DISPLAY_NAME_KEY };
    stored.remove(keys.userId);
    stored.remove(keys.displayName);
    clearSessionSelection();
  }

  function applyLocalIdentity(resetSession = false) {
    state.deploymentMode = "local";
    state.userId = "user";
    state.displayName = "user";
    state.activeSessionUserId = "user";
    state.isAdmin = false;
    state.sessionReady = false;
    if (resetSession) {
      state.sessionId = createSessionId();
      clearSessionSelection();
    }
    stored.set(DEPLOYMENT_MODE_KEY, "local");
    const keys = localInstanceStorageKeys(state.instanceId);
    stored.set(keys.userId, "user");
    stored.set(keys.displayName, "user");
  }

  function hideLocalAuthControls() {
    [editUserButton, passwordSection].forEach((element) => {
      if (element) element.style.display = "none";
    });
    if (logoutButton) {
      logoutButton.style.display = "";
      logoutButton.title = "Lock local instance";
      logoutButton.setAttribute?.("aria-label", "Lock local instance");
    }
    if (settingsLogoutButton) {
      settingsLogoutButton.style.display = "";
      settingsLogoutButton.textContent = "Lock local instance";
    }
  }

  async function refreshAccess(signal) {
    state.isAdmin = false;
    if (!state.userId) return;
    try {
      const response = await fetchImpl(`/api/session-access/${encodeURIComponent(state.userId)}`, { signal });
      if (!response.ok) return;
      const access = await response.json();
      state.isAdmin = Boolean(access.is_admin);
    } catch (error) {
      if (error.name !== "AbortError") state.isAdmin = false;
    }
  }

  function applySession(result) {
    if (!result?.user_id) throw new Error("Authentication response did not include a user ID.");
    state.userId = result.user_id;
    state.displayName = result.display_name || result.user_id;
    state.activeSessionUserId = result.user_id;
    state.sessionId = createSessionId();
    state.sessionReady = false;
    state.isAdmin = Boolean(result.is_admin);
    updateSendButtonState();
    if (loginUuidDisplay) loginUuidDisplay.textContent = `UUID: ${result.user_id}`;
    stored.set(DEPLOYMENT_MODE_KEY, state.deploymentMode);
    stored.set(USER_ID_KEY, result.user_id);
    stored.set(DISPLAY_NAME_KEY, state.displayName);
    clearSessionSelection();
    if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
    renderUser();
    hideModal();
    onSessionApplied();
  }

  async function authenticate(url, payload, errorElement, errorPrefix) {
    cancelIdentityCheck();
    authRequestController?.abort();
    const controller = new AbortController();
    authRequestController = controller;
    if (errorElement) errorElement.textContent = "";
    try {
      const response = await fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (errorElement) errorElement.textContent = body.detail || `${errorPrefix} (${response.status})`;
        return false;
      }
      if (state.localAccountMode) {
        // An account ID in JSON is not authentication. Confirm the new
        // HttpOnly session and derive identity from the server before loading.
        if (loginPassword) loginPassword.value = "";
        if (registerPassword) registerPassword.value = "";
        if (registerConfirm) registerConfirm.value = "";
        await runStartup();
        if (!state.localAuthenticated) {
          if (errorElement) errorElement.textContent = "The login session was not retained. Check browser cookies and try again.";
          return false;
        }
        return true;
      }
      applySession(body);
      return true;
    } catch (error) {
      if (error.name !== "AbortError" && errorElement) {
        errorElement.textContent = `${errorPrefix}: ${error.message}`;
      }
      return false;
    } finally {
      if (authRequestController === controller) authRequestController = null;
    }
  }

  async function readyForAccountCredential() {
    if (!state.localAuthReady) await runStartup();
    if (!state.localAuthReady || !state.localAccountMode) return false;
    return !redirectToInstanceHost(state.localBrowserHost);
  }

  async function login(displayName, password = null) {
    if (state.deploymentMode === "local" && !state.localAccountMode) return loginLocal(displayName);
    if (state.localAccountMode && !await readyForAccountCredential()) return false;
    return authenticate(
      "/api/auth/login",
      { display_name: displayName, password },
      loginError,
      "Login failed",
    );
  }

  async function loginLocal(token) {
    if (!token?.trim()) {
      if (localTokenError) localTokenError.textContent = "Enter this instance's access token.";
      return false;
    }
    if (!state.localAuthReady) {
      // Startup might have failed while the service was still starting. Obtain
      // the instance host before accepting a manually entered credential.
      pendingBootstrapToken = token.trim();
      await runStartup();
      return Boolean(state.localAuthenticated);
    }
    if (redirectToInstanceHost(state.localBrowserHost, token.trim())) return false;
    authRequestController?.abort();
    const controller = new AbortController();
    authRequestController = controller;
    if (localTokenError) localTokenError.textContent = "";
    if (localTokenSubmit) localTokenSubmit.disabled = true;
    try {
      const response = await fetchImpl("/api/local-auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
        signal: controller.signal,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (localTokenError) localTokenError.textContent = response.status === 401
          ? "This token does not unlock this instance. Check the token and address."
          : body.detail || `Could not unlock instance (${response.status}).`;
        return false;
      }
      if (localTokenInput) localTokenInput.value = "";
      // Confirm the HttpOnly cookie through a fresh health request before any
      // protected data is fetched; a successful POST alone is insufficient.
      await runStartup();
      if (!state.localAuthenticated && localTokenError) {
        localTokenError.textContent = "The login session was not retained. Check browser cookies and try again.";
      }
      return Boolean(state.localAuthenticated);
    } catch (error) {
      if (error.name !== "AbortError" && localTokenError) localTokenError.textContent = "Could not connect to this instance.";
      return false;
    } finally {
      if (localTokenSubmit) localTokenSubmit.disabled = false;
      if (authRequestController === controller) authRequestController = null;
    }
  }

  async function register(displayName, password, confirm) {
    if (registerError) registerError.textContent = "";
    if (state.localAccountMode && state.registrationAllowed !== true) {
      if (registerError) registerError.textContent = "Open the local launcher or sign in before creating another account";
      return false;
    }
    if (password !== confirm) {
      if (registerError) registerError.textContent = "Passwords do not match.";
      return false;
    }
    if (state.localAccountMode && [...password].length < 10) {
      if (registerError) registerError.textContent = "Use at least 10 characters for your local password.";
      return false;
    }
    if (state.localAccountMode && new TextEncoder().encode(password).length > 1024) {
      if (registerError) registerError.textContent = "The local password must be no more than 1024 bytes.";
      return false;
    }
    if (state.localAccountMode && !await readyForAccountCredential()) return false;
    return authenticate(
      "/api/auth/register",
      { display_name: displayName, password, ...(state.localAccountMode ? { claim_existing_data: Boolean(state.canClaimLegacy && legacyClaimInput?.checked) } : {}) },
      registerError,
      "Registration failed",
    );
  }

  async function logout() {
    cancelIdentityCheck();
    authRequestController?.abort();
    const userId = state.userId;
    if (state.localAccountMode) {
      try {
        const response = await fetchImpl("/api/auth/logout", { method: "POST" });
        if (!response.ok && response.status !== 401) throw new Error("Session could not be revoked");
      } catch (_) {
        showLogin();
        if (loginError) loginError.textContent = "Could not sign out securely. Reconnect to this local service and try again.";
        return false;
      }
      state.localAuthenticated = false;
      stored.set(accountRevisionKey(), "");
    } else if (state.deploymentMode === "local") {
      try { await fetchImpl("/api/local-auth/logout", { method: "POST" }); } catch (_) { /* Clear the local view even when disconnected. */ }
      state.localAuthenticated = false;
    } else if (state.deploymentMode === "server" && userId) {
      try {
        await fetchImpl("/api/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: userId }),
        });
      } catch (_) {
        // Worker shutdown is best effort; local state must still be cleared.
      }
    }
    clearStoredIdentity();
    state.userId = "";
    state.displayName = "";
    state.activeSessionUserId = "";
    state.isAdmin = false;
    state.sessionReady = false;
    stored.remove(DEPLOYMENT_MODE_KEY);
    if (userDisplay) userDisplay.textContent = "—";
    updateSendButtonState();
    onLoggedOut();
    showLogin();
    if (state.localAccountMode) await runStartup();
    return true;
  }

  async function switchAccount() {
    if (state.localAccountMode) return logout();
    showLogin();
    return true;
  }

  function requireLocalAccess() {
    if (state.deploymentMode !== "local" || !state.localAuthenticated) return;
    cancelIdentityCheck();
    authRequestController?.abort();
    state.localAuthenticated = false;
    if (state.localAccountMode) {
      state.registrationAllowed = false;
      state.canClaimLegacy = false;
    }
    state.userId = "";
    state.displayName = "";
    state.activeSessionUserId = "";
    state.sessionReady = false;
    state.isAdmin = false;
    state.sessionId = createSessionId();
    state.sessionViewCache?.clear();
    state.sessionSummaries = {};
    updateSendButtonState();
    onLoggedOut();
    showLocalLogin();
    const errorElement = state.localAccountMode ? loginError : localTokenError;
    if (errorElement) errorElement.textContent = state.localAccountMode
      ? "Your local login has expired or changed. Sign in again."
      : "Your local session has expired or changed. Unlock this instance again.";
  }

  async function savePassword() {
    const oldPassword = currentPasswordInput?.value || null;
    const newPassword = newPasswordInput?.value || "";
    const confirmPassword = confirmPasswordInput?.value || "";
    if (!passwordMessage) return false;
    window.clearTimeout(passwordMessageTimer);
    passwordMessage.style.color = "#f87171";
    if (!newPassword) {
      passwordMessage.textContent = "New password cannot be empty.";
      return false;
    }
    if (newPassword !== confirmPassword) {
      passwordMessage.textContent = "Passwords do not match.";
      return false;
    }
    if (state.localAccountMode && ([...newPassword].length < 10 || new TextEncoder().encode(newPassword).length > 1024)) {
      passwordMessage.textContent = "Use at least 10 characters and no more than 1024 bytes for your local password.";
      return false;
    }
    try {
      const response = await fetchImpl("/api/auth/set-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(state.localAccountMode ? {} : { user_id: state.userId }),
          old_password: oldPassword,
          new_password: newPassword,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        passwordMessage.textContent = data.detail || "Failed to update password.";
        return false;
      }
      passwordMessage.style.color = "#4ade80";
      passwordMessage.textContent = "Password updated.";
      if (currentPasswordInput) currentPasswordInput.value = "";
      if (newPasswordInput) newPasswordInput.value = "";
      if (confirmPasswordInput) confirmPasswordInput.value = "";
      passwordMessageTimer = window.setTimeout(() => {
        passwordMessage.textContent = "";
      }, 3000);
      return true;
    } catch (_) {
      passwordMessage.textContent = "Network error.";
      return false;
    }
  }

  function startupDelay(milliseconds, signal) {
    return new Promise((resolve) => {
      const timer = window.setTimeout(resolve, milliseconds);
      signal?.addEventListener("abort", () => {
        window.clearTimeout(timer);
        resolve();
      }, { once: true });
    });
  }

  async function getStartupHealth(signal) {
    for (let attempt = 0; attempt < 8 && !signal.aborted; attempt += 1) {
      try {
        const response = await fetchImpl("/api/health", { signal });
        if (response.ok) return response.json();
      } catch (error) {
        if (error.name === "AbortError") return null;
      }
      if (attempt < 7) await startupDelay(250 * (attempt + 1), signal);
    }
    return null;
  }

  async function runLocalAccountStartup(health, controller) {
    state.localAccountMode = true;
    state.localAuthReady = true;
    state.deploymentMode = "local";
    state.instanceId = health.instance_id || "";
    state.localBrowserHost = health.browser_host || "";
    if (pendingBootstrapToken) {
      const token = pendingBootstrapToken;
      pendingBootstrapToken = "";
      // A private launch proof can claim pre-existing local data. It never
      // signs into a personal account and is never presented as a user field.
      await fetchImpl("/api/local-auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }), signal: controller.signal,
      });
      if (controller.signal.aborted) return;
    }
    const response = await fetchImpl("/api/auth/status", { signal: controller.signal });
    if (controller.signal.aborted) return;
    if (!response.ok) {
      state.localAuthenticated = false;
      state.userId = "";
      showLogin();
      if (loginError) loginError.textContent = "Cannot check the local account service. Try again.";
      return;
    }
    const status = await response.json();
    state.legacyAvailable = Boolean(status.legacy_available);
    state.canClaimLegacy = Boolean(status.can_claim_legacy);
    state.accountCount = Number(status.account_count || 0);
    state.registrationAllowed = status.registration_allowed !== false;
    const current = status.authenticated && status.user?.id ? status.user : null;
    if (!current) {
      const wasAuthenticated = state.localAuthenticated;
      state.localAuthenticated = false;
      state.userId = "";
      state.displayName = "";
      state.activeSessionUserId = "";
      state.sessionReady = false;
      state.isAdmin = false;
      updateSendButtonState();
      if (wasAuthenticated) onLoggedOut();
      if (state.accountCount === 0) showRegister();
      else showLogin();
      return;
    }
    const changed = state.userId !== current.id;
    if (changed) {
      if (state.userId) onLoggedOut();
      state.sessionId = createSessionId();
      state.sessionViewCache?.clear();
      state.sessionSummaries = {};
      state.sessionReady = false;
    }
    state.localAuthenticated = true;
    state.userId = current.id;
    state.displayName = current.display_name || current.username || current.id;
    state.profileId = current.profile_id || current.id;
    state.activeSessionUserId = current.id;
    state.isAdmin = false;
    const keys = accountKeys();
    sessionStorageKeys.sessionId = keys.sessionId;
    sessionStorageKeys.owner = keys.owner;
    stored.set(DEPLOYMENT_MODE_KEY, "local");
    stored.set(keys.userId, current.id);
    stored.set(keys.displayName, state.displayName);
    if (stored.get(accountRevisionKey()) !== current.id) stored.set(accountRevisionKey(), current.id);
    setAccountControls();
    updateSendButtonState();
    renderUser();
    hideModal();
    if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
    const selectedId = stored.get(keys.sessionId);
    const selectedOwner = stored.get(keys.owner);
    const sessions = await loadSessions({ retries: 7 });
    if (controller.signal.aborted) return;
    onAuthenticated();
    const selected = validatedStoredSession(sessions, {
      sessionId: selectedId, storedOwner: selectedOwner,
      userId: current.id, deploymentMode: "local", isAdmin: false,
    });
    if (selected) await switchSession(selected.sessionId, selected.owner, {
      knownRunning: selected.knownRunning, knownRun: selected.knownRun,
    });
    else if (sessions && selectedId) clearSessionSelection();
  }

  async function runStartup() {
    cancelIdentityCheck();
    const controller = new AbortController();
    authRequestController = controller;
    const health = await getStartupHealth(controller.signal);
    if (controller.signal.aborted) return;
    if (!health) {
      state.userId = "";
      state.localAuthenticated = false;
      state.localAuthReady = false;
      showLocalLogin();
      const errorElement = state.localAccountMode ? loginError : localTokenError;
      if (errorElement) errorElement.textContent = "Cannot reach the MC service. Check the instance address and try again.";
      return;
    }
    if (health.mode === "local" && health.browser_host) {
      const expectedHost = `mc-${health.instance_id}.localhost`;
      if (!/^[a-f0-9]{32}$/.test(health.instance_id || "") || health.browser_host !== expectedHost) {
        state.userId = "";
        state.localAuthenticated = false;
        state.localAuthReady = false;
        showLocalLogin();
        const errorElement = state.localAccountMode ? loginError : localTokenError;
        if (errorElement) errorElement.textContent = "The service returned an invalid instance address. Check the launcher and address.";
        return;
      }
      state.localBrowserHost = health.browser_host;
      if (redirectToInstanceHost(health.browser_host, pendingBootstrapToken)) {
        pendingBootstrapToken = "";
        // Cookies do not isolate ports. Move to this instance's host before
        // exchanging a token or making any protected browser request.
        return;
      }
    }
    state.deploymentMode = health?.mode === "server" ? "server" : "local";
    state.localAccountMode = state.deploymentMode === "local" && health.authentication_method === "local_account";
    if (state.localAccountMode) {
      await runLocalAccountStartup(health, controller);
      return;
    }
    const storedMode = stored.get(DEPLOYMENT_MODE_KEY);
    const storedId = stored.get(USER_ID_KEY);
    if (state.deploymentMode === "local") {
      state.localAuthReady = true;
      const previousInstance = state.instanceId;
      state.instanceId = typeof health.instance_id === "string" ? health.instance_id : "";
      state.localAuthenticated = Boolean(state.instanceId && health.authenticated === true);
      if (state.instanceId) {
        const keys = localInstanceStorageKeys(state.instanceId);
        sessionStorageKeys.sessionId = keys.sessionId;
        sessionStorageKeys.owner = keys.owner;
      }
      if (previousInstance !== state.instanceId) {
        state.sessionId = createSessionId();
        state.activeSessionUserId = "";
        state.sessionReady = false;
        state.sessionViewCache?.clear();
        state.sessionSummaries = {};
      }
      hideLocalAuthControls();
      if (pendingBootstrapToken) {
        const token = pendingBootstrapToken;
        pendingBootstrapToken = "";
        state.localAuthenticated = false;
        state.userId = "";
        updateSendButtonState();
        showLocalLogin();
        await loginLocal(token);
        return;
      }
      if (!state.localAuthenticated) {
        state.userId = "";
        updateSendButtonState();
        showLocalLogin();
        return;
      }
      pendingBootstrapToken = "";
    } else {
      pendingBootstrapToken = "";
      Object.assign(sessionStorageKeys, serverSessionStorageKeys);
      state.userId = storedId;
      state.displayName = stored.get(DISPLAY_NAME_KEY) || storedId;
      state.activeSessionUserId = storedId;
    }
    const storedSessionId = stored.get(sessionStorageKeys.sessionId);
    const storedSessionOwner = stored.get(sessionStorageKeys.owner);

    if (state.deploymentMode === "local") {
      hideLocalAuthControls();
      applyLocalIdentity();
      updateSendButtonState();
      hideModal();
    } else if ((storedMode && storedMode !== "server") || (!storedMode && storedId === "user")) {
      clearStoredIdentity();
      showLogin();
      return;
    } else if (!storedId || !isValidIdentity(storedId)) {
      showLogin();
      return;
    }

    if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
    await refreshAccess(controller.signal);
    if (controller.signal.aborted) return;
    renderUser();
    if (sessionListElement) {
      sessionListElement.replaceChildren();
      const loading = documentRef.createElement("li");
      loading.className = "empty";
      loading.textContent = "Loading saved sessions…";
      sessionListElement.appendChild(loading);
    }
    const sessions = await loadSessions({ retries: 7 });
    if (controller.signal.aborted) return;
    onAuthenticated();
    const selected = validatedStoredSession(sessions, {
      sessionId: storedSessionId,
      storedOwner: storedSessionOwner,
      userId: state.userId,
      deploymentMode: state.deploymentMode,
      isAdmin: state.isAdmin,
    });
    if (selected) {
      await switchSession(selected.sessionId, selected.owner, {
        knownRunning: selected.knownRunning,
        knownRun: selected.knownRun,
      });
    } else if (sessions && storedSessionId) {
      clearSessionSelection();
      state.sessionId = createSessionId();
      state.activeSessionUserId = state.userId;
      state.sessionReady = false;
      if (sessionIdElement) sessionIdElement.textContent = state.sessionId;
      updateSendButtonState();
    }
  }

  function start() {
    startupPromise ||= runStartup().finally(() => {
      authRequestController = null;
    });
    return startupPromise;
  }

  const submitLogin = () => {
    const name = loginInput?.value.trim();
    if (name) void login(name, loginPassword?.value || null);
  };
  const submitLocalToken = () => void loginLocal(localTokenInput?.value || "");
  const submitLocalTokenOnEnter = (event) => {
    if (event.key === "Enter") submitLocalToken();
  };
  const submitRegistration = () => {
    const name = registerInput?.value.trim();
    if (name) void register(name, registerPassword?.value || "", registerConfirm?.value || "");
  };
  const focusLoginPassword = (event) => {
    if (event.key === "Enter") loginPassword?.focus();
  };
  const submitLoginOnEnter = (event) => {
    if (event.key === "Enter") submitLogin();
  };
  const focusRegisterPassword = (event) => {
    if (event.key === "Enter") registerPassword?.focus();
  };
  const focusRegisterConfirm = (event) => {
    if (event.key === "Enter") registerConfirm?.focus();
  };
  const submitRegistrationOnEnter = (event) => {
    if (event.key === "Enter") submitRegistration();
  };

  function init() {
    if (initialized) return;
    initialized = true;
    eventTarget?.addEventListener("storage", onAccountStorageChange);
    eventTarget?.addEventListener("pageshow", onPageResume);
    eventTarget?.addEventListener("focus", onPageResume);
    documentRef?.addEventListener?.("visibilitychange", onPageResume);
    loginSubmit?.addEventListener("click", submitLogin);
    localTokenSubmit?.addEventListener("click", submitLocalToken);
    localTokenInput?.addEventListener("keydown", submitLocalTokenOnEnter);
    loginInput?.addEventListener("keydown", focusLoginPassword);
    loginPassword?.addEventListener("keydown", submitLoginOnEnter);
    registerSubmit?.addEventListener("click", submitRegistration);
    registerInput?.addEventListener("keydown", focusRegisterPassword);
    registerPassword?.addEventListener("keydown", focusRegisterConfirm);
    registerConfirm?.addEventListener("keydown", submitRegistrationOnEnter);
    switchToRegister?.addEventListener("click", showRegister);
    createAccountButton?.addEventListener("click", showRegister);
    cancelAccountCreationButton?.addEventListener("click", hideModal);
    switchToLogin?.addEventListener("click", showLogin);
    editUserButton?.addEventListener("click", switchAccount);
    logoutButton?.addEventListener("click", logout);
    settingsLogoutButton?.addEventListener("click", logout);
    savePasswordButton?.addEventListener("click", savePassword);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    eventTarget?.removeEventListener("storage", onAccountStorageChange);
    eventTarget?.removeEventListener("pageshow", onPageResume);
    eventTarget?.removeEventListener("focus", onPageResume);
    documentRef?.removeEventListener?.("visibilitychange", onPageResume);
    cancelIdentityCheck();
    authRequestController?.abort();
    authRequestController = null;
    window.clearTimeout(passwordMessageTimer);
    loginSubmit?.removeEventListener("click", submitLogin);
    localTokenSubmit?.removeEventListener("click", submitLocalToken);
    localTokenInput?.removeEventListener("keydown", submitLocalTokenOnEnter);
    loginInput?.removeEventListener("keydown", focusLoginPassword);
    loginPassword?.removeEventListener("keydown", submitLoginOnEnter);
    registerSubmit?.removeEventListener("click", submitRegistration);
    registerInput?.removeEventListener("keydown", focusRegisterPassword);
    registerPassword?.removeEventListener("keydown", focusRegisterConfirm);
    registerConfirm?.removeEventListener("keydown", submitRegistrationOnEnter);
    switchToRegister?.removeEventListener("click", showRegister);
    createAccountButton?.removeEventListener("click", showRegister);
    cancelAccountCreationButton?.removeEventListener("click", hideModal);
    switchToLogin?.removeEventListener("click", showLogin);
    editUserButton?.removeEventListener("click", switchAccount);
    logoutButton?.removeEventListener("click", logout);
    settingsLogoutButton?.removeEventListener("click", logout);
    savePasswordButton?.removeEventListener("click", savePassword);
  }

  return {
    init,
    destroy,
    start,
    showLogin,
    showRegister,
    hideModal,
    login,
    loginLocal,
    register,
    logout,
    switchAccount,
    requireLocalAccess,
    savePassword,
    refreshAccess,
    renderUser,
    canWriteActiveSession,
    activeSessionBackendUserId,
  };
}
