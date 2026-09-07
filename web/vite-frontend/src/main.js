import { createChatRenderer } from "./features/chat/rendering.js";
import { createMessageStreamController } from "./features/chat/messageStream.js";
import { createSmoothTextReveal } from "./features/chat/textReveal.js";
import { createActivityRenderer } from "./features/chat/activityRenderer.js";
import { createTimelineRenderer } from "./features/chat/timelineRenderer.js";
import {
  createStepFeedRenderer,
  formatStepDuration,
  stepFeedStatusIcon,
  stepFeedTitle,
} from "./features/chat/stepFeedRenderer.js";
import { createPlanApprovalRenderer } from "./features/chat/planApproval.js";
import { createComposerModeController } from "./features/chat/ComposerModeController.js";
import { createAgentStatusController } from "./features/chat/AgentStatusController.js";
import { createLayoutController } from "./features/layout/resizers.js";
import { createImageLightbox } from "./features/media/imageLightbox.js";
import { classifyPath, createSessionFileTree } from "./features/session/fileTree.js";
import { createTimelineArtifactRenderer } from "./features/chat/timelineArtifacts.js";
import { createSessionListController } from "./features/session/sessionList.js";
import { createSessionDetailsController } from "./features/session/sessionDetails.js";
import { createSessionSummaryController } from "./features/session/SessionSummaryController.js";
import { createSessionCoordinator } from "./features/session/SessionCoordinator.js";
import { createSessionRuntime } from "./features/session/runtime.js";
import {
  createSessionUploadsController,
  displayMessageFromStoredUserText,
} from "./features/session/uploads.js";
import {
  createSessionId as newSessionId,
  createSessionRequestKey,
  managedRunEventsUrl,
  shouldRefreshPlanGraphForTool,
  workspaceFileUrl,
} from "./features/session/sessionUtils.js";
import {
  findConversationRequest,
  finishRequestCleanup,
  requestHasActiveRun,
  requestPresentsLiveTurn,
} from "./features/session/requestLifecycle.js";
import { createWorkspaceTerminalController } from "./features/workspace/terminal.js";
import { createWorkspaceViewerController } from "./features/workspace/WorkspaceViewerController.js";
import { AgentGraphView } from "./features/graphs/AgentGraphView.js";
import { StepExecutionFeed } from "./features/graphs/StepExecutionFeed.js";
import { ExecutionPlanView } from "./features/graphs/ExecutionPlanView.js";
import { createPlanGraphPopupController } from "./features/graphs/planGraphPopup.js";
import { createSkillGraphController } from "./features/skills/SkillGraphController.js";
import { createKnowledgeReviewController } from "./features/skills/KnowledgeReviewController.js";
import { createSettingsController } from "./features/settings/SettingsController.js";
import { createEvaluationController } from "./features/evaluation/EvaluationController.js";
import { createRemoteJobsController } from "./features/remoteJobs/RemoteJobsController.js";
import { createAuthController } from "./features/auth/AuthController.js";
import { mountOrbitalAgentIndicator } from "./components/mountOrbitalAgentIndicator.js";
import { createDisclosureController } from "./features/ui/disclosureState.js";
import { createAppearanceController, THEME_KEY } from "./features/ui/appearance.js";
import { removeOverlayWithMotion } from "./shared/ui/overlayMotion.js";
import { showConfirmDialog } from "./shared/ui/confirmDialog.js";
import {
  getFunctionResponse,
} from "./features/chat/timelinePresentation.js";
import "./styles/index.css";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const APP_NAME = "MatCreator";

const AGENT_MODE_KEY = "mat_agentMode";
const SESSION_ID_KEY = "mat_sessionId";
const SESSION_OWNER_KEY = "mat_sessionOwnerId";

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
const modeSelector = document.getElementById("mode-selector");
const modeTrigger = document.getElementById("mode-trigger");
const modeMenu = document.getElementById("mode-menu");
const sessionSummaryText = document.getElementById("session-summary-text");
const chatTab = document.getElementById("tab-chat");
const filesColToggleBtn = document.getElementById("files-col-toggle");
const workspaceCli = document.getElementById("workspace-cli");
const workspaceTerminalEl = document.getElementById("workspace-terminal");
let workspaceViewerController = null;
let sessionCoordinator = null;

