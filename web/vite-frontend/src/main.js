import { createChatRenderer } from "./features/chat/rendering.js";
import { createMessageStreamController } from "./features/chat/messageStream.js";
import { createSmoothTextReveal } from "./features/chat/textReveal.js";
import { createLayoutController } from "./features/layout/resizers.js";
import { createImageLightbox } from "./features/media/imageLightbox.js";
import { classifyPath, createSessionFileTree } from "./features/session/fileTree.js";
import { createTimelineArtifactRenderer } from "./features/chat/timelineArtifacts.js";
import { createSessionListController } from "./features/session/sessionList.js";
import { createSessionDetailsController } from "./features/session/sessionDetails.js";
import { createSessionRuntime } from "./features/session/runtime.js";
import {
  findConversationRequest,
  finishRequestCleanup,
  requestHasActiveRun,
  requestPresentsLiveTurn,
} from "./features/session/requestLifecycle.js";
import { createWorkspaceTerminalController } from "./features/workspace/terminal.js";
import { AgentGraphView, StepExecutionFeed } from "./features/graphs/AgentGraphView.js";
import { ExecutionPlanView } from "./features/graphs/ExecutionPlanView.js";
import { createSkillGraphController } from "./features/skills/SkillGraphController.js";
import { createSettingsController } from "./features/settings/SettingsController.js";
import { createEvaluationController } from "./features/evaluation/EvaluationController.js";
import { createRemoteJobsController } from "./features/remoteJobs/RemoteJobsController.js";
import { mountOrbitalAgentIndicator } from "./components/mountOrbitalAgentIndicator.js";
import { createDisclosureController } from "./features/ui/disclosureState.js";
import {
  activityToolCalls,
  executorNodeId,
  formatAgentDuration,
  formatToolDuration,
  getFunctionResponse,
  getPlotPaths,
  getStructurePaths,
  isExecutorLauncherTool,
  normalizeAgentTimestamp,
  timelineSegments,
  toolStatusIcon,
} from "./features/chat/timelinePresentation.js";
import "./styles/index.css";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const APP_NAME = "MatCreator";

const AGENT_MODE_KEY = "mat_agentMode";
const SESSION_ID_KEY = "mat_sessionId";
const SESSION_OWNER_KEY = "mat_sessionOwnerId";
const THEME_KEY = "mat_theme";
const FONT_SCALE_KEY = "mat_fontScale";
const FONT_SCALE_PRESETS = [90, 100, 110, 125, 150];

function getFontScale() {
  const stored = Number(localStorage.getItem(FONT_SCALE_KEY));
  if (stored > 150) return 150;
  return FONT_SCALE_PRESETS.includes(stored) ? stored : 100;
}

function applyFontScale(scale, { persist = true } = {}) {
  const nextScale = FONT_SCALE_PRESETS.includes(Number(scale)) ? Number(scale) : 100;
  document.documentElement.style.setProperty("--font-scale", `${nextScale}%`);
  if (persist) localStorage.setItem(FONT_SCALE_KEY, String(nextScale));
  return nextScale;
}

function removeOverlayWithMotion(overlay) {
  if (!overlay?.isConnected) return Promise.resolve();
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
    overlay.remove();
    return Promise.resolve();
  }
  if (overlay.classList.contains("is-closing")) {
    return overlay._motionRemoval || Promise.resolve();
  }

  overlay.classList.add("is-closing");
  overlay._motionRemoval = new Promise((resolve) => {
    let fallbackTimer;
    const onAnimationEnd = (event) => {
      if (event.target === overlay) finish();
    };
    const finish = () => {
      clearTimeout(fallbackTimer);
      overlay.removeEventListener("animationend", onAnimationEnd);
      overlay.remove();
      resolve();
    };
    overlay.addEventListener("animationend", onAnimationEnd);
    fallbackTimer = window.setTimeout(finish, 250);
  });
  return overlay._motionRemoval;
}

const state = {
  sessionId: localStorage.getItem(SESSION_ID_KEY) || newSessionId(),
  userId: localStorage.getItem("mat_userId") || "",
  displayName: localStorage.getItem("mat_displayName") || localStorage.getItem("mat_userId") || "",
  activeSessionUserId: localStorage.getItem("mat_userId") || "",
  isAdmin: false,
  deploymentMode: localStorage.getItem("mat_deploymentMode") || "local",
  sessionReady: false,
  sessionStatusFilter: "all",
  structure3dViewer: null,
  activeCenterTabId: "chat",
  currentUploads: [],
  activeRequests: new Map(),
  sessionViewCache: new Map(),
  agentMode: localStorage.getItem(AGENT_MODE_KEY) || "normal",
  theme: localStorage.getItem(THEME_KEY) || "dark",
  customWorkdir: "",
  sessionSummaries: {},   // { sessionId: "summary text" }
  summaryGeneratedFor: new Set(),  // sessionIds that have triggered summary generation
  remoteJobs: [],
  appMode: "workspace",
  evaluationCatalog: [],
  evaluationCatalogTotal: null,
  evaluationQuestionSets: [],
  evaluationGeneratedQuestions: [],
  evaluationQuestionTemplates: [],
  evaluationQuestionGenerators: [],
  activeEvaluationQuestionTemplateId: "default",
  activeEvaluationQuestionGeneratorId: "",
  activeEvaluationQuestionSetId: "",
  selectedEvaluationQuestions: new Set(),
  activeEvaluationCampaign: null,
};

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const chatArea = document.getElementById("chat-area");
const textInput = document.getElementById("text-input");
const inputArea = document.querySelector(".input-area");
const inputContainer = document.querySelector(".input-container");
const agentRunningIndicator = document.getElementById("agent-running-indicator");
const agentRunningOrbital = document.getElementById("agent-running-orbital");
const agentRunningText = document.getElementById("agent-running-text");
const sendBtn = document.getElementById("send-btn");
const fileUploadBtn = document.getElementById("file-upload-btn");
const fileUploadInput = document.getElementById("file-upload-input");
const uploadStatus = document.getElementById("upload-status");
const sessionIdEl = document.getElementById("session-id");
const sessionListEl = document.getElementById("session-list");
const resetBtn = document.getElementById("reset-session");
const workspaceCliToggle = document.getElementById("workspace-cli-toggle");
const skillGraphOpenBtn = document.getElementById("skill-graph-open");
const themeToggle = document.getElementById("theme-toggle");
const refreshSessionsBtn = document.getElementById("refresh-sessions");
const sessionStatusFilter = document.getElementById("session-status-filter");
const graphViewport = document.getElementById("graph-viewport");
const graphDetail = document.getElementById("graph-detail");
const centerTabs = document.getElementById("center-tabs");
const centerTabsScroll = document.getElementById("center-tabs-scroll");
const centerTabPanels = document.getElementById("center-tab-panels");
const loginModal = document.getElementById("login-modal");
const loginInput = document.getElementById("login-input");
const loginPassword = document.getElementById("login-password");
const loginError = document.getElementById("login-error");
const loginUuidDisplay = document.getElementById("login-uuid-display");
const loginSubmit = document.getElementById("login-submit");
const loginView = document.getElementById("login-view");
const registerView = document.getElementById("register-view");
const regInput = document.getElementById("reg-input");
const regPassword = document.getElementById("reg-password");
const regConfirm = document.getElementById("reg-confirm");
const regError = document.getElementById("reg-error");
const regSubmit = document.getElementById("reg-submit");
const switchToRegister = document.getElementById("switch-to-register");
const switchToLogin = document.getElementById("switch-to-login");
const userDisplay = document.getElementById("user-display");
const editUserBtn = document.getElementById("edit-user");
const logoutBtn = document.getElementById("logout-btn");
const settingsLogoutBtn = document.getElementById("settings-logout-btn");
const benchToggle = null; // removed — replaced by mode-selector
const modeSelector = document.getElementById("mode-selector");
const modeTrigger = document.getElementById("mode-trigger");
const modeMenu = document.getElementById("mode-menu");
const sessionSummaryText = document.getElementById("session-summary-text");
const chatTab = document.getElementById("tab-chat");
const filesColToggleBtn = document.getElementById("files-col-toggle");
const knowledgeReviewBanner = document.createElement("button");
knowledgeReviewBanner.className = "knowledge-review-banner status-idle";
knowledgeReviewBanner.id = "knowledge-review-banner";
knowledgeReviewBanner.type = "button";
knowledgeReviewBanner.setAttribute("aria-live", "polite");
knowledgeReviewBanner.title = "Click to review memory and graph nodes";
const knowledgeReviewSpinner = document.createElement("span");
knowledgeReviewSpinner.className = "knowledge-review-spinner hidden";
knowledgeReviewSpinner.id = "knowledge-review-spinner";
const knowledgeReviewText = document.createElement("span");
knowledgeReviewText.id = "knowledge-review-text";
knowledgeReviewText.textContent = "Review Know-Do Graph";
knowledgeReviewBanner.append(knowledgeReviewSpinner, knowledgeReviewText);
const workspaceCli = document.getElementById("workspace-cli");
const workspaceTerminalEl = document.getElementById("workspace-terminal");
let knowledgeReviewPoll = null;
const structureTabs = new Map();
let structureViewerModulePromise = null;
let svelteRuntimePromise = null;

const {
  addMessage,
  appendLiveTurnChild,
  applyUserAvatarToEl,
  createAgentAvatarEl,
  markReadingAnchors,
  protectAsyncContentLayout,
  renderMarkdown,
  scrollToBottom,
  setMarkdownContent,
  setUserAvatar,
  updatePreservingReadingPosition,
} = createChatRenderer({ chatArea, bottomOverlay: inputArea });

const createChatDisclosureController = () => createDisclosureController();
const chatDisclosureController = createChatDisclosureController();

const settingsController = createSettingsController({
  state,
  applyLogin,
  getFontScale,
  applyFontScale,
});

const skillGraphController = createSkillGraphController({
  state,
  centerTabs,
  centerTabPanels,
  activateCenterTab,
  renderMarkdown,
  knowledgeReviewBanner,
});

const { render: renderSessionFilesTree } = createSessionFileTree({
  getSessionId: () => state.sessionId,
  pathToApiUrl: (path) => pathToApiUrl(path),
  openStructure: (item) => openViewer(item),
  openFile: (file) => openFileViewer(file),
});

// Graph and timeline views share these artifact renderers. Initialize them
// before either view is constructed; the callbacks defer access to the
// viewer and lightbox until a user actually opens an artifact.
const { createArtifactListItem, createStructureViewButtonGroup, createTimelineImage } = createTimelineArtifactRenderer({
  pathToApiUrl,
  openStructure: (item) => openViewer(item),
  openLightbox: (url) => lightbox.open(url),
  updatePreservingReadingPosition,
});

function loadStructureViewerModules() {
  structureViewerModulePromise ||= import("./structure/StructureViewer.svelte");
  svelteRuntimePromise ||= import("svelte");
  return Promise.all([structureViewerModulePromise, svelteRuntimePromise]);
}

const scheduleStructureViewerPreload = window.requestIdleCallback
  ? (callback) => window.requestIdleCallback(callback, { timeout: 2000 })
  : (callback) => window.setTimeout(callback, 1000);
scheduleStructureViewerPreload(() => void loadStructureViewerModules());

function sessionTabTooltip(title) {
  return `${title || "Chat"}\nDouble-click to edit session name`;
}

function autoResizeTextInput() {
  if (!textInput) return;
  textInput.style.height = "auto";
  const computed = window.getComputedStyle(textInput);
  const lineHeight = parseFloat(computed.lineHeight) || 24;
  const maxHeight = lineHeight * 3;
  const nextHeight = Math.min(textInput.scrollHeight, maxHeight);
  textInput.style.height = `${nextHeight}px`;
  textInput.style.overflowY = textInput.scrollHeight > maxHeight ? "auto" : "hidden";
}

autoResizeTextInput();
textInput?.addEventListener("input", autoResizeTextInput);

function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  state.theme = nextTheme;
  document.body.dataset.theme = nextTheme;
  window.dispatchEvent(new CustomEvent("matcreator-theme-change", { detail: nextTheme }));
  themeToggle?.setAttribute("aria-pressed", String(nextTheme === "light"));
  themeToggle?.setAttribute("title", nextTheme === "light" ? "Toggle dark mode" : "Toggle light mode");
  themeToggle?.setAttribute("aria-label", nextTheme === "light" ? "Toggle dark mode" : "Toggle light mode");
}

applyTheme(state.theme);
applyFontScale(getFontScale());
themeToggle?.addEventListener("click", () => {
  const nextTheme = state.theme === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, nextTheme);
  applyTheme(nextTheme);
});

