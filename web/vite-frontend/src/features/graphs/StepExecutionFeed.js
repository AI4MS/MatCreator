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
    for (const card of new Set(this._cards.values())) card.remove();
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
    const card = node && this._cards.get(node.id);
    if (node && card) this._insertIntoLiveContainer(hostEl, card, node);
    return true;
  }

  finishLiveTurn() {
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
      .filter((node) => !hasLiveDestination || this._isLiveStep(node))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });
    this.setHierarchy(steps);
    const rootSteps = steps.filter((node) => this.isRootStep(node));

    this._updatePreservingReadingPosition(() => {
      rootSteps.forEach((node) => this._upsert(node));
    });
    this._syncElapsedTimer();
  }

  setHierarchy(stepNodes) {
    const steps = Array.isArray(stepNodes) ? stepNodes : [];
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
    if (this._isSending() && this._liveContainerEl) {
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

  _renderKey(node, ancestors = new Set([node.id])) {
    const children = (this._childNodes.get(node.id) || [])
      .filter((child) => !ancestors.has(child.id))
      .map((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        return this._renderKey(child, nextAncestors);
      });
    return JSON.stringify({
      status: node.status,
      startTime: node.start_time,
      endTime: node.end_time,
      summary: node.summary,
      input: node.input,
      conversation: node.conversation,
      toolCalls: node.tool_calls,
      artifacts: node.artifacts,
      children,
    });
  }

  _renderCardIfChanged(outer, node, ancestors = new Set([node.id])) {
    const renderKey = this._renderKey(node, ancestors);
    if (outer._stepRenderKey === renderKey) return;
    outer._stepRenderKey = renderKey;
    this._renderCard(outer, node, ancestors);
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
      outer.remove();
      attempts.forEach((element) => {
        if (element !== newestExisting) element.remove();
      });
      return false;
    }
    attempts.forEach((element) => element.remove());
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

  _renderCard(outer, node, ancestors = new Set([node.id])) {
    outer.dataset.stepNodeId = node.id;
    outer.dataset.stepStatus = node.status || "idle";
    outer._stepNode = node;
    outer.classList.toggle("step-feed-highlight", this._highlightedId === node.id);

    const bubble = outer.querySelector(".step-feed-bubble");

    const details = outer.querySelector(".step-feed-details");
    const cardKey = `step:${node.id}:card`;
    const isRunning = node.status === "running";
    if (!isRunning) this._disclosures.state.delete(cardKey);
    const userChoice = this._disclosures.state.get(cardKey);
    // Start compact even while work is live. A reader can explicitly open a
    // card for its activity stream; completed cards always compact again.
    details.open = isRunning && userChoice === true;
    details.innerHTML = "";

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
    details.appendChild(summary);

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

    const childNodes = (this._childNodes.get(node.id) || [])
      .filter((child) => !ancestors.has(child.id));
    if (childNodes.length) {
      let section = bubble?.querySelector(":scope > .step-feed-child-section");
      if (!section) {
        section = document.createElement("div");
        section.className = "step-feed-section step-feed-child-section";
        const label = document.createElement("div");
        label.className = "step-feed-section-title";
        const childHost = document.createElement("div");
        childHost.className = "step-feed-child-list";
        section.append(label, childHost);
        bubble?.appendChild(section);
      }
      const label = section.querySelector(":scope > .step-feed-section-title");
      label.textContent = `Sub-executors (${childNodes.length})`;
      const childHost = section.querySelector(":scope > .step-feed-child-list");

      childNodes.forEach((child) => {
        const nextAncestors = new Set(ancestors);
        nextAncestors.add(child.id);
        this._upsertNested(child, childHost, nextAncestors);
      });
    } else {
      bubble?.querySelector(":scope > .step-feed-child-section")?.remove();
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

    if (!body.childElementCount) {
      const empty = document.createElement("div");
      empty.className = "step-feed-empty";
      empty.textContent = "Waiting for step executor events…";
      body.appendChild(empty);
    }

    details.appendChild(body);
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