const knowledgeReviewController = createKnowledgeReviewController({
  getSessionId: () => state.sessionId,
});
const knowledgeReviewBanner = knowledgeReviewController.element;

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

const appearanceController = createAppearanceController({
  state,
  textInput,
  themeToggle,
});
const {
  getFontScale,
  applyFontScale,
  autoResizeTextInput,
} = appearanceController;
appearanceController.init();

const createChatDisclosureController = () => createDisclosureController();
const chatDisclosureController = createChatDisclosureController();

const authController = createAuthController({
  state,
  elements: {
    loginModal,
    loginInput,
    loginPassword,
    loginError,
    loginUuidDisplay,
    loginSubmit,
    loginView,
    registerView,
    registerInput: regInput,
    registerPassword: regPassword,
    registerConfirm: regConfirm,
    registerError: regError,
    registerSubmit: regSubmit,
    switchToRegister,
    switchToLogin,
    userDisplay,
    editUserButton: editUserBtn,
    logoutButton: logoutBtn,
    settingsLogoutButton: settingsLogoutBtn,
    savePasswordButton: document.getElementById("settings-save-password-btn"),
    passwordMessage: document.getElementById("settings-password-msg"),
    currentPasswordInput: document.getElementById("settings-current-password"),
    newPasswordInput: document.getElementById("settings-new-password"),
    confirmPasswordInput: document.getElementById("settings-confirm-password"),
    passwordSection: document.getElementById("settings-save-password-btn")?.parentElement,
    sessionIdElement: sessionIdEl,
    sessionListElement: sessionListEl,
  },
  sessionStorageKeys: {
    sessionId: SESSION_ID_KEY,
    owner: SESSION_OWNER_KEY,
  },
  createSessionId: newSessionId,
  clearSessionSelection: clearStoredSessionSelection,
  updateSendButtonState,
  loadSessions: (options) => loadSessions(options),
  switchSession: (...args) => switchSession(...args),
  onLoggedOut: () => {
    sessionRuntime.resetTranscript();
    stepExecutionFeed.reset();
    chatDisclosureController.clear();
    state.sessionViewCache.clear();
    sessionListEl.replaceChildren();
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Sign in to see sessions";
    sessionListEl.appendChild(empty);
    renderSessionFilesTree([]);
    clearCurrentUploads();
    remoteJobsController.reset();
    agentGraph.reset();
    planGraph.reset();
    hidePlanGraph();
    settingsController.close();
  },
  onSessionApplied: () => {
    sessionRuntime.resetTranscript();
    stepExecutionFeed.reset();
    renderSessionFilesTree([]);
    clearCurrentUploads();
    remoteJobsController.reset();
    agentGraph.reset();
    planGraph.reset();
    hidePlanGraph();
    layoutController.refresh();
    void loadSessions();
  },
});
const showLoginModal = authController.showLogin;
const applyLogin = authController.login;
const canWriteActiveSession = authController.canWriteActiveSession;
const activeSessionBackendUserId = authController.activeSessionBackendUserId;

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
knowledgeReviewController.init();

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

sessionIdEl.textContent = state.sessionId;
if (state.userId) userDisplay.textContent = state.displayName || state.userId;

const evaluationController = createEvaluationController({
  state,
  activateCenterTab,
  switchSession,
  removeOverlayWithMotion,
});

const sessionDetailsController = createSessionDetailsController({
  getStatus: sessionDisplayStatus,
});

let stepExecutionFeed;
const activityRenderer = createActivityRenderer({
  setMarkdownContent,
  getActiveRequest: activeSessionRequest,
  getStepExecutionFeed: () => stepExecutionFeed,
});
const stepFeedRenderer = createStepFeedRenderer({
  activityRenderer,
  createStructureViewButtonGroup,
});
const {
  renderTimeline,
  addAgentTimelineMessage,
} = createTimelineRenderer({
  activityRenderer,
  disclosureController: chatDisclosureController,
  setMarkdownContent,
  updatePreservingReadingPosition,
  createTimelineImage,
  createStructureViewButtonGroup,
  createAgentAvatarEl,
  appendLiveTurnChild,
  chatArea,
});
const addPlanApprovalActions = createPlanApprovalRenderer({
  sendMessage: (message) => messageStreamController.send(message),
  scrollToBottom,
});