sessionIdEl.textContent = state.sessionId;
if (state.userId) userDisplay.textContent = state.displayName || state.userId;
refreshKnowledgeReviewStatus();

const evaluationController = createEvaluationController({
  state,
  activateCenterTab,
  switchSession,
  removeOverlayWithMotion,
});

const sessionDetailsController = createSessionDetailsController({
  getStatus: sessionDisplayStatus,
});

// ---------------------------------------------------------------------------
// Agent Graph Visualization
// ---------------------------------------------------------------------------

const stepExecutionFeed = new StepExecutionFeed({
  chatArea,
  isSending: () => requestHasActiveRun(activeSessionRequest()),
  updatePreservingReadingPosition,
  createAgentAvatarEl,
  stepFeedTitle,
  formatStepDuration,
  renderStepInput,
  renderStepConversationEvent,
  renderStepToolCall,
  requestStepCancellation,
  createArtifactListItem,
  disclosureController: chatDisclosureController,
});
const agentGraph = new AgentGraphView("agent-graph", {
  stepExecutionFeed,
  graphViewport,
  requestStepCancellation,
  createArtifactListItem,
  renderStepConversationEvent,
  renderStepToolCall,
  syncPanelResizerVisibility: () => layoutController.syncPanelResizerVisibility(),
});
const planGraph = new ExecutionPlanView("plan-graph-canvas", {
  toggleButton: document.getElementById("plan-graph-toggle"),
  thumbnailElement: document.getElementById("plan-graph-thumbnail"),
});
const layoutController = createLayoutController({
  getUserId: () => state.userId,
  onLayoutChanged: () => agentGraph.notifyLayoutChanged(),
  elements: {
    graphResizer: document.getElementById("graph-resizer"),
    graphColumn: document.getElementById("graph-column"),
    sidePanel: document.getElementById("side-panel"),
    fileExplorerCol: document.getElementById("file-explorer-col"),
    colResizerGraph: document.getElementById("col-resizer-graph"),
    colResizerSide: document.getElementById("col-resizer-side"),
    colResizerFiles: document.getElementById("col-resizer-files"),
  },
});

async function requestStepCancellation(stepNumber) {
  if (stepNumber === undefined || stepNumber === null) return false;
  try {
    const query = new URLSearchParams({
      user_id: state.activeSessionUserId || state.userId,
    });
    const resp = await fetch(
      `/api/sessions/${state.sessionId}/cancel-step/${stepNumber}?${query}`,
      { method: "POST" },
    );
    return resp.ok;
  } catch (_) {
    return false;
  }
}

function shouldRefreshPlanGraphForTool(toolName) {
  return toolName === "validate_graph" || toolName === "validate_plan";
}

function newSessionId() {
  const randomPart = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  return `session-${Date.now()}-${randomPart}`;
}

function sessionRequestKey(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
  return `${owner || "user"}:${sessionId || ""}`;
}

function activeSessionRequest() {
  return findConversationRequest(state.activeRequests, {
    key: sessionRequestKey(),
    sessionId: state.sessionId,
    owner: state.activeSessionUserId || state.userId,
  });
}

function releaseSessionRequest(request) {
  if (!request) return;
  const current = state.activeRequests.get(request.key);
  if (current === request) {
    state.activeRequests.delete(request.key);
  }
  finishRequestCleanup(request);
  if (request.key === sessionRequestKey()) {
    updateSendButtonState();
    rerenderSessionList?.();
  }
}

const orbitalIndicator = mountOrbitalAgentIndicator(agentRunningOrbital);

function attachAgentRunningIndicator(messageView) {
  const message = messageView?.element
    || messageView?.closest?.(".agent-message:not(.step-feed-message)");
  if (!agentRunningIndicator) return;
  if (!message) {
    agentRunningIndicator.setAttribute("aria-hidden", "true");
    return;
  }
  const meta = message.querySelector(".agent-bubble-meta");
  (meta || message).prepend(agentRunningIndicator);
  // Before the first streamed item, make the agent's avatar and status row
  // visible without rendering an empty message bubble.
  const timelineElement = messageView?.timelineElement || messageView;
  const hasPresentedContent = timelineElement?.querySelector?.(
    ":scope > .timeline-segment, .delegation-group .step-feed-message",
  );
  if (!hasPresentedContent) message.classList.add("is-waiting");
  message.classList.remove("is-pending");
}

function ensureAgentRunningIndicatorAttached() {
  if (!agentRunningIndicator || agentRunningIndicator.isConnected) return;
  const message = [...chatArea.querySelectorAll(".agent-message:not(.step-feed-message)")].at(-1);
  if (!message) {
    agentRunningIndicator.setAttribute("aria-hidden", "true");
    return;
  }
  const meta = message?.querySelector(".agent-bubble-meta");
  if (meta || message) (meta || message).prepend(agentRunningIndicator);
}

function updateAgentRunningStatus(phase = "working") {
  const phases = {
    connecting: ["Connecting to MatCreator…", "thinking"],
    connected: ["Connected — MatCreator is working…", "thinking"],
    saving: ["Saving the response…", "thinking"],
    working: ["MatCreator is working. Please wait…", "thinking"],
    thinking: ["MatCreator is thinking…", "thinking"],
    planning: ["MatCreator is planning the workflow…", "thinking"],
    finalizing_plan: ["Plan validated — preparing it for review…", "thinking"],
    searching: ["MatCreator is searching for information…", "searching"],
    executing: ["MatCreator is executing the workflow…", "computing"],
    computing: ["MatCreator is computing…", "computing"],
  };
  const [label, orbitalState] = phases[phase] || phases.working;
  if (agentRunningText) agentRunningText.textContent = label;
  orbitalIndicator?.render(orbitalState);
}

function updateSendButtonState() {
  const request = activeSessionRequest();
  const running = requestHasActiveRun(request);
  // The text presentation can finish before the backend completes its durable
  // bookkeeping. Keep the request lock, but do not present that gap as an
  // active agent response.
  // Presentation can intentionally outlive the backend run while durable
  // history is being reconciled. In particular, an empty recovered turn must
  // keep its waiting status until there is something safe to replace it.
  const presenting = requestPresentsLiveTurn(request);
  const activeAgentMessage = [...chatArea.querySelectorAll(".agent-message:not(.step-feed-message)")].at(-1);
  if (presenting && activeAgentMessage) ensureAgentRunningIndicatorAttached();
  const indicatorVisible = Boolean(presenting && agentRunningIndicator?.closest(".agent-message:not(.step-feed-message)"));
  if (agentRunningIndicator) agentRunningIndicator.setAttribute("aria-hidden", String(!indicatorVisible));
  if (!running) updateAgentRunningStatus();
  if (!sendBtn) return;
  const queued = Boolean(request?.followupQueued);
  sendBtn.disabled = queued;
  sendBtn.textContent = queued ? "…" : running ? "■" : "➜";
  sendBtn.title = queued ? "Sending after message sync…" : running ? "Stop" : "Send";
  sendBtn.setAttribute("aria-label", sendBtn.title);
  sendBtn.classList.toggle("is-stopping", running);
  sendBtn.classList.toggle("is-finalizing", queued);
}

function storeSessionSelection(sessionId, owner) {
  localStorage.setItem(SESSION_ID_KEY, sessionId);
  localStorage.setItem(SESSION_OWNER_KEY, owner);
}

function clearStoredSessionSelection() {
  localStorage.removeItem(SESSION_ID_KEY);
  localStorage.removeItem(SESSION_OWNER_KEY);
}

function validatedStoredSession(sessions, sessionId, storedOwner) {
  if (!sessionId || !Array.isArray(sessions)) return null;
  let owner = state.userId;
  if (state.deploymentMode === "server" && state.isAdmin) {
    if (!storedOwner) return null;
    owner = storedOwner;
  } else if (state.deploymentMode === "server" && storedOwner && storedOwner !== state.userId) {
    return null;
  }
  const found = sessions.find((session) => (
    session.id === sessionId && (session.userId || state.userId) === owner
  ));
  return found ? {
    sessionId,
    owner,
    knownRunning: String(found.status || found.phase || "").toLowerCase() === "running",
  } : null;
}

function managedRunEventsUrl(request) {
  return `/api/runs/${request.runId}/events` + `?after=${request.lastSequence}`;
}

// ---------------------------------------------------------------------------
// Plan graph popup toggle
// ---------------------------------------------------------------------------

const planGraphPopup = document.getElementById("plan-graph-popup");
const planGraphToggleBtn = document.getElementById("plan-graph-toggle");
const planGraphThumbnailEl = document.getElementById("plan-graph-thumbnail");
const planGraphCloseBtn = document.getElementById("plan-graph-close");

function showPlanGraph() {
  planGraphPopup?.classList.remove("hidden");
  planGraphToggleBtn?.classList.add("is-open");
  planGraphToggleBtn?.setAttribute("aria-pressed", "true");
  planGraphToggleBtn?.setAttribute("title", "Close roadmap");
  planGraphToggleBtn?.setAttribute("aria-label", "Close roadmap");
  // vis-network calculates its camera from the canvas dimensions. Because the
  // popup was `display: none`, wait for one layout frame to expose it and a
  // second frame for flex sizing to settle before fitting the initial view.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    planGraph.notifyLayoutChanged();
    planGraph.fitToView({ animate: false });
  }));
}

function hidePlanGraph() {
  planGraphPopup?.classList.add("hidden");
  planGraphToggleBtn?.classList.remove("is-open");
  planGraphToggleBtn?.setAttribute("aria-pressed", "false");
  planGraphToggleBtn?.setAttribute("title", "Open roadmap");
  planGraphToggleBtn?.setAttribute("aria-label", "Open roadmap");
}

planGraphToggleBtn?.addEventListener("click", () => {
  if (planGraphPopup?.classList.contains("hidden")) {
    showPlanGraph();
  } else {
    hidePlanGraph();
  }
});

planGraphCloseBtn?.addEventListener("click", hidePlanGraph);
document.getElementById("plan-graph-zoom-in")?.addEventListener("click", () => planGraph.zoomIn());
document.getElementById("plan-graph-zoom-out")?.addEventListener("click", () => planGraph.zoomOut());
document.getElementById("plan-graph-fit")?.addEventListener("click", () => planGraph.fitToView());
document.getElementById("plan-graph-prev")?.addEventListener("click", () => planGraph.goPrev());
document.getElementById("plan-graph-next")?.addEventListener("click", () => planGraph.goNext());
// ---------------------------------------------------------------------------

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

// ---------------------------------------------------------------------------
// Login / username management
// ---------------------------------------------------------------------------

function _isUuid(s) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
}

function _isValidIdentity(s) {
  return s === "user" || _isUuid(s);
}

function showLoginModal() {
  if (state.deploymentMode !== "server") {
    hideLoginModal();
    return;
  }
  loginModal.classList.remove("hidden");
  loginView.classList.remove("hidden");
  registerView.classList.add("hidden");
  loginInput.value = state.displayName || "";
  loginPassword.value = "";
  loginError.textContent = "";
  loginUuidDisplay.textContent = state.userId ? `UUID: ${state.userId}` : "";
  // Hide register link when already logged in — log out first to register a new account.
  const registerLink = document.getElementById("switch-to-register")?.parentElement;
  if (registerLink) registerLink.style.display = state.userId ? "none" : "";
  loginInput.focus();
}

async function logout() {
  const userId = state.userId;
  const deploymentMode = state.deploymentMode;
  if (deploymentMode === "server" && userId) {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
    } catch (_) { /* best-effort worker shutdown */ }
  }
  state.userId = "";
  state.displayName = "";
  state.activeSessionUserId = "";
  state.isAdmin = false;
  state.sessionReady = false;
  localStorage.removeItem("mat_userId");
  localStorage.removeItem("mat_displayName");
  clearStoredSessionSelection();
  localStorage.removeItem("mat_deploymentMode");
  userDisplay.textContent = "—";
  sessionRuntime.resetTranscript();
  stepExecutionFeed.reset();
  chatDisclosureController.clear();
  state.sessionViewCache.clear();
  sessionListEl.innerHTML = '<li class="empty">Sign in to see sessions</li>';
  renderSessionFilesTree([]);
  clearCurrentUploads();
  remoteJobsController.reset();
  agentGraph.reset();
  planGraph.reset();
  hidePlanGraph();
  settingsController.close();
  showLoginModal();
}

function showRegisterModal() {
  loginModal.classList.remove("hidden");
  loginView.classList.add("hidden");
  registerView.classList.remove("hidden");
  regInput.value = "";
  regPassword.value = "";
  regConfirm.value = "";
  regError.textContent = "";
  regInput.focus();
}

function hideLoginModal() {
  loginModal.classList.add("hidden");
}

