import { graphNodeRevision, graphNodeSummary } from "./graphSummary.js";

const COLLAPSE_MOTION_MS = 240;

// Owns the inline executor cards rendered within an assistant timeline.
// Rendering collaborators are injected so this feed remains independent of
// the graph visualization and the chat presentation implementation.

function stepAttemptTimestamp(node = {}) {
  const raw = node.start_time ?? node.startTime;
  const numeric = Number(raw);
  if (raw !== "" && Number.isFinite(numeric)) {
    return numeric < 1e12 ? numeric * 1000 : numeric;
  }
  const parsed = raw ? new Date(raw).getTime() : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function stepAttemptSequence(node = {}) {
  const match = String(node.id || "").match(/^execution_(\d+)/);
  return match ? Number(match[1]) : 0;
}

export function compareStepAttempts(left, right) {
  const leftTime = stepAttemptTimestamp(left);
  const rightTime = stepAttemptTimestamp(right);
  if (leftTime !== null && rightTime !== null && leftTime !== rightTime) {
    return leftTime - rightTime;
  }
  const sequenceDifference = stepAttemptSequence(left) - stepAttemptSequence(right);
  if (sequenceDifference) return sequenceDifference;
  if (leftTime !== rightTime) return leftTime === null ? -1 : 1;
  return String(left?.id || "").localeCompare(String(right?.id || ""));
}

export class StepExecutionFeed {
  constructor(dependencies) {
    this._chatArea = dependencies.chatArea;
    this._isSending = dependencies.isSending;
    this._updatePreservingReadingPosition = dependencies.updatePreservingReadingPosition;
    this._createAgentAvatarEl = dependencies.createAgentAvatarEl;
    this._stepFeedTitle = dependencies.stepFeedTitle;
    this._stepFeedStatusIcon = dependencies.stepFeedStatusIcon;
    this._formatStepDuration = dependencies.formatStepDuration;
    this._renderStepInput = dependencies.renderStepInput;
    this._renderStepConversationEvent = dependencies.renderStepConversationEvent;
    this._renderStepToolCall = dependencies.renderStepToolCall;
    this._requestStepCancellation = dependencies.requestStepCancellation;
    this._createArtifactListItem = dependencies.createArtifactListItem;
    this._loadNodeDetail = dependencies.loadNodeDetail;
    this._cards = new Map();
    this._disclosures = dependencies.disclosureController;
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts = new Map();
    this._rootHostsByAction = new Map();
    this._stepById = new Map();
    this._childNodes = new Map();
    this._elapsedTimer = null;
  }

  reset({ preserveDisclosures = false } = {}) {
    this._stopElapsedTimer();
    // `_cards` is the ownership registry, not merely a render cache. Never
    // forget a card while leaving its DOM node behind: a later graph replay
    // would otherwise create a second bubble for the same graph node.
    for (const card of new Set(this._cards.values())) {
      this._disposeCard(card);
      card.remove();
    }
    this._cards.clear();
    if (!preserveDisclosures) this._disclosures.clear();
    this._highlightedId = null;
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts.clear();
    this._rootHostsByAction.clear();
    this._stepById = new Map();
    this._childNodes = new Map();
  }

  // Called before a virtual row or delegation host loses its DOM ownership.
  releaseWithin(host) {
    if (!host) return;
    this._disclosures?.capture?.(host);
    const cards = [...(host.querySelectorAll?.(".step-feed-message") || [])];
    if (host.classList?.contains("step-feed-message")) cards.unshift(host);
    // Unregister hosts before disposing bodies changes their ancestry.
    for (const hosts of [this._rootHosts, this._rootHostsByAction]) {
      for (const [key, element] of hosts || []) {
        if (element === host || host.contains?.(element)) hosts.delete(key);
      }
    }
    for (const card of cards) {
      const id = card.dataset?.stepNodeId;
      if (this._cards?.get(id) === card) this._cards.delete(id);
      this._disposeCard(card);
    }
    if (this._cards?.size === 0) this._stopElapsedTimer();
  }

  _disposeCard(card) {
    this._disclosures?.deletePrefix?.(`step:${card.dataset?.stepNodeId}:nested:`);
    if (card._stepClearTimer != null) clearTimeout(card._stepClearTimer);
    card._stepClearTimer = null;
    card._stepDetailRequest?.abort();
    card._stepDetailRequest = null;
    card._stepDetail = null;
    card._stepBodyKey = null;
    card.querySelector?.(".step-feed-body")?.remove();
    card._stepNode = null;
  }

  _unmountBody(outer) {
    outer._stepDetailRequest?.abort();
    outer._stepDetailRequest = null;
    outer._stepDetail = null;
    outer._stepBodyKey = null;
    const body = outer.querySelector(".step-feed-body");
    if (body) {
      this.releaseWithin(body);
      body.remove();
    }
    this._disclosures.deletePrefix(`step:${outer.dataset.stepNodeId}:nested:`);
  }

  _bodyVisible(outer) {
    if (!outer.isConnected) return false;
    let details = outer.querySelector(".step-feed-details");
    if (!details?.open) return false;
    details = outer.parentElement?.closest(".step-feed-details");
    while (details) {
      if (!details.open) return false;
      details = details.parentElement?.closest(".step-feed-details");
    }
    return this._cards.get(outer.dataset.stepNodeId) === outer;
  }

  _toggleBody(outer) {
    if (outer._stepClearTimer != null) clearTimeout(outer._stepClearTimer);
    outer._stepClearTimer = null;
    const details = outer.querySelector(".step-feed-details");
    if (details.open) {
      this._refreshBody(outer);
    } else {
      for (const card of [outer, ...outer.querySelectorAll(".step-feed-message")]) {
        card._stepDetailRequest?.abort();
        card._stepDetailRequest = null;
      }
      outer._stepClearTimer = setTimeout(() => {
        outer._stepClearTimer = null;
        if (!details.open) this._unmountBody(outer);
      }, COLLAPSE_MOTION_MS);
    }
  }

  async _refreshBody(outer) {
    if (!this._bodyVisible(outer)) return;
    const node = outer._stepNode;
    const key = graphNodeRevision(node);
    // At most one request per open card. If revisions arrive during a fetch,
    // finish it then request the newest revision, avoiding starvation on streams.
    if (outer._stepDetailRequest) return;
    if (outer._stepDetail && outer._stepBodyKey === key) {
      this._renderChildren(outer, node);
      return;
    }
    const controller = new AbortController();
    outer._stepDetailRequest = controller;
    if (!outer.querySelector(".step-feed-body")) {
      const body = document.createElement("div");
      body.className = "step-feed-body";
      body.textContent = "Loading task details…";
      outer.querySelector(".step-feed-details").appendChild(body);
    }
    try {
      const detail = this._loadNodeDetail ? await this._loadNodeDetail(node.id, controller.signal) : node;
      if (controller.signal.aborted || !this._bodyVisible(outer)) return;
      if (!detail || detail.id !== node.id) throw new Error("Invalid task detail");
      outer._stepDetail = detail;
      outer._stepBodyKey = key;
      this._updatePreservingReadingPosition(() => this._renderBody(outer, detail));
    } catch (error) {
      if (controller.signal.aborted || !this._bodyVisible(outer)) return;
      outer._stepDetail = null;
      outer._stepBodyKey = null;
      let body = outer.querySelector(".step-feed-body");
      if (!body) {
        body = document.createElement("div");
        body.className = "step-feed-body";
        outer.querySelector(".step-feed-details").appendChild(body);
      }
      this.releaseWithin(body);
      body.replaceChildren();
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Could not load task details. Retry";
      retry.addEventListener("click", () => this._refreshBody(outer));
      body.appendChild(retry);
    } finally {
      if (outer._stepDetailRequest === controller) {
        outer._stepDetailRequest = null;
        if (!controller.signal.aborted && key !== graphNodeRevision(outer._stepNode)) void this._refreshBody(outer);
      }
    }
  }

  captureDisclosureState() {
    this._disclosures.capture(this._chatArea);
  }

  startLiveTurn(anchorEl, startedAt = Date.now(), hostEl = null) {
    this._liveAnchorEl = anchorEl || null;
    this._liveStartedAt = startedAt;
    this._liveContainerEl = document.createElement("div");
    this._rootHosts.clear();
    this._rootHostsByAction.clear();

    // `hostEl` is the message timeline, used only to recover already-rendered
    // invocation slots. A graph node without a launcher slot stays detached;
    // guessing a visible fallback position is what made tasks jump later.
    hostEl?.querySelectorAll?.(".delegation-task-host[data-step-execution-key], .delegation-task-host[data-step-execution-action]").forEach((host) => {
      if (host.dataset.stepExecutionKey) this._rootHosts.set(host.dataset.stepExecutionKey, host);
      if (host.dataset.stepExecutionAction) this._rootHostsByAction.set(host.dataset.stepExecutionAction, host);
    });

    return this._liveContainerEl;
  }

  resumeLiveTurn(hostEl, startedAt = Date.now()) {
    if (!hostEl) return;
    this._liveAnchorEl = null;
    this._liveStartedAt = startedAt;
    this._liveContainerEl = document.createElement("div");
    this._rootHosts.clear();
    this._rootHostsByAction.clear();
    hostEl.querySelectorAll?.(".delegation-task-host[data-step-execution-key], .delegation-task-host[data-step-execution-action]").forEach((host) => {
      if (host.dataset.stepExecutionKey) this._rootHosts.set(host.dataset.stepExecutionKey, host);
      if (host.dataset.stepExecutionAction) this._rootHostsByAction.set(host.dataset.stepExecutionAction, host);
    });
    hostEl.querySelectorAll?.(".step-feed-message[data-step-node-id]").forEach((card) => {
      if (card._stepNode) this._cards.set(card.dataset.stepNodeId, card);
    });
    this._syncElapsedTimer();
  }

  bindRootHost(hostEl, executionKey = "", action = "") {
    const key = String(executionKey || "");
    const actionKey = String(action || "");
    if (!hostEl || (!key && !actionKey)) return false;
    if (key) {
      hostEl.dataset.stepExecutionKey = key;
      this._rootHosts.set(key, hostEl);
    }
    if (actionKey) {
      hostEl.dataset.stepExecutionAction = actionKey;
      this._rootHostsByAction.set(actionKey, hostEl);
    }
    const node = [...this._stepById.values()].find((candidate) => (
      (key && (this._nodeExecutionKey(candidate) === key || String(candidate.id || "").endsWith(`__node_${key}`)))
      || (!key && candidate?.input?.action === actionKey && this.isRootStep(candidate))
    ));
    if (node) {
      const card = this._ensureCard(node);
      this._insertIntoLiveContainer(hostEl, card, node);
      this._renderCardIfChanged(card, node);
      if (card.querySelector(".step-feed-details")?.open) void this._refreshBody(card);
    }
    return true;
  }

  finishLiveTurn() {
    this.releaseWithin(this._liveContainerEl);
    this._liveContainerEl?.replaceChildren();
    this._liveAnchorEl = null;
    this._liveContainerEl = null;
    this._liveStartedAt = null;
    this._rootHosts.clear();
    this._rootHostsByAction.clear();
  }

  update(graphData, patch = {}) {
    if (!graphData || typeof graphData.nodes !== "object") return;
    const hasLiveDestination = Boolean(this._liveStartedAt);
    // Rebuild the small hierarchy index for every graph snapshot, including
    // deltas. Parent nodes and children often arrive in separate updates; the
    // former incremental path updated node values but not topology, leaving a
    // child rendered once as a root and again beneath its eventual parent.
    const steps = Object.values(graphData.nodes)
      .filter((node) => node.type === "step")
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });
    this.setHierarchy(steps);
    const rootSteps = steps.filter((node) => this.isRootStep(node));

    this._updatePreservingReadingPosition(() => {
      rootSteps.forEach((node) => {
        if (this._cards.has(node.id) || (hasLiveDestination && this._isLiveStep(node))) this._upsert(node);
      });
      for (const [id, card] of [...this._cards]) {
        const node = this._stepById.get(id);
        if (!node) {
          this.releaseWithin(card);
          card.remove();
          continue;
        }
        // A late parent changes a former root into a child. Closed parents own
        // no descendant DOM; open parents will recreate the child in their body.
        if (!this.isRootStep(node) && card.parentElement?.closest(".step-feed-message")?.dataset.stepNodeId !== node.parent_id) {
          this.releaseWithin(card);
          card.remove();
          continue;
        }
        this._renderCardIfChanged(card, node);
      }
    });
    this._syncElapsedTimer();
  }

  setHierarchy(stepNodes) {
    const steps = Array.isArray(stepNodes) ? stepNodes.map(graphNodeSummary) : [];
    this._stepById = new Map(steps.map((node) => [node.id, node]));
    this._childNodes = new Map();

    steps.forEach((node) => {
      if (!this._stepById.has(node.parent_id)) return;
      const children = this._childNodes.get(node.parent_id) || [];
      children.push(node);
      this._childNodes.set(node.parent_id, children);
    });

    for (const children of this._childNodes.values()) {
      children.sort((a, b) => this._stepSortTime(a) - this._stepSortTime(b));
    }
  }

  isRootStep(node) {
    return !this._stepById.has(node?.parent_id);
  }

  _nodeExecutionKey(node) {
    const input = node?.input || {};
    return String(input.node_id || input.step_id || node?.id || "");
  }

  _rootHostForNode(node) {
    if (this._liveStartedAt && !this._isLiveStep(node)) return null;
    const directKey = this._nodeExecutionKey(node);
    let host = this._rootHosts.get(directKey);
    if (!host && node?.id) {
      const matchingKey = [...this._rootHosts.keys()].find((key) => String(node.id).endsWith(`__node_${key}`));
      if (matchingKey) host = this._rootHosts.get(matchingKey);
    }
    if (!host && this.isRootStep(node) && node?.input?.action) {
      host = this._rootHostsByAction.get(String(node.input.action));
    }
    return host?.isConnected ? host : null;
  }

  _isLiveStep(node) {
    if (!this._liveStartedAt) return true;
    if (!node.start_time) return node.status === "running";
    const startedAt = new Date(node.start_time).getTime();
    return Number.isFinite(startedAt) && startedAt >= this._liveStartedAt - 2000;
  }

  highlight(nodeId) {
    this._highlightedId = nodeId;
    for (const [id, card] of this._cards.entries()) {
      card.classList.toggle("step-feed-highlight", id === nodeId);
    }
    const card = this._cards.get(nodeId);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setTimeout(() => card.classList.remove("step-feed-highlight"), 1600);
    }
  }

  _upsert(node) {
    const outer = this._ensureCard(node);
    // Placement is idempotent and is part of reconciliation, not creation.
    // A node can become a child (or a root) after an incremental graph update;
    // checking only DOM presence left it in its former host indefinitely.
    this._placeCard(outer, node);
    this._renderCardIfChanged(outer, node);
  }

  appendStatic(node, container) {
    if (!container) {
      console.warn("StepExecutionFeed received a static card without a delegated-task host.");
      return null;
    }
    const outer = this._ensureCard(node);
    outer.classList.remove("step-feed-child-message");
    this._insertIntoLiveContainer(container, outer, node);
    this._renderCardIfChanged(outer, node);
    this._syncElapsedTimer();
    return outer;
  }

  _placeCard(outer, node) {
    outer.classList.remove("step-feed-child-message");
    const rootHost = this._rootHostForNode(node);
    if (rootHost) {
      this._insertIntoLiveContainer(rootHost, outer, node);
      return;
    }
    if (this._isSending() && this._liveContainerEl && this._isLiveStep(node)) {
      // This holding element is deliberately detached. The function-call
      // event will bind the node to its permanent chronological slot.
      this._insertIntoLiveContainer(this._liveContainerEl, outer, node);
      return;
    }

    // A reconnect snapshot can restore cards into an inline region before
    // graph polling resumes. Retain that restored in-bubble host instead of
    // moving the card back to the chat root when its position changes.
    const existingHost = outer.parentElement;
    if (existingHost && existingHost !== this._chatArea && this._chatArea.contains(existingHost)) {
      this._insertIntoLiveContainer(existingHost, outer, node);
      return;
    }
    // A step card has no valid presentation at the chat root.  Historical
    // rendering supplies an inline host, while a live turn supplies its
    // dedicated Delegated tasks host above.  Keeping an orphan detached is
    // preferable to silently rendering it as a sibling of the chat bubble.
  }

  _stepSortTime(node) {
    return node?.start_time ? new Date(node.start_time).getTime() : Infinity;
  }

  _upsertNested(node, container, ancestors) {
    const outer = this._ensureCard(node);
    outer.classList.add("step-feed-child-message");
    this._insertIntoLiveContainer(container, outer, node);
    this._renderCardIfChanged(outer, node, ancestors);
    return outer;
  }

  _ensureCard(node) {
    const nodeId = String(node?.id || "");
    let outer = this._cards.get(nodeId) || null;
    if (!outer) outer = this._createCard(node);
    this._cards.set(nodeId, outer);
    return outer;
  }

  _renderKey(node) {
    return [graphNodeRevision(node), node.summary, node.label,
      ...(this._childNodes.get(node.id) || []).map(graphNodeRevision)].join("|");
  }

  _renderCardIfChanged(outer, node, ancestors = outer._stepAncestors || new Set([node.id])) {
    const renderKey = this._renderKey(node);
    if (outer._stepRenderKey === renderKey) return;
    outer._stepRenderKey = renderKey;
    outer._stepAncestors = ancestors;
    this._renderCard(outer, graphNodeSummary(node));
  }

  _insertIntoLiveContainer(container, outer, node) {
    if (!this._reconcileDelegationAttempt(container, outer, node)) return;
    const delegationGroup = container.closest?.(".delegation-group");
    if (delegationGroup) {
      delegationGroup.hidden = false;
      delegationGroup.closest(".agent-message")?.classList.remove("is-pending", "is-waiting");
    }
    const newTime = this._stepSortTime(node);
    outer.dataset.stepStartTime = String(newTime);

    let followingCard = null;
    for (const el of [...container.children]) {
      if (el === outer) continue;
      if (!el.dataset.stepStartTime) continue;
      if (newTime < Number(el.dataset.stepStartTime)) {
        followingCard = el;
        break;
      }
    }
    if (followingCard) {
      // Avoid reinserting a card that is already in its sorted position. In a
      // live message this method runs with every text render; needless DOM
      // moves restart the card's entry animation and look like a flash.
      if (outer.parentElement !== container || outer.nextElementSibling !== followingCard) {
        container.insertBefore(outer, followingCard);
      }
      return;
    }

    // The card is already correctly placed after all earlier cards. Leave it
    // alone so streamed assistant prose does not repeatedly remount it.
    if (outer.parentElement !== container) container.appendChild(outer);
  }

  _reconcileDelegationAttempt(container, outer, node) {
    if (!container.classList?.contains("delegation-task-host")) return true;
    const attempts = [...container.children].filter((element) => (
      element !== outer && element.classList?.contains("step-feed-message")
    ));
    const newestExisting = attempts.reduce((newest, element) => {
      if (!newest) return element;
      return compareStepAttempts(this._attemptNode(element), this._attemptNode(newest)) > 0
        ? element
        : newest;
    }, null);
    if (newestExisting && compareStepAttempts(this._attemptNode(newestExisting), node) > 0) {
      this.releaseWithin(outer);
      outer.remove();
      attempts.forEach((element) => {
        if (element !== newestExisting) { this.releaseWithin(element); element.remove(); }
      });
      return false;
    }
    attempts.forEach((element) => { this.releaseWithin(element); element.remove(); });
    return true;
  }

  _attemptNode(element) {
    return element?._stepNode || {
      id: element?.dataset?.stepNodeId,
      startTime: element?.dataset?.stepStartTime,
    };
  }

  _createCard(node) {
    const outer = document.createElement("div");
    outer.className = "message agent-message step-feed-message is-entering";
    const clearEntryAnimation = (event) => {
      if (event.target !== outer) return;
      outer.classList.remove("is-entering");
      outer.removeEventListener("animationend", clearEntryAnimation);
    };
    outer.addEventListener("animationend", clearEntryAnimation);
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStartTime = node.start_time ? String(new Date(node.start_time).getTime()) : "";
    outer.appendChild(this._createAgentAvatarEl());

    const bubble = document.createElement("div");
    bubble.className = "message-bubble step-feed-bubble";
    const details = document.createElement("details");
    details.className = "step-feed-details";
    this._disclosures.wire(details, `step:${node.id}:card`, {
      defaultOpen: false,
    });
    details.addEventListener("toggle", (event) => {
      if (event.target === details) this._toggleBody(outer);
    });
    bubble.appendChild(details);
    outer.appendChild(bubble);
    return outer;
  }

  _wireNested(nodeId, key, element) {
    if (element?.tagName !== "DETAILS") return element;
    element.dataset.stepNestedKey = key;
    this._disclosures.wire(element, `step:${nodeId}:nested:${key}`);
    return element;
  }

  _renderCard(outer, node) {
    const previousStatus = outer._stepNode?.status;
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStatus = node.status || "idle";
    outer._stepNode = node;
    outer.classList.toggle("step-feed-highlight", this._highlightedId === node.id);

    const details = outer.querySelector(".step-feed-details");
    const cardKey = `step:${node.id}:card`;
    const isRunning = node.status === "running";
    // Completed tasks default closed. Preserve an explicit historical open
    // choice, but compact once when a running task reaches its terminal state.
    if (previousStatus === "running" && !isRunning) {
      this._disclosures.state.delete(cardKey);
      details.open = false;
      this._toggleBody(outer);
    }
    details.querySelector(":scope > summary")?.remove();

    const summary = document.createElement("summary");
    summary.className = "step-feed-summary";
    const titleInfo = this._stepFeedTitle(node);
    const title = document.createElement("span");
    title.className = "step-feed-title";
    const task = document.createElement("span");
    task.className = "step-feed-task";
    task.textContent = titleInfo.action;
    title.appendChild(task);
    if (titleInfo.identifier) {
      const identity = document.createElement("span");
      identity.className = "step-feed-identity";
      identity.textContent = `Sub-agent · ${titleInfo.identifier}`;
      title.appendChild(identity);
    }
    const status = document.createElement("span");
    status.className = `step-feed-status step-feed-status-${node.status || "idle"}`;
    status.textContent = this._stepFeedStatusIcon(node.status);
    status.title = node.status || "idle";
    const meta = document.createElement("span");
    meta.className = "step-feed-meta";
    meta.textContent = this._formatStepDuration(node);
    if (isRunning && node.start_time) meta.title = "Elapsed time";
    summary.append(status, title, meta);

    const stepNumber = node.input && node.input.step_number;
    if (node.status === "running" && stepNumber !== undefined && stepNumber !== null) {
      const stopBtn = document.createElement("button");
      stopBtn.type = "button";
      stopBtn.className = "step-feed-stop-btn";
      stopBtn.textContent = "Stop";
      stopBtn.title = `Stop step ${stepNumber}`;
      stopBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        stopBtn.disabled = true;
        stopBtn.textContent = "Stopping…";
        await this._requestStepCancellation(stepNumber);
      });
      summary.appendChild(stopBtn);
    }
    details.prepend(summary);
    if (details.open) void this._refreshBody(outer);
  }

  _renderBody(outer, node) {
    const details = outer.querySelector(".step-feed-details");
    const previousBody = details.querySelector(":scope > .step-feed-body");
    // Preserve nested cards while this node streams; each has its own revision.
    const children = previousBody?.querySelector(":scope > .step-feed-child-section");
    if (previousBody) this._disclosures.capture(previousBody);
    const body = document.createElement("div");
    body.className = "step-feed-body";

    if (node.summary) {
      const p = document.createElement("div");
      p.className = "step-feed-node-summary";
      p.textContent = node.summary;
      body.appendChild(p);
    }

    if (node.input && Object.keys(node.input).length) {
      body.appendChild(this._wireNested(node.id, "input", this._renderStepInput(node.input)));
    }

    const activityItems = this._activityStream(node);
    if (activityItems.length) {
      const activity = document.createElement("div");
      activity.className = "step-feed-activity-list agent-activity-action-list";
      activityItems.forEach((item) => {
        if (item.kind === "conversation") {
          const { event, index } = item;
          const key = `conversation:${index}:${event.timestamp || ""}:${event.type || ""}:${event.author || ""}`;
          activity.appendChild(this._wireNested(node.id, key, this._renderStepConversationEvent(event, {
            collapsed: node.status !== "running",
            timelineId: `step:${node.id}:${key}`,
          })));
          return;
        }
        const { toolCall, index } = item;
        const key = `tool:${index}:${toolCall.name || ""}:${toolCall.start_time || ""}`;
        activity.appendChild(this._wireNested(node.id, key, this._renderStepToolCall(toolCall)));
      });
      body.appendChild(activity);
    }

    const artifacts = node.artifacts || [];
    if (artifacts.length) {
      const section = document.createElement("div");
      section.className = "step-feed-section";
      const label = document.createElement("div");
      label.className = "step-feed-section-title";
      label.textContent = "Artifacts";
      const list = document.createElement("ul");
      list.className = "detail-artifacts step-feed-artifacts";
      artifacts.forEach((artifact) => {
        list.appendChild(this._createArtifactListItem(artifact));
      });
      section.append(label, list);
      body.appendChild(section);
    }

    if (children) body.appendChild(children);
    if (!body.childElementCount) {
      const empty = document.createElement("div");
      empty.className = "step-feed-empty";
      empty.textContent = "Waiting for step executor events…";
      body.appendChild(empty);
    }

    previousBody?.remove();
    details.appendChild(body);
    this._renderChildren(outer, node);
  }

  _renderChildren(outer, node) {
    const ancestors = outer._stepAncestors || new Set([node.id]);
    const body = outer.querySelector(".step-feed-body");
    const childNodes = (this._childNodes.get(node.id) || [])
      .filter((child) => !ancestors.has(child.id));
    if (childNodes.length) {
      let section = body?.querySelector(":scope > .step-feed-child-section");
      if (!section) {
        section = document.createElement("div");
        section.className = "step-feed-section step-feed-child-section";
        const label = document.createElement("div");
        label.className = "step-feed-section-title";
        const childHost = document.createElement("div");
        childHost.className = "step-feed-child-list";
        section.append(label, childHost);
        body?.appendChild(section);
      }
      const label = section.querySelector(":scope > .step-feed-section-title");
      label.textContent = `Sub-executors (${childNodes.length})`;
      const childHost = section.querySelector(":scope > .step-feed-child-list");

      for (const card of [...childHost.children]) {
        if (!childNodes.some((child) => child.id === card.dataset.stepNodeId)) {
          this.releaseWithin(card);
          card.remove();
        }
      }
      childNodes.forEach((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        this._upsertNested(child, childHost, nextAncestors);
      });
    } else {
      const section = body?.querySelector(":scope > .step-feed-child-section");
      this.releaseWithin(section);
      section?.remove();
    }

  }

  _syncElapsedTimer() {
    const hasTimedRunningCard = [...this._cards.values()].some((outer) => (
      outer.isConnected
      && outer.dataset.stepStatus === "running"
      && Number.isFinite(new Date(outer._stepNode?.start_time || "").getTime())
    ));

    if (hasTimedRunningCard) {
      this._refreshRunningDurations();
      if (this._elapsedTimer === null) {
        this._elapsedTimer = window.setInterval(() => this._refreshRunningDurations(), 1000);
      }
      return;
    }
    this._stopElapsedTimer();
  }

  _refreshRunningDurations() {
    for (const outer of this._cards.values()) {
      if (!outer.isConnected || outer.dataset.stepStatus !== "running") continue;
      const node = outer._stepNode;
      if (!node?.start_time) continue;
      const meta = outer.querySelector(".step-feed-meta");
      if (meta) meta.textContent = this._formatStepDuration(node);
    }
  }

  _stopElapsedTimer() {
    if (this._elapsedTimer === null) return;
    window.clearInterval(this._elapsedTimer);
    this._elapsedTimer = null;
  }

  _activityStream(node) {
    const toolCalls = node.tool_calls || [];
    const toolMatchesConversationEvent = (event) => {
      if (!["function_call", "function_response"].includes(event.type)) return false;
      const content = String(event.content || "");
      return toolCalls.some((toolCall) => {
        const name = toolCall.name || "";
        return name && (content.startsWith(`${name}(`) || content.startsWith(`${name} →`));
      });
    };
    const timeValue = (value) => {
      const time = new Date(value || "").getTime();
      return Number.isFinite(time) ? time : null;
    };
    const items = [
      ...(node.conversation || [])
        .filter((event) => !toolMatchesConversationEvent(event))
        .map((event, index) => ({ kind: "conversation", event, index, time: timeValue(event.timestamp), sequence: index })),
      ...toolCalls.map((toolCall, index) => ({
        kind: "tool",
        toolCall,
        index,
        time: timeValue(toolCall.start_time || toolCall.end_time),
        sequence: (node.conversation || []).length + index,
      })),
    ];
    return items.sort((a, b) => {
      if (a.time !== null && b.time !== null && a.time !== b.time) return a.time - b.time;
      if (a.time !== null && b.time === null) return -1;
      if (a.time === null && b.time !== null) return 1;
      return a.sequence - b.sequence;
    });
  }
}