// ---------------------------------------------------------------------------
// Agent Graph Visualization
// ---------------------------------------------------------------------------

stepExecutionFeed = new StepExecutionFeed({
  chatArea,
  isSending: () => requestHasActiveRun(activeSessionRequest()),
  updatePreservingReadingPosition,
  createAgentAvatarEl,
  stepFeedTitle,
  stepFeedStatusIcon,
  formatStepDuration,
  renderStepInput: stepFeedRenderer.renderStepInput,
  renderStepConversationEvent: stepFeedRenderer.renderStepConversationEvent,
  renderStepToolCall: stepFeedRenderer.renderStepToolCall,
  requestStepCancellation,
  createArtifactListItem,
  disclosureController: chatDisclosureController,
});
const agentGraph = new AgentGraphView("agent-graph", {
  stepExecutionFeed,
  graphViewport,
  requestStepCancellation,
  createArtifactListItem,
  renderStepConversationEvent: stepFeedRenderer.renderStepConversationEvent,
  renderStepToolCall: stepFeedRenderer.renderStepToolCall,
  syncPanelResizerVisibility: () => layoutController.syncPanelResizerVisibility(),
});
const planGraph = new ExecutionPlanView("plan-graph-canvas", {
  toggleButton: document.getElementById("plan-graph-toggle"),
  thumbnailElement: document.getElementById("plan-graph-thumbnail"),
});
const planGraphPopupController = createPlanGraphPopupController({
  planGraph,
  popup: document.getElementById("plan-graph-popup"),
  toggleButton: document.getElementById("plan-graph-toggle"),
  closeButton: document.getElementById("plan-graph-close"),
  zoomInButton: document.getElementById("plan-graph-zoom-in"),
  zoomOutButton: document.getElementById("plan-graph-zoom-out"),
  fitButton: document.getElementById("plan-graph-fit"),
  previousButton: document.getElementById("plan-graph-prev"),
  nextButton: document.getElementById("plan-graph-next"),
});
const showPlanGraph = planGraphPopupController.show;
const hidePlanGraph = planGraphPopupController.hide;
planGraphPopupController.init();
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