function renderUserDisplay() {
  const label = state.displayName || state.userId;
  userDisplay.textContent = state.isAdmin ? `${label} (admin)` : label;
}

function canWriteActiveSession() {
  return state.deploymentMode === "local" || !state.activeSessionUserId || state.activeSessionUserId === state.userId;
}

function activeSessionBackendUserId() {
  return state.deploymentMode === "local"
    ? (state.activeSessionUserId || state.userId)
    : state.userId;
}

async function refreshAccess() {
  state.isAdmin = false;
  if (!state.userId) return;
  try {
    const resp = await fetch(`/api/session-access/${encodeURIComponent(state.userId)}`);
    if (!resp.ok) return;
    const access = await resp.json();
    state.isAdmin = Boolean(access.is_admin);
  } catch (_) {
    state.isAdmin = false;
  }
}

function _applySession(result) {
  state.userId = result.user_id;
  state.displayName = result.display_name;
  state.activeSessionUserId = result.user_id;
  state.sessionId = newSessionId();
  state.sessionReady = false;
  updateSendButtonState();
  state.isAdmin = Boolean(result.is_admin);
  loginUuidDisplay.textContent = `UUID: ${result.user_id}`;
  localStorage.setItem("mat_deploymentMode", state.deploymentMode);
  localStorage.setItem("mat_userId", result.user_id);
  localStorage.setItem("mat_displayName", result.display_name);
  clearStoredSessionSelection();
  sessionIdEl.textContent = state.sessionId;
  sessionRuntime.resetTranscript();
  stepExecutionFeed.reset();
  renderSessionFilesTree([]);
  clearCurrentUploads();
  remoteJobsController.reset();
  agentGraph.reset();
  planGraph.reset();
  hidePlanGraph();
  renderUserDisplay();
  hideLoginModal();
  layoutController.refresh();
  loadSessions();
}

async function applyLogin(displayName, password = null) {
  loginError.textContent = "";
  let result;
  try {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName, password }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      loginError.textContent = body.detail || `Login failed (${resp.status})`;
      return;
    }
    result = body;
  } catch (err) {
    loginError.textContent = `Login failed: ${err.message}`;
    return;
  }
  _applySession(result);
}

async function applyRegister(displayName, password, confirm) {
  regError.textContent = "";
  if (password !== confirm) {
    regError.textContent = "Passwords do not match.";
    return;
  }
  let result;
  try {
    const resp = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName, password }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      regError.textContent = body.detail || `Registration failed (${resp.status})`;
      return;
    }
    result = body;
  } catch (err) {
    regError.textContent = `Registration failed: ${err.message}`;
    return;
  }
  _applySession(result);
}

loginSubmit.addEventListener("click", () => {
  const name = loginInput.value.trim();
  if (name) applyLogin(name, loginPassword.value || null);
});

loginInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") loginPassword.focus();
});

loginPassword.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const name = loginInput.value.trim();
    if (name) applyLogin(name, loginPassword.value || null);
  }
});

regSubmit.addEventListener("click", () => {
  const name = regInput.value.trim();
  if (name) applyRegister(name, regPassword.value, regConfirm.value);
});

regInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") regPassword.focus();
});

regPassword.addEventListener("keydown", (e) => {
  if (e.key === "Enter") regConfirm.focus();
});

regConfirm.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const name = regInput.value.trim();
    if (name) applyRegister(name, regPassword.value, regConfirm.value);
  }
});

switchToRegister.addEventListener("click", () => showRegisterModal());
switchToLogin.addEventListener("click", () => showLoginModal());

editUserBtn.addEventListener("click", () => showLoginModal());
logoutBtn.addEventListener("click", () => logout());
settingsLogoutBtn.addEventListener("click", () => logout());

const savePasswordBtn = document.getElementById("settings-save-password-btn");
const passwordMsg = document.getElementById("settings-password-msg");
const settingsPasswordSection = savePasswordBtn?.parentElement;

async function savePassword() {
  const oldPw = document.getElementById("settings-current-password").value || null;
  const newPw = document.getElementById("settings-new-password").value;
  const confirmPw = document.getElementById("settings-confirm-password").value;
  passwordMsg.style.color = "#f87171";
  if (!newPw) { passwordMsg.textContent = "New password cannot be empty."; return; }
  if (newPw !== confirmPw) { passwordMsg.textContent = "Passwords do not match."; return; }
  try {
    const res = await fetch("/api/auth/set-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: state.userId, old_password: oldPw, new_password: newPw }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      passwordMsg.textContent = data.detail || "Failed to update password.";
      return;
    }
    passwordMsg.style.color = "#4ade80";
    passwordMsg.textContent = "Password updated.";
    document.getElementById("settings-current-password").value = "";
    document.getElementById("settings-new-password").value = "";
    document.getElementById("settings-confirm-password").value = "";
    setTimeout(() => { passwordMsg.textContent = ""; }, 3000);
  } catch (e) {
    passwordMsg.textContent = "Network error.";
  }
}

savePasswordBtn.addEventListener("click", savePassword);

function clearStoredIdentity() {
  localStorage.removeItem("mat_userId");
  localStorage.removeItem("mat_displayName");
  clearStoredSessionSelection();
}

function applyLocalIdentity(resetSession = false) {
  state.deploymentMode = "local";
  state.userId = "user";
  state.displayName = "user";
  state.activeSessionUserId = "user";
  state.isAdmin = false;
  state.sessionReady = false;
  if (resetSession) {
    state.sessionId = newSessionId();
    clearStoredSessionSelection();
  }
  localStorage.setItem("mat_deploymentMode", "local");
  localStorage.setItem("mat_userId", "user");
  localStorage.setItem("mat_displayName", "user");
}

function hideLocalAuthControls() {
  if (editUserBtn) editUserBtn.style.display = "none";
  if (logoutBtn) logoutBtn.style.display = "none";
  if (settingsLogoutBtn) settingsLogoutBtn.style.display = "none";
  if (settingsPasswordSection) settingsPasswordSection.style.display = "none";
}

async function getStartupHealth() {
  // The development server may render before its API proxy is listening. Do
  // not permanently select local mode or abandon the saved-session restore
  // because of that short startup race.
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      const response = await fetch("/api/health");
      if (response.ok) return response.json();
    } catch (_) {
      // Retry below.
    }
    if (attempt < 7) {
      await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
    }
  }
  return null;
}

// On load: in local mode force passwordless "user"; in server mode require server auth.
(async () => {
  const health = await getStartupHealth();
  const serverMode = health?.mode || "local";

  state.deploymentMode = serverMode === "server" ? "server" : "local";
  const storedMode = localStorage.getItem("mat_deploymentMode") || "";
  const storedId = localStorage.getItem("mat_userId") || "";
  const storedSessionId = localStorage.getItem(SESSION_ID_KEY) || "";
  const storedSessionOwner = localStorage.getItem(SESSION_OWNER_KEY) || "";

  if (state.deploymentMode === "local") {
    hideLocalAuthControls();
    applyLocalIdentity(storedMode === "server" || (storedId && storedId !== "user"));
    hideLoginModal();
  } else if ((storedMode && storedMode !== "server") || (!storedMode && storedId === "user")) {
    clearStoredIdentity();
    showLoginModal();
    return;
  } else if (!storedId) {
    showLoginModal();
    return;
  } else if (!_isValidIdentity(storedId)) {
    // Legacy: localStorage contains a raw display name (non-"user"). Show login modal.
    showLoginModal();
    return;
  }

  sessionIdEl.textContent = state.sessionId;
  await refreshAccess();
  renderUserDisplay();
  sessionListEl.innerHTML = '<li class="empty">Loading saved sessions…</li>';
  const sessions = await loadSessions({ retries: 7 });
  const storedSession = validatedStoredSession(sessions, storedSessionId, storedSessionOwner);
  if (storedSession) {
    await switchSession(storedSession.sessionId, storedSession.owner, {
      knownRunning: storedSession.knownRunning,
    });
  } else if (sessions && storedSessionId) {
    clearStoredSessionSelection();
    state.sessionId = newSessionId();
    state.activeSessionUserId = state.userId;
    state.sessionReady = false;
    sessionIdEl.textContent = state.sessionId;
    updateSendButtonState();
  }
})();

// ---------------------------------------------------------------------------
// Session list management
// ---------------------------------------------------------------------------

const { loadSessions, rerender: rerenderSessionList } = createSessionListController({
  state,
  sessionListEl,
  refreshButton: refreshSessionsBtn,
  filterElement: sessionStatusFilter,
  activeSessionRequest: (key) => state.activeRequests.get(key),
  sessionRequestKey,
  switchSession,
  deleteSession,
  downloadSessionLog,
  sessionDisplayStatus,
  showDraft: evaluationController.showSessionQuestionGeneratorPicker,
  showSessionDetails: (session, owner) => sessionDetailsController.open(session, owner),
});

const remoteJobsController = createRemoteJobsController({
  state,
  dummyMode: import.meta.env.VITE_DUMMY_REMOTE_JOBS === "true",
  onJobsChanged: rerenderSessionList,
});

function sessionDisplayStatus(session, owner) {
  if (requestHasActiveRun(state.activeRequests.get(sessionRequestKey(session.id, owner)))) return "running";
  if (session.id === state.sessionId && owner === state.activeSessionUserId) {
    const statuses = state.remoteJobs.map((job) => job.status);
    if (statuses.includes("running") || statuses.includes("queued")) return "running";
  }
  const status = String(session.status || session.phase || "").toLowerCase();
  return ["running", "idle"].includes(status) ? status : "idle";
}

async function switchSession(sessionId, owner = state.userId, { knownRunning = false } = {}) {
  const viewKey = sessionRequestKey(sessionId, owner);
  // The active item remains clickable after the sidebar is rerendered.  It is
  // not a refresh control, so do not tear down and rebuild the current view
  // when the user clicks it again.
  if (state.sessionReady && viewKey === sessionRequestKey()) return;
  state.sessionId = sessionId;
  state.activeSessionUserId = owner;
  state.sessionReady = true;
  updateSendButtonState();
  storeSessionSelection(sessionId, owner);
  sessionIdEl.textContent = sessionId;
  const cachedView = state.sessionViewCache.get(viewKey);
  if (cachedView) renderSessionSnapshot(cachedView);
  else renderSessionFilesTree([]);
  clearCurrentUploads();
  remoteJobsController.reset();
  agentGraph.reset();
  planGraph.reset();
  hidePlanGraph();
  if (knownRunning) sessionRuntime.beginManagedRunDiscovery(sessionId, owner);
  remoteJobsController.startPolling(sessionId, owner);
  // History and active-run discovery are independent reads. Whichever returns
  // first may establish the initial view; the runtime's live-turn ownership
  // transaction reconciles them without allowing two assistant renderers.
  const reconnect = sessionRuntime.discoverManagedRun(sessionId, owner).then((activeRun) => {
    if (activeRun) sessionRuntime.startManagedRunReconnect(activeRun, sessionId, owner);
    else sessionRuntime.discardManagedRunDiscovery(sessionId, owner);
  });
  await Promise.all([
    reconnect,
    sessionRuntime.loadSession(sessionId, owner),
    remoteJobsController.load(sessionId, owner),
  ]);
  void loadSessions();
  agentGraph.startPolling(sessionId);
  planGraph.startPolling(sessionId);
}


// ---------------------------------------------------------------------------
// Confirm dialog & session delete
// ---------------------------------------------------------------------------

function showConfirmDialog(message) {
  const existing = document.querySelector(".confirm-overlay");
  if (existing) existing.remove();

  return new Promise((resolve) => {
    let settled = false;
    const done = (result) => {
      if (settled) return;
      settled = true;
      void removeOverlayWithMotion(overlay).then(() => resolve(result));
    };

    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const msg = document.createElement("p");
    msg.className = "confirm-message";
    msg.textContent = message;

    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "confirm-cancel";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => done(false);
    const okBtn = document.createElement("button");
    okBtn.className = "confirm-ok";
    okBtn.textContent = "Delete";
    okBtn.onclick = () => done(true);
    actions.append(cancelBtn, okBtn);

    const card = document.createElement("div");
    card.className = "confirm-card";
    card.append(msg, actions);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    overlay.addEventListener("click", (e) => { if (e.target === overlay) done(false); });
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") done(false); });
    okBtn.focus();
  });
}

