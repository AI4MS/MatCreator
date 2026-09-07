import {
  formatAgentDuration,
  getPlotPaths,
  getStructurePaths,
  normalizeAgentTimestamp,
  timelineSegments,
} from "./timelinePresentation.js";

export function segmentArtifacts(segment, claimedPlotPaths) {
  if (segment.type !== "activity" && segment.type !== "delegation") {
    return { plotPaths: [], structurePaths: [] };
  }
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

export function createTimelineRenderer({
  activityRenderer,
  disclosureController,
  setMarkdownContent,
  updatePreservingReadingPosition,
  createTimelineImage,
  createStructureViewButtonGroup,
  createAgentAvatarEl,
  appendLiveTurnChild,
  chatArea,
}) {
  const {
    createAgentActivity,
    createDelegationGroupShell,
    reconcileDelegationGroup,
  } = activityRenderer;

  function renderTimelineSegment(segment, context) {
    const wrapper = document.createElement("div");
    wrapper.className = "timeline-segment";
    wrapper.dataset.timelineSegment = segment.key;
    if (segment.type === "text") {
      const item = segment.items[0];
      const content = document.createElement("div");
      content.className = "markdown-content";
      setMarkdownContent(content, item.text || "", {
        cache: context.cacheMarkdown,
        defer: context.deferMarkdown,
        anchorPrefix: context.completed
          ? `${context.disclosurePrefix}${item.timelineId || "text:legacy"}:content`
          : "",
      });
      wrapper.appendChild(content);
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

  function renderTimeline(view, message, shownPlotPaths = null) {
    const { timelineElement: container, element: agentMessage } = view;
    const disclosurePrefix = `timeline:${message.id}:`;
    const liveDisclosureKeys = new Set();
    const wireTimelineDetails = (details, key, defaultOpen = false) => {
      const scopedKey = `${disclosurePrefix}${key}`;
      liveDisclosureKeys.add(scopedKey);
      disclosureController.wire(details, scopedKey, { defaultOpen });
      return scopedKey;
    };

    const updateTimeline = () => {
      const previousSegments = container._timelineSegments || new Map();
      const previousActivityItemIds = container._activityItemIds || new Set();
      const segments = timelineSegments(message.items);
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
        const completionRevision = segment.type === "activity"
          ? `;completed:${completed ? 1 : 0}`
          : "";
        const signature = `${segment.revision}${completionRevision}`
          + `;plots:${artifacts.plotPaths.join("\u001f")}`
          + `;structures:${artifacts.structurePaths.join("\u001f")}`;
        const previous = previousSegments.get(segment.key);
        let element = previous?.signature === signature ? previous.element : null;

        if (!element && previous?.element && segment.type === "text") {
          element = previous.element;
          const item = segment.items[0];
          const content = element.querySelector(".markdown-content");
          if (content) {
            setMarkdownContent(content, item.text || "", {
              cache: completed || !isLastSegment,
              defer: completed && !view.live,
              anchorPrefix: completed
                ? `${disclosurePrefix}${item.timelineId || "text:legacy"}:content`
                : "",
            });
          }
        }
        if (!element && previous?.element && segment.type === "delegation") {
          element = previous.element;
          const group = element.querySelector(".delegation-group");
          if (group) reconcileDelegationGroup(group, segment.calls, { live: !completed });
        }
        if (!element) {
          if (previous?.element) disclosureController.capture(previous.element);
          element = renderTimelineSegment(segment, {
            artifacts,
            cacheMarkdown: completed || !isLastSegment,
            disclosurePrefix,
            previousActivityItemIds,
            wireTimelineDetails,
            completed,
            deferMarkdown: completed && !view.live,
          });
          if (previous?.element?.parentElement === container) {
            if (insertionPoint === previous.element) insertionPoint = element;
            previous.element.replaceWith(element);
          }
        } else {
          element.querySelectorAll("details[data-disclosure-key]").forEach((details) => {
            liveDisclosureKeys.add(details.dataset.disclosureKey);
          });
        }
        if (element !== insertionPoint) container.insertBefore(element, insertionPoint);
        insertionPoint = element.nextElementSibling;
        nextSegments.set(segment.key, { element, signature });
      }

      for (const [key, previous] of previousSegments) {
        if (nextSegments.get(key)?.element !== previous.element) previous.element.remove();
      }
      disclosureController.prunePrefix(disclosurePrefix, liveDisclosureKeys);
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

  function addAgentTimelineMessage(
    message,
    shownPlotPaths = null,
    msgIndex,
    container = chatArea,
    timing = {},
  ) {
    const outer = document.createElement("div");
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
      inner.querySelectorAll("details.agent-activity[open]").forEach((activity) => {
        activity.open = false;
      });
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

  return { renderTimeline, addAgentTimelineMessage };
}