function sessionRequestKey(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
  return createSessionRequestKey(sessionId, owner);
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
const agentStatusController = createAgentStatusController({
  chatArea,
  runningIndicator: agentRunningIndicator,
  runningText: agentRunningText,
  sendButton: sendBtn,
  orbitalIndicator,
  getActiveRequest: activeSessionRequest,
  requestHasActiveRun,
  requestPresentsLiveTurn,
});

function attachAgentRunningIndicator(messageView) {
  agentStatusController.attach(messageView);
}

function updateAgentRunningStatus(phase = "working") {
  agentStatusController.updatePhase(phase);
}

function updateSendButtonState() {
  agentStatusController.updateSendButton();
}

function storeSessionSelection(sessionId, owner) {
  localStorage.setItem(SESSION_ID_KEY, sessionId);
  localStorage.setItem(SESSION_OWNER_KEY, owner);
}

function clearStoredSessionSelection() {
  localStorage.removeItem(SESSION_ID_KEY);
  localStorage.removeItem(SESSION_OWNER_KEY);
}

// Authentication and startup restoration are owned by AuthController.

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

const sessionSummaryController = createSessionSummaryController({
  state,
  summaryElement: sessionSummaryText,
  tabElement: chatTab,
  createTextReveal: createSmoothTextReveal,
  rerenderSessionList,
});
const {
  render: renderSessionBanner,
  generate: generateSessionSummary,
} = sessionSummaryController;
sessionSummaryController.init();

function sessionDisplayStatus(session, owner) {
  return sessionCoordinator?.displayStatus(session, owner) || "idle";
}

function switchSession(sessionId, owner = state.userId, options = {}) {
  return sessionCoordinator?.switchSession(sessionId, owner, options);
}

function deleteSession(sessionId) {
  return sessionCoordinator?.deleteSession(sessionId);
}

function downloadSessionLog(sessionId, owner = state.userId) {
  return sessionCoordinator?.downloadSessionLog(sessionId, owner);
}

document.getElementById("refresh-files")?.addEventListener("click", (event) => {
  event.stopPropagation();
  void refreshSessionFiles();
});

// ---------------------------------------------------------------------------
// File path → API URL conversion
// ---------------------------------------------------------------------------

function pathToApiUrl(path) {
  return workspaceFileUrl(path, state.sessionId);
}

// Chat presentation is composed above from focused activity, timeline,
// step-feed, payload, and approval renderers.
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

function refreshSessionFiles(sessionId = state.sessionId, owner = state.activeSessionUserId || state.userId) {
  return sessionCoordinator?.refreshFiles(sessionId, owner);
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

const sessionUploadsController = createSessionUploadsController({
  state,
  elements: {
    button: fileUploadBtn,
    input: fileUploadInput,
    status: uploadStatus,
  },
  ensureSession: createSession,
  refreshFiles: refreshSessionFiles,
  showLogin: showLoginModal,
  canWrite: canWriteActiveSession,
  showReadOnlyMessage: () => addMessage(
    "agent",
    `Admin view is read-only for ${state.activeSessionUserId}'s session.`,
    undefined,
    sessionRuntime.getLiveHost(),
  ),
});
const {
  clear: clearCurrentUploads,
  messageWithUploadContext,
  messageWithUploadNames,
} = sessionUploadsController;

sessionCoordinator = createSessionCoordinator({
  state,
  appName: APP_NAME,
  sessionIdElement: sessionIdEl,
  createSessionId: newSessionId,
  sessionRequestKey,
  activeSessionRequest,
  requestHasActiveRun,
  activeSessionBackendUserId,
  updateSendButtonState,
  storeSessionSelection,
  clearSessionSelection: clearStoredSessionSelection,
  loadSessions: (...args) => loadSessions(...args),
  getSessionRuntime: () => sessionRuntime,
  stepExecutionFeed,
  renderSessionFilesTree,
  clearCurrentUploads,
  remoteJobsController,
  agentGraph,
  planGraph,
  hidePlanGraph,
  clearDisclosures: () => chatDisclosureController.clear(),
  renderSessionBanner,
  showConfirmDialog,
});

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

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

function createSession() {
  return sessionCoordinator?.createSession();
}

function patchSessionAgentMode(mode) {
  return sessionCoordinator?.patchAgentMode(mode);
}

// ---------------------------------------------------------------------------
// Workspace structure and file viewers
// ---------------------------------------------------------------------------

const lightbox = createImageLightbox();
workspaceViewerController = createWorkspaceViewerController({
  state,
  elements: {
    tabs: centerTabs,
    tabsScroll: centerTabsScroll,
    panels: centerTabPanels,
    graphDetail,
    fileModal: document.getElementById("file-viewer-modal"),
    fileContent: document.getElementById("fv-content"),
    fileName: document.getElementById("fv-filename"),
    fileCloseButton: document.getElementById("fv-close"),
  },
  skillGraphController,
  lightbox,
  classifyPath,
  pathToApiUrl,
  refreshSessionFiles,
});
workspaceViewerController.init();

function activateCenterTab(tabId) {
  workspaceViewerController?.activate(tabId);
}

function openViewer(item) {
  return workspaceViewerController?.openStructure(item);
}

function openFileViewer(file) {
  return workspaceViewerController?.openFile(file);
}

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

sessionUploadsController.init();

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
const composerModeController = createComposerModeController({
  state,
  storageKey: AGENT_MODE_KEY,
  selector: modeSelector,
  trigger: modeTrigger,
  menu: modeMenu,
  inputContainer,
  onModeChanged: patchSessionAgentMode,
});
composerModeController.init();

resetBtn.addEventListener("click", () => {
  _doNewSession(state.defaultWorkdir || "");
});

function _doNewSession(customWorkdir) {
  return sessionCoordinator?.createNew(customWorkdir);
}

authController.init();
void authController.start();