async function deleteSession(sessionId) {
  if (activeSessionRequest()) return;
  if (!await showConfirmDialog(`Delete session ${sessionId}? This cannot be undone.`)) return;
  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    if (!resp.ok) return;
    if (sessionId === state.sessionId) {
      state.sessionId = newSessionId();
      state.activeSessionUserId = state.userId;
      state.sessionReady = false;
      updateSendButtonState();
      clearStoredSessionSelection();
      sessionIdEl.textContent = state.sessionId;
      sessionRuntime.resetTranscript();
      stepExecutionFeed.reset();
      renderSessionFilesTree([]);
      clearCurrentUploads();
      remoteJobsController.reset();
      agentGraph.reset();
      planGraph.reset();
      hidePlanGraph();
    }
    await loadSessions();
  } catch (_) {
    // silently ignore
  }
}

async function downloadSessionLog(sessionId, owner = state.userId) {
  if (!sessionId) return;
  const userQuery = owner || state.userId;
  const query = userQuery ? `?user_id=${encodeURIComponent(userQuery)}` : "";
  const url = `/api/sessions/${encodeURIComponent(sessionId)}/session-log${query}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      const msg = await resp.text().catch(() => "");
      throw new Error(msg || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `matcreator-session-log-${sessionId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (err) {
    console.warn("Failed to download session log", err);
  }
}


document.getElementById("refresh-files").addEventListener("click", (e) => { e.stopPropagation(); refreshSessionFiles(); });

// ---------------------------------------------------------------------------
// File path → API URL conversion
// ---------------------------------------------------------------------------

function pathToApiUrl(path) {
  const sid = state.sessionId ? `&session_id=${encodeURIComponent(state.sessionId)}` : "";
  return `/api/workspace/files?path=${encodeURIComponent(path)}${sid}`;
}

// ---------------------------------------------------------------------------
// Chat presentation
// ---------------------------------------------------------------------------

function createPayloadView(payload) {
  if (payload === null || payload === undefined) {
    const empty = document.createElement("span");
    empty.className = "payload-value payload-value-empty";
    empty.textContent = payload === null ? "null" : "Not available";
    return empty;
  }

  if (Array.isArray(payload)) {
    const list = document.createElement("div");
    list.className = "payload-list";
    payload.forEach((item) => {
      const row = document.createElement("div");
      row.className = "payload-list-item";
      row.appendChild(createPayloadView(item));
      list.appendChild(row);
    });
    if (!payload.length) list.textContent = "Empty list";
    return list;
  }

  if (typeof payload === "object") {
    const fields = document.createElement("div");
    fields.className = "payload-fields";
    Object.entries(payload).forEach(([key, value]) => {
      const row = document.createElement("div");
      row.className = "payload-field";
      const label = document.createElement("span");
      label.className = "payload-key";
      label.textContent = key;
      row.append(label, createPayloadView(value));
      fields.appendChild(row);
    });
    if (!fields.childElementCount) fields.textContent = "Empty object";
    return fields;
  }

  const value = document.createElement("span");
  value.className = `payload-value payload-value-${typeof payload}`;
  value.textContent = typeof payload === "string" ? payload : String(payload);
  return value;
}

function createPayloadBlock(payload, empty = "Not available") {
  const block = document.createElement("div");
  block.className = "payload-block";
  block.appendChild(createPayloadView(payload === undefined ? empty : payload));
  return block;
}

function createToolCallRawView(toolCall) {
  const body = document.createElement("div");
  body.className = "tool-call-raw";
  const addPayload = (label, payload, empty = "Not available") => {
    const section = document.createElement("section");
    const heading = document.createElement("div");
    heading.className = "tool-call-raw-label";
    heading.textContent = label;
    section.appendChild(heading);
    const deferred = payload?._matcreator_deferred_detail;
    if (deferred) {
      const preview = { ...payload };
      delete preview._matcreator_deferred_detail;
      if (Object.keys(preview).length) section.appendChild(createPayloadBlock(preview));
      const load = document.createElement("button");
      load.type = "button";
      load.className = "ghost tool-detail-load";
      load.textContent = `Load full output (${Math.ceil((deferred.byte_size || 0) / 1024)} KB)`;
      load.addEventListener("click", async () => {
        load.disabled = true;
        load.textContent = "Loading output…";
        try {
          const url = `/api/users/${encodeURIComponent(deferred.user_id)}`
            + `/sessions/${encodeURIComponent(deferred.session_id)}`
            + `/events/${deferred.row_id}/parts/${deferred.part_index}/detail`;
          const response = await fetch(url);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const detail = await response.json();
          section.replaceChildren(heading, createPayloadBlock(detail.response));
        } catch (error) {
          load.disabled = false;
          load.textContent = `Retry full output (${error.message})`;
        }
      });
      section.appendChild(load);
    } else {
      section.appendChild(createPayloadBlock(payload === null || payload === undefined ? empty : payload));
    }
    body.appendChild(section);
  };
  if (toolCall.error) addPayload("Error", toolCall.error);
  addPayload("Input", toolCall.input, "No input payload");
  addPayload("Output", toolCall.output, "Awaiting output");
  return body;
}

function createActionRawView(action) {
  const body = document.createElement("div");
  body.className = "activity-action-raw";
  action.toolCalls.forEach((call) => {
    const section = document.createElement("section");
    if (action.toolCalls.length > 1) {
      const heading = document.createElement("div");
      heading.className = "tool-call-raw-label";
      heading.textContent = call.name;
      section.appendChild(heading);
    }
    section.appendChild(createToolCallRawView(call));
    body.appendChild(section);
  });
  return body;
}

function createTimelineReasoning(entry, wireTimelineDetails, collapsed = false, isNew = false, cacheMarkdown = true) {
  if (collapsed) {
    const details = document.createElement("details");
    details.className = "agent-activity-reasoning-entry is-collapsed";
    const summary = document.createElement("summary");
    const preview = document.createElement("span");
    preview.className = "agent-activity-reasoning-preview";
    const reasoningText = String(entry.text || "");
    preview.textContent = reasoningText.replace(/\s+/g, " ").trim().slice(0, 240);
    const ellipsis = document.createElement("span");
    ellipsis.className = "agent-activity-reasoning-ellipsis";
    ellipsis.textContent = "…";
    const chevron = document.createElement("span");
    chevron.className = "agent-activity-reasoning-chevron";
    chevron.textContent = "›";
    summary.append(preview, ellipsis, chevron);
    details.append(summary);
    wireTimelineDetails(details, `${entry.timelineId}:reasoning`);
    let content = null;
    const mountContent = () => {
      if (content) return;
      content = document.createElement("div");
      content.className = "agent-activity-reasoning-content";
      const markdown = document.createElement("div");
      markdown.className = "markdown-content";
      setMarkdownContent(markdown, reasoningText, { cache: cacheMarkdown });
      const collapse = document.createElement("button");
      collapse.type = "button";
      collapse.className = "agent-activity-reasoning-collapse";
      collapse.textContent = "›";
      collapse.title = "Collapse reasoning";
      collapse.setAttribute("aria-label", "Collapse reasoning");
      collapse.addEventListener("click", () => { details.open = false; });
      content.append(markdown, collapse);
      details.appendChild(content);
    };
    const unmountContent = () => {
      content?.remove();
      content = null;
    };
    details.addEventListener("toggle", () => {
      if (details.open) mountContent();
      else unmountContent();
    });
    if (reasoningText.length <= 240) {
      details.classList.add("is-static");
      details.open = true;
      mountContent();
    } else if (details.open) mountContent();
    return details;
  }
  const section = document.createElement("div");
  section.className = `agent-activity-reasoning-entry${isNew ? " is-entering" : ""}`;
  const content = document.createElement("div");
  content.className = "markdown-content";
  // This is the live reasoning path, so keep it mounted while text streams.
  setMarkdownContent(content, entry.text || "", { cache: cacheMarkdown, defer: false });
  section.appendChild(content);
  return section;
}

function createActivityAction(action, wireTimelineDetails, { isNew = false, includeExecutorTools = false } = {}) {
  const toolCalls = includeExecutorTools ? (action.toolCalls || []) : activityToolCalls(action);
  if (!toolCalls.length) return null;
  const displayAction = {
    ...action,
    toolCalls,
    status: toolCalls.some((call) => call.status === "failed") ? "failed"
      : toolCalls.some((call) => call.status === "running") ? "running" : "success",
    durationMs: toolCalls.reduce((total, call) => total + (call.durationMs || 0), 0) || null,
  };
  const details = document.createElement("details");
  details.className = `agent-activity-action is-${displayAction.status}${isNew ? " is-entering" : ""}`;
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "agent-activity-status";
  icon.textContent = toolStatusIcon(displayAction);
  const text = document.createElement("span");
  text.className = "activity-action-heading";
  const title = document.createElement("span");
  title.className = "activity-action-title";
  title.textContent = displayAction.toolCalls.map((call) => call.name).join(" · ");
  text.appendChild(title);
  const duration = document.createElement("span");
  duration.className = "agent-activity-duration";
  duration.textContent = formatToolDuration(displayAction);
  summary.append(icon, text, duration);
  details.appendChild(summary);

  let body = null;
  let unmountTimer = null;
  const mountBody = () => {
    if (body) return;
    body = document.createElement("div");
    body.className = "activity-action-body";
    displayAction.toolCalls.forEach((call) => {
      const result = document.createElement("div");
      result.className = `activity-action-tool-result${displayAction.toolCalls.length === 1 ? " is-standalone" : ""}`;
      result.textContent = call.semanticSummary;
      if (displayAction.toolCalls.length > 1) {
        const row = document.createElement("div");
        row.className = `activity-action-tool is-${call.status}`;
        const status = document.createElement("span");
        status.className = "tool-call-status";
        status.textContent = toolStatusIcon(call);
        const name = document.createElement("span");
        name.className = "tool-call-name";
        name.textContent = call.name;
        const callDuration = document.createElement("span");
        callDuration.className = "tool-call-duration";
        callDuration.textContent = formatToolDuration(call);
        row.append(status, name, callDuration);
        body.appendChild(row);
      }
      body.appendChild(result);
    });
    // Raw tool inputs/outputs can dwarf the visible transcript for delegated
    // work. Construct them only after this one activity is explicitly opened.
    body.appendChild(createActionRawView(displayAction));
    details.appendChild(body);
  };
  const unmountBody = () => {
    body?.remove();
    body = null;
  };
  details.addEventListener("toggle", () => {
    if (details.open) mountBody();
    else unmountBody();
  });
  wireTimelineDetails(details, `${action.timelineId}:tool`);
  if (details.open) mountBody();
  return details;
}

function createDelegationGroupShell({ isNew = false } = {}) {
  const group = document.createElement("section");
  group.className = `delegation-group${isNew ? " is-entering" : ""}`;
  const header = document.createElement("div");
  header.className = "delegation-group-header";
  const title = document.createElement("span");
  title.className = "delegation-group-title";
  title.textContent = "Delegated tasks";
  const meta = document.createElement("span");
  meta.className = "delegation-group-meta";
  header.append(title, meta);
  group.appendChild(header);

  const list = document.createElement("div");
  list.className = "delegation-group-list";
  group.appendChild(list);
  return { group, list, meta };
}

function reconcileDelegationGroup(group, calls, { live = false } = {}) {
  const list = group.querySelector(".delegation-group-list");
  const meta = group.querySelector(".delegation-group-meta");
  const rows = group._delegationRows || new Map();
  group._delegationRows = rows;
  const running = calls.filter((call) => call.status === "running").length;
  const liveKeys = new Set();
  let insertionPoint = list.firstElementChild;
  calls.forEach((call) => {
    const key = String(call.executorTaskKey || call.id || executorNodeId(call) || call.timelineId || call.name);
    liveKeys.add(key);
    let row = rows.get(key);
    if (!row) {
      const task = document.createElement("div");
      task.className = "delegation-task";
      task.dataset.delegationKey = key;
      const host = document.createElement("div");
      host.className = "step-feed-inline-region delegation-task-host";
      host.dataset.stepExecutionKey = String(executorNodeId(call) || "");
      task.appendChild(host);
      row = { task, host };
      rows.set(key, row);
    }
    if (row.task !== insertionPoint) list.insertBefore(row.task, insertionPoint);
    insertionPoint = row.task.nextElementSibling;
    if (Array.isArray(call.stepNodes) && call.stepNodes.length) {
      call.stepNodes.forEach((node) => stepExecutionFeed.appendStatic(node, row.host));
    } else if (live) {
      stepExecutionFeed.bindRootHost(row.host, executorNodeId(call));
    }
  });
  for (const [key, row] of rows) {
    if (liveKeys.has(key)) continue;
    row.task.remove();
    rows.delete(key);
  }
  meta.textContent = `${calls.length} task${calls.length === 1 ? "" : "s"}${running ? ` · ${running} running` : ""}`;
  group.hidden = calls.length === 0;
}

function createAgentActivity(items, wireTimelineDetails, options) {
  const actions = items
    .filter((item) => item.type === "activity_action")
    .map((action) => ({ ...action, toolCalls: activityToolCalls(action) }))
    .filter((action) => action.toolCalls.length);
  const hasReasoning = items.some((item) => item.type === "reasoning");
  if (!actions.length && !hasReasoning) return null;
  const completed = options.completed || !activeSessionRequest()
    && actions.every((action) => action.status !== "running");
  const activity = document.createElement("details");
  activity.className = "agent-activity";
  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "agent-activity-title";
  title.textContent = "Agent activity";
  const meta = document.createElement("span");
  meta.className = "agent-activity-meta";
  const totalDuration = actions.reduce((total, action) => total + (action.durationMs || 0), 0);
  const countLabel = actions.length ? `${actions.length} action${actions.length === 1 ? "" : "s"}` : "";
  const durationLabel = totalDuration
    ? formatToolDuration({ durationMs: totalDuration })
    : actions.some((action) => action.status === "running") ? "running…" : "";
  meta.textContent = [countLabel, durationLabel].filter(Boolean).join(" · ");
  summary.append(title, meta);
  activity.appendChild(summary);

  // Reasoning is supporting detail, not a second assistant answer.  Keeping
  // it expanded during streaming displayed the token-level thought stream
  // directly above the Markdown response and looked like duplicated content.
  // The running state remains visible in the summary; readers can explicitly
  // open the activity when they want the detail.
  wireTimelineDetails(activity, `${options.activityKey}:container`, false);
  if (completed) activity.open = false;
  let body = null;
  const mountBody = () => {
    if (body) return;
    body = document.createElement("div");
    body.className = "agent-activity-body";
    const actionList = document.createElement("div");
    actionList.className = "agent-activity-action-list";
    let rendered = 0;
    const batchSize = 40;
    const more = document.createElement("button");
    more.type = "button";
    more.className = "ghost agent-activity-load-more";
    const appendBatch = () => {
      const end = Math.min(items.length, rendered + batchSize);
      for (; rendered < end; rendered += 1) {
        const item = items[rendered];
        if (item.type === "reasoning") {
          actionList.appendChild(createTimelineReasoning(
            item,
            wireTimelineDetails,
            completed,
            !options.previousItemIds?.has(item.timelineId),
            options.cacheMarkdown,
          ));
        }
        if (item.type === "activity_action") {
          const action = createActivityAction(item, wireTimelineDetails, {
            isNew: !options.previousItemIds?.has(item.timelineId),
          });
          if (action) actionList.appendChild(action);
        }
      }
      more.textContent = `Show more activity (${items.length - rendered} remaining)`;
      more.hidden = rendered >= items.length;
    };
    more.addEventListener("click", appendBatch);
    appendBatch();
    body.appendChild(actionList);
    body.appendChild(more);
    activity.appendChild(body);
  };
  const unmountBody = () => {
    body?.remove();
    body = null;
  };
  activity.addEventListener("toggle", () => {
    if (activity.open) mountBody();
    else unmountBody();
  });
  if (activity.open) mountBody();
  return activity;
}

function segmentArtifacts(segment, claimedPlotPaths) {
  if (segment.type !== "activity" && segment.type !== "delegation") return { plotPaths: [], structurePaths: [] };
  const calls = segment.items
    .filter((item) => item.type === "activity_action")
    .flatMap((action) => action.toolCalls || []);
  const plotPaths = [];
  const structurePaths = [];
  for (const call of calls) {
    for (const path of getPlotPaths(call.output)) {
      if (claimedPlotPaths.has(path)) continue;
      claimedPlotPaths.add(path);
      plotPaths.push(path);
    }
    structurePaths.push(...getStructurePaths(call.output));
  }
  return { plotPaths, structurePaths: [...new Set(structurePaths)] };
}

function renderTimelineSegment(segment, context) {
  const wrapper = document.createElement("div");
  wrapper.className = "timeline-segment";
  wrapper.dataset.timelineSegment = segment.key;
  if (segment.type === "text") {
    const item = segment.items[0];
    const div = document.createElement("div");
    div.className = "markdown-content";
    setMarkdownContent(div, item.text || "", {
      cache: context.cacheMarkdown,
      // Streaming reparses only this affected text segment at the controller's
      // cadence. Immutable history may defer exceptionally large DOM trees.
      defer: context.deferMarkdown,
      anchorPrefix: context.completed
        ? `${context.disclosurePrefix}${item.timelineId || "text:legacy"}:content`
        : "",
    });
    wrapper.appendChild(div);
    return wrapper;
  }

  if (segment.type === "delegation") {
    const { group } = createDelegationGroupShell({
      isNew: !context.previousActivityItemIds.has(segment.items[0].timelineId),
    });
    reconcileDelegationGroup(group, segment.calls, { live: !context.completed });
    wrapper.appendChild(group);
    context.artifacts.plotPaths.forEach((path) => wrapper.appendChild(createTimelineImage(path)));
    if (context.artifacts.structurePaths.length) {
      wrapper.appendChild(createStructureViewButtonGroup(context.artifacts.structurePaths));
    }
    return wrapper;
  }

  const activityKey = `activity:${segment.items[0].timelineId}`;
  const activity = createAgentActivity(segment.items, context.wireTimelineDetails, {
    activityKey,
    previousItemIds: context.previousActivityItemIds,
    cacheMarkdown: context.cacheMarkdown,
    completed: context.completed,
  });
  if (activity) wrapper.appendChild(activity);
  context.artifacts.plotPaths.forEach((path) => wrapper.appendChild(createTimelineImage(path)));
  if (context.artifacts.structurePaths.length) {
    wrapper.appendChild(createStructureViewButtonGroup(context.artifacts.structurePaths));
  }
  return wrapper;
}

// Render one canonical assistant-message model into permanent semantic
// regions. The timeline controls the slot's chronological position, while the
// message view permanently owns its DOM node and delegated-task cards.
function renderTimeline(view, message, shownPlotPaths = null) {
  const { timelineElement: container, element: agentMessage } = view;
  const timeline = message.items;
  const disclosures = chatDisclosureController;
  const messageKey = message.id;
  const disclosurePrefix = `timeline:${messageKey}:`;
  const liveKeys = new Set();
  const wireTimelineDetails = (details, key, defaultOpen = false) => {
    const scopedKey = `${disclosurePrefix}${key}`;
    liveKeys.add(scopedKey);
    disclosures.wire(details, scopedKey, { defaultOpen });
    return scopedKey;
  };
  const updateTimeline = () => {
    // Timeline updates rebuild a compact activity block and any inline Node cards.
    // Persist their actual DOM state first; defaults alone are insufficient
    // once a running Node has become completed but remains visibly open.
    const previousSegments = container._timelineSegments || new Map();
    const previousActivityItemIds = container._activityItemIds || new Set();
    const segments = timelineSegments(timeline);
    const currentActivityItemIds = new Set(segments
      .filter((segment) => segment.type === "activity" || segment.type === "delegation")
      .flatMap((segment) => segment.items.map((item) => item.timelineId)));
    const externalPlotPaths = container._externalPlotPaths
      || new Set(shownPlotPaths ? [...shownPlotPaths] : []);
    container._externalPlotPaths = externalPlotPaths;
    const claimedPlotPaths = new Set(externalPlotPaths);
    const visiblePlotPaths = new Set();
    const nextSegments = new Map();
    let insertionPoint = container.firstElementChild;
    const completed = message.lifecycle === "completed";
    for (const [segmentIndex, segment] of segments.entries()) {
      const artifacts = segmentArtifacts(segment, claimedPlotPaths);
      artifacts.plotPaths.forEach((path) => visiblePlotPaths.add(path));
      const isLastSegment = segmentIndex === segments.length - 1;
      const completionRevision = segment.type === "activity" ? `;completed:${completed ? 1 : 0}` : "";
      const signature = `${segment.revision}${completionRevision};plots:${artifacts.plotPaths.join("\u001f")};structures:${artifacts.structurePaths.join("\u001f")}`;
      const previous = previousSegments.get(segment.key);
      let element = previous?.signature === signature ? previous.element : null;
      if (!element && previous?.element && segment.type === "text") {
        element = previous.element;
        const item = segment.items[0];
        const content = element.querySelector(".markdown-content");
        if (content) setMarkdownContent(content, item.text || "", {
          cache: completed || !isLastSegment,
          defer: completed && !view.live,
          anchorPrefix: completed
            ? `${disclosurePrefix}${item.timelineId || "text:legacy"}:content`
            : "",
        });
      }
      if (!element && previous?.element && segment.type === "delegation") {
        element = previous.element;
        const group = element.querySelector(".delegation-group");
        if (group) reconcileDelegationGroup(group, segment.calls, { live: !completed });
      }
      if (!element) {
        if (previous?.element) disclosures.capture(previous.element);
        element = renderTimelineSegment(segment, {
          artifacts,
          cacheMarkdown: completed || !isLastSegment,
          disclosurePrefix,
          previousActivityItemIds,
          wireTimelineDetails,
          completed,
          deferMarkdown: completed && !view.live,
        });
        // Activity segments are rebuilt as their reasoning/tool content
        // changes. Replace one in place so its permanent Delegated tasks
        // sibling is not pulled out and appended again on every update.
        if (previous?.element?.parentElement === container) {
          if (insertionPoint === previous.element) insertionPoint = element;
          previous.element.replaceWith(element);
        }
      } else {
        element.querySelectorAll("details[data-disclosure-key]").forEach((details) => {
          liveKeys.add(details.dataset.disclosureKey);
        });
      }
      if (element !== insertionPoint) container.insertBefore(element, insertionPoint);
      insertionPoint = element.nextElementSibling;
      nextSegments.set(segment.key, { element, signature });
    }
    for (const [key, previous] of previousSegments) {
      if (nextSegments.get(key)?.element !== previous.element) {
        previous.element.remove();
      }
    }
    disclosures.prunePrefix(disclosurePrefix, liveKeys);
    container._plotPaths = visiblePlotPaths;
    container._activityItemIds = currentActivityItemIds;
    container._timelineSegments = nextSegments;
    visiblePlotPaths.forEach((path) => shownPlotPaths?.add(path));
    const hasContent = nextSegments.size > 0;
    agentMessage.classList.toggle("is-pending", !hasContent && message.lifecycle === "created");
    if (hasContent) agentMessage.classList.remove("is-waiting");
  };
  updatePreservingReadingPosition(updateTimeline, { mutationRoot: agentMessage });
}

// Create one assistant view with permanent timeline, delegation, and metadata
// slots. The returned view is the only DOM owner for this message.
function addAgentTimelineMessage(message, shownPlotPaths = null, msgIndex, container = chatArea, timing = {}) {
  const outer = document.createElement("div");
  // A live turn starts before the server has sent its first event. Keep its
  // empty shell out of view until it contains a timeline item or step card.
  outer.className = "message agent-message is-pending";
  if (msgIndex !== undefined) outer.dataset.msgIndex = String(msgIndex);
  outer.appendChild(createAgentAvatarEl());
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const inner = document.createElement("div");
  inner.className = "timeline-container";
  outer.dataset.readingAnchor = `message:${message.id}`;
  const duration = document.createElement("div");
  duration.className = "agent-bubble-duration";
  duration.setAttribute("aria-live", "off");
  const meta = document.createElement("div");
  meta.className = "agent-bubble-meta";
  meta.appendChild(duration);
  bubble.appendChild(inner);

  const startedAt = normalizeAgentTimestamp(message.startedAt ?? timing.startedAt);
  let timer = null;
  const renderDuration = (endedAt = null) => {
    if (!Number.isFinite(startedAt)) {
      duration.hidden = true;
      return;
    }
    const finishedAt = normalizeAgentTimestamp(endedAt);
    const elapsedMs = Math.max(0, (finishedAt ?? Date.now()) - startedAt);
    duration.hidden = false;
    duration.textContent = `Total time · ${formatAgentDuration(elapsedMs)}`;
    duration.title = "Total agent runtime";
  };
  const finishDuration = (endedAt = Date.now()) => {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    renderDuration(endedAt);
  };
  const finishLiveActivity = () => {
    const openActivities = [...inner.querySelectorAll("details.agent-activity[open]")];
    openActivities.forEach((activity) => { activity.open = false; });
    return Promise.resolve();
  };
  const endedAt = normalizeAgentTimestamp(message.endedAt ?? timing.endedAt);
  renderDuration(endedAt);
  if (timing.live && Number.isFinite(startedAt) && !Number.isFinite(endedAt)) {
    timer = window.setInterval(renderDuration, 1000);
  }
  outer.append(bubble, meta);
  if (timing.live) outer.classList.add("is-entering");
  appendLiveTurnChild(container, outer);
  const view = {
    element: outer,
    timelineElement: inner,
    stepFeedLiveHost: inner,
    finishDuration,
    finishLiveActivity,
    live: Boolean(timing.live),
  };
  renderTimeline(view, message, shownPlotPaths);
  return view;
}

function addPlanApprovalActions(messageView) {
  const agentMessage = messageView?.element || messageView?.closest?.(".agent-message");
  if (!agentMessage || agentMessage.nextElementSibling?.classList.contains("plan-approval-message")) return;
  const responseMessage = document.createElement("div");
  responseMessage.className = "message user-message plan-approval-message is-entering";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const prompt = document.createElement("div");
  prompt.className = "plan-approval-prompt";
  prompt.textContent = "How would you like to proceed?";
  const actions = document.createElement("div");
  actions.className = "plan-approval-actions";
  actions.setAttribute("role", "group");
  actions.setAttribute("aria-label", "Plan actions");

  const disableControls = () => responseMessage.querySelectorAll("button, input").forEach((item) => { item.disabled = true; });
  [["yes", "Approve plan", "Approve this plan and start execution", "is-approve"], ["replan", "Revise plan", "Ask the agent to revise this plan", "is-replan"]]
    .forEach(([message, label, title, variant]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `plan-approval-btn ${variant}`;
      button.textContent = label;
      button.title = title;
      button.addEventListener("click", () => {
        disableControls();
        messageStreamController.send(message);
      });
      actions.appendChild(button);
    });

  const feedback = document.createElement("div");
  feedback.className = "plan-feedback";
  const feedbackLabel = document.createElement("label");
  feedbackLabel.className = "plan-feedback-label";
  feedbackLabel.textContent = "Or describe what you’d like changed";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "plan-feedback-input";
  input.placeholder = "Other feedback or changes…";
  input.setAttribute("aria-label", "Other feedback about this plan");
  const sendFeedback = () => {
    const message = input.value.trim();
    if (!message) return;
    disableControls();
    messageStreamController.send(message);
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendFeedback();
    }
  });
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "plan-approval-btn plan-feedback-submit";
  submit.textContent = "Send";
  submit.title = "Send feedback about this plan";
  submit.disabled = true;
  input.addEventListener("input", () => {
    submit.disabled = !input.value.trim();
  });
  submit.addEventListener("click", sendFeedback);
  feedbackLabel.appendChild(input);
  feedback.append(feedbackLabel, submit);
  bubble.append(prompt, actions, feedback);
  responseMessage.appendChild(bubble);
  agentMessage.after(responseMessage);
  // Approval prompts follow the same bottom placement as every other newly
  // appended dialog. The shared reserve keeps the full prompt above the
  // floating composer, regardless of composer/upload height.
  scrollToBottom({ preserveUserPosition: true });
}

