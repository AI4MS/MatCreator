const USER_ID_KEY = "mat_userId";
const DISPLAY_NAME_KEY = "mat_displayName";
const DEPLOYMENT_MODE_KEY = "mat_deploymentMode";

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
  fetchImpl = globalThis.fetch,
  storage = globalThis.localStorage,
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
  } = elements;
  let initialized = false;
  let startupPromise = null;
  let authRequestController = null;
  let passwordMessageTimer = null;

  function showLogin() {
    if (state.deploymentMode !== "server") {
      hideModal();
      return;
    }
    loginModal?.classList.remove("hidden");
    loginView?.classList.remove("hidden");
    registerView?.classList.add("hidden");
    if (loginInput) loginInput.value = state.displayName || "";
    if (loginPassword) loginPassword.value = "";
    if (loginError) loginError.textContent = "";
    if (loginUuidDisplay) loginUuidDisplay.textContent = state.userId ? `UUID: ${state.userId}` : "";
    const registerLink = switchToRegister?.parentElement;
    if (registerLink) registerLink.style.display = state.userId ? "none" : "";
    loginInput?.focus();
  }

  function showRegister() {
    loginModal?.classList.remove("hidden");
    loginView?.classList.add("hidden");
    registerView?.classList.remove("hidden");
    if (registerInput) registerInput.value = "";
    if (registerPassword) registerPassword.value = "";
    if (registerConfirm) registerConfirm.value = "";
    if (registerError) registerError.textContent = "";
    registerInput?.focus();
  }

  function hideModal() {
    loginModal?.classList.add("hidden");
  }

  function renderUser() {
    if (!userDisplay) return;
    const label = state.displayName || state.userId;
    userDisplay.textContent = state.isAdmin ? `${label} (admin)` : label;
  }

  function canWriteActiveSession() {
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
    stored.remove(USER_ID_KEY);
    stored.remove(DISPLAY_NAME_KEY);
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
    stored.set(USER_ID_KEY, "user");
    stored.set(DISPLAY_NAME_KEY, "user");
  }

  function hideLocalAuthControls() {
    [editUserButton, logoutButton, settingsLogoutButton, passwordSection].forEach((element) => {
      if (element) element.style.display = "none";
    });
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

  function login(displayName, password = null) {
    return authenticate(
      "/api/auth/login",
      { display_name: displayName, password },
      loginError,
      "Login failed",
    );
  }

  function register(displayName, password, confirm) {
    if (registerError) registerError.textContent = "";
    if (password !== confirm) {
      if (registerError) registerError.textContent = "Passwords do not match.";
      return Promise.resolve(false);
    }
    return authenticate(
      "/api/auth/register",
      { display_name: displayName, password },
      registerError,
      "Registration failed",
    );
  }

  async function logout() {
    authRequestController?.abort();
    const userId = state.userId;
    if (state.deploymentMode === "server" && userId) {
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
    state.userId = "";
    state.displayName = "";
    state.activeSessionUserId = "";
    state.isAdmin = false;
    state.sessionReady = false;
    clearStoredIdentity();
    stored.remove(DEPLOYMENT_MODE_KEY);
    if (userDisplay) userDisplay.textContent = "—";
    onLoggedOut();
    showLogin();
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
    try {
      const response = await fetchImpl("/api/auth/set-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: state.userId,
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

  async function runStartup() {
    const controller = new AbortController();
    authRequestController = controller;
    const health = await getStartupHealth(controller.signal);
    if (controller.signal.aborted) return;
    state.deploymentMode = health?.mode === "server" ? "server" : "local";
    const storedMode = stored.get(DEPLOYMENT_MODE_KEY);
    const storedId = stored.get(USER_ID_KEY);
    const storedSessionId = stored.get(sessionStorageKeys.sessionId);
    const storedSessionOwner = stored.get(sessionStorageKeys.owner);

    if (state.deploymentMode === "local") {
      hideLocalAuthControls();
      applyLocalIdentity(storedMode === "server" || (storedId && storedId !== "user"));
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
      const loading = document.createElement("li");
      loading.className = "empty";
      loading.textContent = "Loading saved sessions…";
      sessionListElement.appendChild(loading);
    }
    const sessions = await loadSessions({ retries: 7 });
    if (controller.signal.aborted) return;
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
    loginSubmit?.addEventListener("click", submitLogin);
    loginInput?.addEventListener("keydown", focusLoginPassword);
    loginPassword?.addEventListener("keydown", submitLoginOnEnter);
    registerSubmit?.addEventListener("click", submitRegistration);
    registerInput?.addEventListener("keydown", focusRegisterPassword);
    registerPassword?.addEventListener("keydown", focusRegisterConfirm);
    registerConfirm?.addEventListener("keydown", submitRegistrationOnEnter);
    switchToRegister?.addEventListener("click", showRegister);
    switchToLogin?.addEventListener("click", showLogin);
    editUserButton?.addEventListener("click", showLogin);
    logoutButton?.addEventListener("click", logout);
    settingsLogoutButton?.addEventListener("click", logout);
    savePasswordButton?.addEventListener("click", savePassword);
  }

  function destroy() {
    if (!initialized) return;
    initialized = false;
    authRequestController?.abort();
    authRequestController = null;
    window.clearTimeout(passwordMessageTimer);
    loginSubmit?.removeEventListener("click", submitLogin);
    loginInput?.removeEventListener("keydown", focusLoginPassword);
    loginPassword?.removeEventListener("keydown", submitLoginOnEnter);
    registerSubmit?.removeEventListener("click", submitRegistration);
    registerInput?.removeEventListener("keydown", focusRegisterPassword);
    registerPassword?.removeEventListener("keydown", focusRegisterConfirm);
    registerConfirm?.removeEventListener("keydown", submitRegistrationOnEnter);
    switchToRegister?.removeEventListener("click", showRegister);
    switchToLogin?.removeEventListener("click", showLogin);
    editUserButton?.removeEventListener("click", showLogin);
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
    register,
    logout,
    savePassword,
    refreshAccess,
    renderUser,
    canWriteActiveSession,
    activeSessionBackendUserId,
  };
}