function formatStepDuration(node) {
  if (!node.start_time) return "—";
  if (!node.end_time) {
    const elapsedMs = Date.now() - new Date(node.start_time).getTime();
    if (!Number.isFinite(elapsedMs)) return "running…";
    const elapsedSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    return hours > 0
      ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      : `${minutes}:${String(seconds).padStart(2, "0")}`;
  }
  const secs = ((new Date(node.end_time) - new Date(node.start_time)) / 1000).toFixed(1);
  return `${secs}s`;
}

function stepFeedTitle(node) {
  const input = node.input || {};
  // A user needs to recognize the work before they need its durable ID. Keep
  // the latter as a secondary label in the card header, while leading with
  // the task the executor was delegated to perform.
  return {
    action: input.action || node.label || input.node_id || input.step_id || node.id || "Task",
    identifier: input.node_id || input.step_id || node.id || "",
  };
}

function renderStepInput(input) {
  const details = document.createElement("details");
  details.className = "step-feed-nested";
  const summary = document.createElement("summary");
  summary.textContent = "Input";
  details.appendChild(summary);
  details.appendChild(createPayloadBlock(input));
  return details;
}

function renderStepConversationEvent(evt, { collapsed = false, timelineId } = {}) {
  if (["thought", "text"].includes(evt.type)) {
    return createTimelineReasoning({
      timelineId: timelineId || `step-conversation:${evt.timestamp || ""}:${evt.author || ""}:${evt.type}`,
      text: String(evt.content || ""),
    }, () => {}, collapsed);
  }
  const details = document.createElement("details");
  details.className = "agent-activity-action step-feed-conversation";
  const summary = document.createElement("summary");
  const icon = evt.type === "thought" ? "💭" : evt.type === "text" ? "💬" : evt.type === "function_call" ? "🔧" : "↩";
  const status = document.createElement("span");
  status.className = "agent-activity-status";
  status.textContent = icon;
  const heading = document.createElement("span");
  heading.className = "activity-action-heading";
  const title = document.createElement("span");
  title.className = "activity-action-title";
  title.textContent = `[${evt.author || "step_executor"}] ${evt.type || "event"}`;
  heading.appendChild(title);
  summary.append(status, heading);
  details.appendChild(summary);
  const body = document.createElement("div");
  body.className = "activity-action-body";
  body.appendChild(createPayloadBlock(evt.content));
  details.appendChild(body);
  return details;
}

function renderStepToolCall(tc) {
  const status = tc.status || (tc.error ? "failed" : tc.end_time || tc.result_summary ? "success" : "running");
  const startedAt = tc.start_time ? new Date(tc.start_time).getTime() : null;
  const endedAt = tc.end_time ? new Date(tc.end_time).getTime() : null;
  const durationMs = Number.isFinite(startedAt) && Number.isFinite(endedAt) ? Math.max(0, endedAt - startedAt) : null;
  const toolCall = {
    ...tc,
    status,
    startedAt: Number.isFinite(startedAt) ? startedAt : null,
    durationMs,
    input: tc.input ?? tc.args ?? tc.args_summary,
    output: tc.output ?? tc.result ?? tc.result_summary,
    semanticSummary: tc.result_summary || tc.error || (status === "running" ? "Running…" : "Completed"),
  };
  const details = createActivityAction({
    timelineId: `step-tool:${tc.id || `${tc.name || "tool"}:${tc.start_time || ""}`}`,
    toolCalls: [toolCall],
  }, () => {}, { includeExecutorTools: true });
  const raw = details?.querySelector(".tool-call-raw");
  const structurePaths = getStructurePaths(tc);
  if (structurePaths.length) raw?.appendChild(createStructureViewButtonGroup(structurePaths));
  return details;
}

const workspaceTerminalController = createWorkspaceTerminalController({
  state,
  container: workspaceTerminalEl,
  panel: workspaceCli,
  toggleButton: workspaceCliToggle,
});

workspaceCliToggle?.addEventListener("click", () => {
  workspaceTerminalController.setOpen(workspaceCli?.classList.contains("hidden"));
});

skillGraphOpenBtn?.addEventListener("click", () => {
  skillGraphController.open({ force: true });
});

async function refreshSessionFiles(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
  if (!sessionId || !state.sessionReady) return;
  try {
    const resp = await fetch(`/api/sessions/${sessionId}/files`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (sessionRequestKey(sessionId, owner) !== sessionRequestKey()) return;
    renderSessionFilesTree(data.files || []);
  } catch (_) {}
}

const sessionRuntime = createSessionRuntime({
  session: {
    state,
    requestKey: sessionRequestKey,
    activeRequest: activeSessionRequest,
    releaseRequest: releaseSessionRequest,
  },
  timeline: {
    chatArea,
    stepExecutionFeed,
    getFunctionResponse,
    displayStoredUserText: displayMessageFromStoredUserText,
    addMessage,
    addAgentTimelineMessage,
    addPlanApprovalActions,
    renderTimeline,
    clearDisclosures: () => chatDisclosureController.clear(),
  },
  ui: {
    updateSendButtonState,
    renderSessionBanner,
    renderSessionFilesTree,
    refreshSessionFiles,
    onRequestStateChange: rerenderSessionList,
    attachAgentRunningIndicator,
    updateAgentRunningStatus,
    workdirDisplay: document.getElementById("session-workdir-display"),
  },
  managedRun: { eventsUrl: managedRunEventsUrl },
});
if (new URLSearchParams(window.location.search).has("debug_transcript")) {
  window.__matcreatorTranscriptMetrics = sessionRuntime.metrics;
}

const messageStreamController = createMessageStreamController({
  session: {
    state,
    activeRequest: activeSessionRequest,
    requestKey: sessionRequestKey,
    backendUserId: activeSessionBackendUserId,
    canWrite: canWriteActiveSession,
    create: createSession,
    releaseRequest: releaseSessionRequest,
    runtime: sessionRuntime,
    refreshFiles: refreshSessionFiles,
    generateSummary: generateSessionSummary,
  },
  composer: {
    appName: APP_NAME,
    chatArea,
    textInput,
    showLoginModal,
    addMessage: (role, content, msgIndex, container, options) => addMessage(
      role, content, msgIndex, container || sessionRuntime.getLiveHost(), options,
    ),
    addAgentTimelineMessage,
    addPlanApprovalActions,
    renderTimeline,
    messageWithUploadNames,
    messageWithUploadContext,
    clearCurrentUploads,
    autoResizeTextInput,
  },
  execution: {
    stepExecutionFeed,
    agentGraph,
    planGraph,
    updateSendButtonState,
    updateAgentRunningStatus,
    attachAgentRunningIndicator,
    managedRunEventsUrl,
    shouldRefreshPlanGraphForTool,
    showPlanGraph,
    onRequestStateChange: rerenderSessionList,
  },
});

function setUploadStatus(message, tone = "idle") {
  if (!uploadStatus) return;
  uploadStatus.textContent = message || "";
  uploadStatus.className = `upload-status upload-status-${tone}`;
}

function renderCurrentUploadChips() {
  if (!uploadStatus) return;
  uploadStatus.innerHTML = "";
  uploadStatus.className = "upload-status upload-file-list";
  if (!state.currentUploads.length) return;

  state.currentUploads.forEach((file) => {
    const chip = document.createElement("span");
    chip.className = "upload-file-chip";

    const name = document.createElement("span");
    name.className = "upload-file-name";
    name.textContent = file.name;
    name.title = file.path;
    chip.appendChild(name);

    const removeBtn = document.createElement("button");
    removeBtn.className = "upload-file-remove";
    removeBtn.type = "button";
    removeBtn.title = "Delete uploaded file";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => deleteUploadedFile(file));
    chip.appendChild(removeBtn);

    uploadStatus.appendChild(chip);
  });
}

function clearCurrentUploads() {
  state.currentUploads = [];
  renderCurrentUploadChips();
}

function mergeUploadedFiles(existingFiles, newFiles) {
  const merged = [...existingFiles];
  const seenPaths = new Set(existingFiles.map((file) => file?.path).filter(Boolean));

  newFiles.forEach((file) => {
    const path = file?.path;
    if (path && seenPaths.has(path)) return;
    if (path) seenPaths.add(path);
    merged.push(file);
  });

  return merged;
}

function sessionRelativeUploadPath(file) {
  const normalized = String(file?.path || "").replaceAll("\\", "/");
  const marker = `/${state.sessionId}/`;
  const markerIdx = normalized.indexOf(marker);
  if (markerIdx >= 0) return normalized.slice(markerIdx + marker.length);
  return file?.name ? `uploads/${file.name}` : normalized;
}

function messageWithUploadContext(message, uploads) {
  if (!uploads.length) return message;
  const fileLines = uploads.map((file) => {
    const relPath = sessionRelativeUploadPath(file);
    return `- ${file.name}: ${relPath} (absolute path: ${file.path})`;
  });
  return [
    message,
    "",
    "The user uploaded the following file(s) for this message. They are saved in the current session workspace. Use these paths when inspecting or processing the files:",
    ...fileLines,
  ].join("\n");
}

function formatUploadNames(uploadNames) {
  if (!uploadNames.length) return "";
  return `Attached: ${uploadNames.map((name) => `\`${name}\``).join(", ")}`;
}

function messageWithUploadNames(message, uploads) {
  const uploadNames = uploads.map((file) => file.name).filter(Boolean);
  const suffix = formatUploadNames(uploadNames);
  return suffix ? `${message}\n\n${suffix}` : message;
}

function displayMessageFromStoredUserText(message) {
  const marker = "\n\nThe user uploaded the following file(s) for this message.";
  const rawMessage = String(message || "");
  const markerIdx = rawMessage.indexOf(marker);
  if (markerIdx < 0) return rawMessage;

  const visibleMessage = rawMessage.slice(0, markerIdx);
  const hiddenContext = rawMessage.slice(markerIdx);
  const uploadNames = hiddenContext
    .split("\n")
    .map((line) => line.match(/^-\s+([^:]+):/)?.[1]?.trim())
    .filter(Boolean);
  const suffix = formatUploadNames(uploadNames);
  return suffix ? `${visibleMessage}\n\n${suffix}` : visibleMessage;
}

async function deleteUploadedFile(file) {
  if (!file?.path || !state.sessionId) return;
  try {
    const resp = await fetch(
      `/api/sessions/${encodeURIComponent(state.sessionId)}/files?path=${encodeURIComponent(file.path)}`,
      { method: "DELETE" }
    );
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    state.currentUploads = state.currentUploads.filter((item) => item.path !== file.path);
    renderCurrentUploadChips();
    await refreshSessionFiles();
  } catch (err) {
    setUploadStatus(`Delete failed: ${err.message || err}`, "error");
  }
}

async function uploadFilesToSession(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (!state.userId) { showLoginModal(); return; }
  if (!canWriteActiveSession()) {
    addMessage("agent", `Admin view is read-only for ${state.activeSessionUserId}'s session.`, undefined, sessionRuntime.getLiveHost());
    return;
  }

  if (!state.sessionReady) await createSession();
  if (!state.sessionReady) {
    setUploadStatus("Could not create session.", "error");
    return;
  }

  if (fileUploadBtn) fileUploadBtn.disabled = true;
  const uploaded = [];
  try {
    for (const file of files) {
      setUploadStatus(`Uploading ${file.name}...`, "busy");
      const formData = new FormData();
      formData.append("file", file);
      const resp = await fetch(`/api/sessions/${encodeURIComponent(state.sessionId)}/files`, {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      uploaded.push(await resp.json());
    }

    await refreshSessionFiles();
    state.currentUploads = mergeUploadedFiles(state.currentUploads, uploaded);
    renderCurrentUploadChips();
  } catch (err) {
    setUploadStatus(`Upload failed: ${err.message || err}`, "error");
  } finally {
    if (fileUploadBtn) fileUploadBtn.disabled = false;
    fileUploadInput.value = "";
  }
}

// ---------------------------------------------------------------------------
// Session summary (experimental)
// ---------------------------------------------------------------------------

function renderSessionBanner(summary, { animate = false } = {}) {
  if (!sessionSummaryText) return;
  const defaultTitle = sessionSummaryText.dataset.defaultTitle || "Chat";
  stopTextReveal(sessionSummaryText);
  if (summary) {
    chatTab?.setAttribute("title", sessionTabTooltip(summary));
    sessionSummaryText.classList.remove("session-summary-placeholder");
    if (animate) runTextReveal(sessionSummaryText, summary);
    else sessionSummaryText.textContent = summary;
  } else {
    sessionSummaryText.textContent = defaultTitle;
    chatTab?.setAttribute("title", sessionTabTooltip(defaultTitle));
    sessionSummaryText.classList.remove("session-summary-placeholder");
  }
}

function stopTextReveal(el) {
  el._textReveal?.cancel();
  el._textReveal = null;
}

function runTextReveal(el, text) {
  const reveal = createSmoothTextReveal(el);
  el._textReveal = reveal;
  // The summary endpoint currently returns its result as one chunk. The reveal
  // controller also accepts incremental chunks without changing this caller.
  reveal.append(text);
  reveal.finish();
}

function startSummaryEdit() {
  if (!sessionSummaryText || !chatTab || chatTab.querySelector("input")) return;
  const isPlaceholder = sessionSummaryText.classList.contains("session-summary-placeholder");
  const defaultTitle = sessionSummaryText.dataset.defaultTitle || "Chat";
  const original = isPlaceholder || sessionSummaryText.textContent === defaultTitle ? "" : sessionSummaryText.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.value = original;
  input.className = "session-summary-input";
  input.maxLength = 60;
  input.placeholder = "Enter session name…";
  const labelWidth = Math.ceil(sessionSummaryText.getBoundingClientRect().width);
  input.style.width = `${Math.max(44, labelWidth)}px`;
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("dblclick", (e) => e.stopPropagation());
  sessionSummaryText.style.display = "none";
  chatTab.insertBefore(input, sessionSummaryText);
  input.focus();
  input.select();

  const finish = async (save) => {
    const newValue = input.value.trim();
    input.remove();
    sessionSummaryText.style.display = "";
    if (save && newValue !== original) {
      if (newValue) {
        state.sessionSummaries[state.sessionId] = newValue;
        state.summaryGeneratedFor.add(state.sessionId);
        renderSessionBanner(newValue);
        await saveSessionSummary(state.sessionId, newValue);
      } else {
        delete state.sessionSummaries[state.sessionId];
        state.summaryGeneratedFor.delete(state.sessionId);
        renderSessionBanner("");
        await saveSessionSummary(state.sessionId, "");
      }
      rerenderSessionList();
    } else if (!save) {
      renderSessionBanner(original || state.sessionSummaries[state.sessionId] || "");
    }
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

chatTab?.addEventListener("dblclick", (e) => {
  e.preventDefault();
  e.stopPropagation();
  startSummaryEdit();
});

async function saveSessionSummary(sessionId, summary) {
  try {
    const owner = state.activeSessionUserId || state.userId || "";
    const query = owner ? `?user_id=${encodeURIComponent(owner)}` : "";
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/summary${query}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ summary }),
    });
  } catch (_) {
    // silently ignore
  }
}

async function generateSessionSummary(sessionId) {
  try {
    const owner = state.activeSessionUserId || state.userId || "";
    const query = owner ? `?user_id=${encodeURIComponent(owner)}` : "";
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/summarize${query}`, {
      method: "POST",
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.summary) {
      state.sessionSummaries[sessionId] = data.summary;
      state.summaryGeneratedFor.add(sessionId);
      // Only update banner if user is still on this session
      if (sessionId === state.sessionId) {
        renderSessionBanner(data.summary, { animate: true });
      }
      // Refresh session list to show summary
      rerenderSessionList();
    }
  } catch (_) {
    // silently ignore — summary is non-critical
  }
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

async function createSession() {
  state.activeSessionUserId = state.userId;
  const sessionId = state.sessionId;
  const url = `/apps/${APP_NAME}/users/${activeSessionBackendUserId()}/sessions/${sessionId}`;
  const defaultWorkdir = (state.defaultWorkdir || "").trim();
  const sessionWorkdir = state.customWorkdir || defaultWorkdir;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(state.agentMode !== "normal" ? { agent_mode: state.agentMode } : {}),
        ...(state.agentMode === "bench" ? { benchmark_mode: true } : {}),
        ...(sessionWorkdir ? { custom_workdir: sessionWorkdir } : {}),
      }),
    });
    const existingResp = resp.status === 409 ? await fetch(url) : null;
    if (!resp.ok) {
      if (!existingResp?.ok) {
        if (resp.status !== 409) console.error(`Failed to create session: HTTP ${resp.status}`, await resp.text());
        return;
      }
    }
    state.sessionReady = true;
    storeSessionSelection(sessionId, state.activeSessionUserId);
    await loadSessions();
  } catch (err) {
    console.error("Failed to create session:", err);
  }
}

function renderKnowledgeReviewStatus(review) {
  const status = review?.status || "idle";
  const running = status === "running";
  const progress = review?.progress || {};
  const phase = review?.phase || "memory";
  const completed = progress.completed || 0;
  const total = progress.total || 0;
  const results = Array.isArray(review?.results) ? review.results : [];
  const errors = Array.isArray(review?.errors) ? review.errors : [];
  if (knowledgeReviewBanner) {
    knowledgeReviewBanner.disabled = running;
    knowledgeReviewBanner.className = `knowledge-review-banner status-${status}`;
    const detail = review?.summary || errors[0];
    knowledgeReviewBanner.title = detail
      ? `${detail}${running ? "" : " Click to review memory and graph nodes."}`
      : running
        ? "Knowledge review is running"
        : "Click to review memory and graph nodes";
  }
  knowledgeReviewSpinner?.classList.toggle("hidden", !running);
  if (knowledgeReviewText) {
    if (running) {
      const phaseLabel = phase === "graph" ? "graph nodes" : "memory";
      knowledgeReviewText.textContent = total
        ? `Reviewing ${phaseLabel}: ${completed}/${total} (${progress.percent || 0}%)`
        : `Starting ${phaseLabel} review`;
    } else if (status === "failed") {
      knowledgeReviewText.textContent = `Review failed: ${errors[0] || "unknown error"} · click to retry`;
    } else if (status === "completed" || status === "completed_with_errors") {
      const memoryCount = results.filter((item) => item.phase === "memory").length;
      const graphCount = results.filter((item) => item.phase === "graph").length;
      const warning = errors.length ? `, ${errors.length} errors` : "";
      const summary = review?.summary?.trim();
      if (memoryCount === 0 && graphCount === 0 && summary) {
        knowledgeReviewText.textContent = `${summary}${warning} · click to run again`;
      } else {
        knowledgeReviewText.textContent = `Review complete: ${memoryCount} memory, ${graphCount} graph actions${warning} · click to run again`;
      }
    } else {
      knowledgeReviewText.textContent = "Review memory and graph · click to start";
    }
  }
  if (!running && knowledgeReviewPoll) {
    clearInterval(knowledgeReviewPoll);
    knowledgeReviewPoll = null;
  }
}

async function refreshKnowledgeReviewStatus() {
  try {
    const resp = await fetch("/api/knowledge-review/status");
    if (!resp.ok) return;
    const review = await resp.json();
    renderKnowledgeReviewStatus(review);
    if (review.status === "running" && !knowledgeReviewPoll) {
      knowledgeReviewPoll = setInterval(refreshKnowledgeReviewStatus, 2000);
    }
  } catch (_) {
    // The banner is informational; session work should continue if polling fails.
  }
}

async function startKnowledgeReview() {
  renderKnowledgeReviewStatus({
    status: "running",
    phase: "memory",
    message: "Starting Know-Do Graph review.",
  });
  try {
    const resp = await fetch("/api/knowledge-review/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });
    const review = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      renderKnowledgeReviewStatus({
        status: "failed",
        errors: [review.detail || `HTTP ${resp.status}`],
      });
      return;
    }
    renderKnowledgeReviewStatus(review);
    if (!knowledgeReviewPoll) {
      knowledgeReviewPoll = setInterval(refreshKnowledgeReviewStatus, 2000);
    }
  } catch (_) {
    renderKnowledgeReviewStatus({
      status: "failed",
      errors: ["Could not reach the review service"],
    });
  }
}

knowledgeReviewBanner?.addEventListener("click", () => {
  if (!knowledgeReviewBanner.disabled) startKnowledgeReview();
});

async function patchSessionAgentMode(mode) {
  if (!state.sessionReady || !state.sessionId) return;
  const url = `/apps/${APP_NAME}/users/${encodeURIComponent(activeSessionBackendUserId())}/sessions/${encodeURIComponent(state.sessionId)}`;
  try {
    const delta = { agent_mode: mode, benchmark_mode: mode === "bench" };
    const resp = await fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state_delta: delta }),
    });
    if (!resp.ok) console.error(`Failed to patch agent mode: HTTP ${resp.status}`);
  } catch (err) {
    console.error("Failed to patch agent mode:", err);
  }
}

function renderSessionSnapshot(snapshot) {
  if (!snapshot) return;
  if (sessionRuntime.restoreSessionSnapshot(snapshot)) {
    renderSessionFilesTree(snapshot.files || []);
    return;
  }
  renderSessionBanner(snapshot.summary || "");
  sessionRuntime.renderSessionTimeline(
    snapshot.events || [],
    snapshot.graphNodes || [],
    Boolean(snapshot.awaitingPlanApproval),
  );
  renderSessionFilesTree(snapshot.files || []);
  sessionRuntime.updateSessionWorkdirDisplay(snapshot.sessionData || {});
}

// ---------------------------------------------------------------------------
// Streaming deduplication helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Message sending + SSE streaming
// ---------------------------------------------------------------------------

// Structure viewer
// ---------------------------------------------------------------------------

function structureTabId(path) {
  let hash = 0;
  const source = String(path || "");
  for (let i = 0; i < source.length; i++) {
    hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
  }
  return `structure-${Math.abs(hash)}`;
}

function structureTabTitle(path) {
  const filename = String(path || "Structure").split(/[\\/]/).filter(Boolean).pop();
  return filename || "Structure";
}

function activateCenterTab(tabId) {
  state.activeCenterTabId = tabId;
  centerTabsScroll?.querySelectorAll(".center-tab")?.forEach((tab) => {
    const active = tab.dataset.tabId === tabId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  centerTabPanels?.querySelectorAll(".center-tab-panel")?.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.tabId === tabId);
  });

  const structureTab = structureTabs.get(tabId);
  state.structure3dViewer = structureTab?.viewer || null;
  skillGraphController.activate(tabId);
}

function closeCenterTab(tabId) {
  if (skillGraphController.close(tabId)) {
    if (state.activeCenterTabId === tabId) {
      activateCenterTab("chat");
    }
    return;
  }

  const tab = structureTabs.get(tabId);
  if (!tab) return;

  if (tab.destroyViewer) void tab.destroyViewer();
  tab.button.remove();
  tab.panel.remove();
  structureTabs.delete(tabId);

  if (state.activeCenterTabId === tabId) {
    activateCenterTab("chat");
  }
}

function ensureStructureTab(item) {
  const tabId = structureTabId(item.path);
  const existing = structureTabs.get(tabId);
  if (existing) {
    activateCenterTab(tabId);
    return existing;
  }

  const button = document.createElement("button");
  button.className = "center-tab";
  button.type = "button";
  button.role = "tab";
  button.dataset.tabId = tabId;
  button.id = `tab-${tabId}`;
  button.setAttribute("aria-selected", "false");
  button.setAttribute("aria-controls", `${tabId}-panel`);
  button.title = item.path;

  const title = document.createElement("span");
  title.className = "center-tab-title";
  title.textContent = structureTabTitle(item.path);
  button.appendChild(title);

  const close = document.createElement("span");
  close.className = "center-tab-close";
  close.dataset.closeTabId = tabId;
  close.setAttribute("aria-hidden", "true");
  close.textContent = "×";
  button.appendChild(close);

  const panel = document.createElement("div");
  panel.className = "center-tab-panel structure-tab-panel";
  panel.id = `${tabId}-panel`;
  panel.role = "tabpanel";
  panel.dataset.tabId = tabId;
  panel.setAttribute("aria-labelledby", button.id);

  const header = document.createElement("div");
  header.className = "structure-tab-header";

  const labelWrap = document.createElement("div");
  const eyebrow = document.createElement("div");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Structure";
  const meta = document.createElement("div");
  meta.className = "sv-meta";
  labelWrap.append(eyebrow, meta);
  header.appendChild(labelWrap);

  const canvas = document.createElement("div");
  canvas.className = "sv-canvas structure-tab-canvas";

  panel.append(header, canvas);
  centerTabsScroll?.appendChild(button);
  centerTabPanels?.appendChild(panel);

  const tab = { id: tabId, item, button, panel, canvas, meta, viewer: null, destroyViewer: null };
  structureTabs.set(tabId, tab);
  activateCenterTab(tabId);
  return tab;
}

centerTabs?.addEventListener("click", (event) => {
  const closeEl = event.target.closest("[data-close-tab-id]");
  if (closeEl) {
    event.stopPropagation();
    closeCenterTab(closeEl.dataset.closeTabId);
    return;
  }

  const tab = event.target.closest(".center-tab");
  if (tab?.dataset.tabId) activateCenterTab(tab.dataset.tabId);
});

async function openViewer(item) {
  graphDetail.classList.add("hidden");
  const tab = ensureStructureTab(item);
  if (tab.viewer) return;
  if (tab.destroyViewer) await tab.destroyViewer();
  tab.viewer = null;
  tab.destroyViewer = null;
  tab.canvas.innerHTML = '<div style="color:var(--muted);padding:16px;font-size:var(--font-size-ui)">Loading…</div>';
  tab.meta.textContent = "";

  try {
    const [resp, [structureViewer, svelte]] = await Promise.all([
      fetch(`/api/structure/view?path=${encodeURIComponent(item.path)}&session_id=${encodeURIComponent(state.sessionId || "")}`),
      loadStructureViewerModules(),
    ]);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    tab.canvas.innerHTML = "";
    const structureMeta =
      `${data.formula}  ·  ${data.n_atoms} atoms${data.periodic ? "  ·  periodic" : ""}`;
    const viewer = svelte.mount(structureViewer.default, {
      target: tab.canvas,
      props: {
        structure_string: data.structure_string || data.xyz,
        source_path: item.path,
        session_id: state.sessionId || "",
        background_color: state.theme === "light" ? "#f8fbff" : "#06080f",
        performance_mode: data.n_atoms > 500 ? "speed" : "quality",
        on_modified: () => {
          tab.meta.textContent = `${structureMeta}  ·  unsaved atom edits`;
        },
        on_generated: (generated) => {
          const generatedMeta = `${generated.formula}  ·  ${generated.n_atoms} atoms`;
          tab.meta.textContent = `${generatedMeta}  ·  ${generated.operation}  ·  saved`;
          void refreshSessionFiles();
        },
      },
    });
    tab.viewer = viewer;
    tab.destroyViewer = () => svelte.unmount(viewer);
    if (state.activeCenterTabId === tab.id) state.structure3dViewer = viewer;

    tab.meta.textContent = `${structureMeta}  ·  Select an atom to edit it`;
  } catch (err) {
    const error = document.createElement("div");
    error.className = "viewer-load-error";
    error.textContent = `Failed to load structure: ${String(err?.message || err)}`;
    tab.canvas.replaceChildren(error);
  }
}

// ---------------------------------------------------------------------------
// File viewer
// ---------------------------------------------------------------------------

async function openFileViewer(file) {
  const modal = document.getElementById("file-viewer-modal");
  const content = document.getElementById("fv-content");
  const filenameEl = document.getElementById("fv-filename");
  if (!modal) return;

  filenameEl.textContent = file.name;
  content.innerHTML = '<p style="color:var(--muted);padding:16px 20px">Loading…</p>';
  modal.classList.remove("hidden");

  const type = classifyPath(file.path);
  const url = pathToApiUrl(file.path);

  if (type === "image") {
    const wrap = document.createElement("div");
    wrap.className = "fv-img-wrap";
    const img = document.createElement("img");
    img.src = url;
    img.alt = file.name;
    img.style.cursor = "zoom-in";
    img.addEventListener("click", () => lightbox.open(url));
    wrap.appendChild(img);
    content.innerHTML = "";
    content.appendChild(wrap);
    return;
  }

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    if (text.includes("\0")) {
      content.innerHTML = '<p style="color:var(--muted);padding:16px 20px">Binary file — cannot preview.</p>';
      return;
    }
    const pre = document.createElement("pre");
    pre.className = "fv-pre";
    pre.textContent = text;
    content.innerHTML = "";
    content.appendChild(pre);
  } catch (err) {
    const error = document.createElement("p");
    error.className = "file-viewer-error";
    error.textContent = `Failed to load: ${err.message}`;
    content.replaceChildren(error);
  }
}

document.getElementById("fv-close")?.addEventListener("click", () => {
  document.getElementById("file-viewer-modal")?.classList.add("hidden");
});
document.getElementById("file-viewer-modal")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget)
    e.currentTarget.classList.add("hidden");
});

// ---------------------------------------------------------------------------
// Image lightbox
// ---------------------------------------------------------------------------

const lightbox = createImageLightbox();

layoutController.init();

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

sendBtn.addEventListener("click", () => {
  if (requestHasActiveRun(activeSessionRequest())) {
    messageStreamController.stop();
    return;
  }
  messageStreamController.send(textInput.value);
});
textInput.addEventListener("keydown", (e) => {
  // A Chinese/Japanese/Korean IME uses Enter to confirm its active candidate.
  // Do not treat that keypress as a chat submission. `keyCode === 229` is a
  // compatibility fallback for browsers that do not set `isComposing` here.
  if (e.isComposing || e.keyCode === 229) return;

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (requestHasActiveRun(activeSessionRequest())) return;
    messageStreamController.send(textInput.value);
  }
});

if (fileUploadBtn && fileUploadInput) {
  fileUploadBtn.addEventListener("click", () => fileUploadInput.click());
  fileUploadInput.addEventListener("change", (e) => uploadFilesToSession(e.target.files));
}

// Avatar upload
const avatarUploadInput = document.getElementById("avatar-upload-input");
const avatarUploadBtn = document.getElementById("avatar-upload-btn");
if (avatarUploadBtn && avatarUploadInput) {
  applyUserAvatarToEl(avatarUploadBtn);
  avatarUploadBtn.addEventListener("click", () => avatarUploadInput.click());
  avatarUploadInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setUserAvatar(ev.target.result);
      applyUserAvatarToEl(avatarUploadBtn);
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  });
}

// Agent mode selector
function updateComposerModeState(mode) {
  if (!inputContainer) return;
  inputContainer.dataset.agentMode = mode || "normal";
}

if (modeSelector && modeTrigger && modeMenu) {
  const modeDetails = {
    flash: { label: "Flash", icon: '<svg viewBox="0 0 24 24"><path d="m13 2-9 12h7l-1 8 10-13h-7z"/></svg>' },
    normal: { label: "Standard", icon: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/></svg>' },
    bench: { label: "Bench", icon: '<svg viewBox="0 0 24 24"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 16h8"/></svg>' },
  };
  const modeButtons = [...modeSelector.querySelectorAll(".mode-btn")];
  const modeLabel = modeTrigger.querySelector(".mode-trigger-label");
  const modeIcon = modeTrigger.querySelector(".mode-trigger-icon");
  let closeTimer = null;
  let menuPinned = false;

  function setMenuOpen(open, { pinned = menuPinned, focusSelected = false } = {}) {
    window.clearTimeout(closeTimer);
    menuPinned = open && pinned;
    modeSelector.classList.toggle("is-open", open);
    modeTrigger.setAttribute("aria-expanded", String(open));
    if (open && focusSelected) {
      modeButtons.find((btn) => btn.dataset.mode === state.agentMode)?.focus();
    }
  }

  function renderMode(mode) {
    const detail = modeDetails[mode] || modeDetails.normal;
    modeLabel.textContent = detail.label;
    modeIcon.innerHTML = detail.icon;
    modeSelector.dataset.selectedMode = mode;
    modeButtons.forEach((btn) => {
      const selected = btn.dataset.mode === mode;
      btn.classList.toggle("mode-btn-active", selected);
      btn.setAttribute("aria-checked", String(selected));
    });
  }

  function selectMode(mode) {
    state.agentMode = mode;
    localStorage.setItem(AGENT_MODE_KEY, mode);
    renderMode(mode);
    updateComposerModeState(mode);
    patchSessionAgentMode(mode);
    setMenuOpen(false, { pinned: false });
    modeTrigger.focus();
  }

  renderMode(state.agentMode);
  updateComposerModeState(state.agentMode);

  modeTrigger.addEventListener("click", () => {
    const open = !modeSelector.classList.contains("is-open");
    setMenuOpen(open, { pinned: open });
  });
  modeSelector.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-btn");
    if (btn) selectMode(btn.dataset.mode);
  });
  modeSelector.addEventListener("keydown", (e) => {
    const currentIndex = modeButtons.indexOf(document.activeElement);
    if (e.key === "Escape") {
      e.preventDefault();
      setMenuOpen(false, { pinned: false });
      modeTrigger.focus();
    } else if ((e.key === "Enter" || e.key === " ") && document.activeElement === modeTrigger) {
      e.preventDefault();
      const open = !modeSelector.classList.contains("is-open");
      setMenuOpen(open, { pinned: open, focusSelected: open });
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const nextIndex = e.key === "Home" ? 0 : e.key === "End" ? modeButtons.length - 1 : (currentIndex < 0 ? modeButtons.findIndex((btn) => btn.dataset.mode === state.agentMode) : currentIndex + (e.key === "ArrowDown" ? 1 : -1) + modeButtons.length) % modeButtons.length;
      setMenuOpen(true, { pinned: true });
      modeButtons[nextIndex].focus();
    } else if ((e.key === "Enter" || e.key === " ") && currentIndex >= 0) {
      e.preventDefault();
      selectMode(modeButtons[currentIndex].dataset.mode);
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (!modeSelector.contains(e.target)) setMenuOpen(false, { pinned: false });
  });
  modeSelector.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (!modeSelector.contains(document.activeElement)) setMenuOpen(false, { pinned: false });
    });
  });
}

resetBtn.addEventListener("click", () => {
  _doNewSession(state.defaultWorkdir || "");
});

async function _doNewSession(customWorkdir) {
  state.customWorkdir = customWorkdir;
  state.sessionId = newSessionId();
  state.activeSessionUserId = state.userId;
  state.sessionReady = false;
  updateSendButtonState();
  clearStoredSessionSelection();
  sessionIdEl.textContent = state.sessionId;
  sessionRuntime.resetTranscript();
  stepExecutionFeed.reset();
  state.sessionSummaries = {};
  state.summaryGeneratedFor = new Set();
  chatDisclosureController.clear();
  renderSessionBanner("");
  renderSessionFilesTree([]);
  clearCurrentUploads();
  remoteJobsController.reset();
  agentGraph.reset();
  planGraph.reset();
  hidePlanGraph();
  await createSession();
}
