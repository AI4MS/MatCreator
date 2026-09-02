import {
  activityToolCalls,
  executorNodeId,
  formatToolDuration,
  toolStatusIcon,
} from "./timelinePresentation.js";
import { createActionRawView } from "./payloadViews.js";

export function createActivityRenderer({
  setMarkdownContent,
  getActiveRequest,
  getStepExecutionFeed,
}) {
  function createTimelineReasoning(
    entry,
    wireTimelineDetails,
    collapsed = false,
    isNew = false,
    cacheMarkdown = true,
  ) {
    if (!collapsed) {
      const section = document.createElement("div");
      section.className = `agent-activity-reasoning-entry${isNew ? " is-entering" : ""}`;
      const content = document.createElement("div");
      content.className = "markdown-content";
      setMarkdownContent(content, entry.text || "", { cache: cacheMarkdown, defer: false });
      section.appendChild(content);
      return section;
    }

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
      const collapseButton = document.createElement("button");
      collapseButton.type = "button";
      collapseButton.className = "agent-activity-reasoning-collapse";
      collapseButton.textContent = "›";
      collapseButton.title = "Collapse reasoning";
      collapseButton.setAttribute("aria-label", "Collapse reasoning");
      collapseButton.addEventListener("click", () => { details.open = false; });
      content.append(markdown, collapseButton);
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
    } else if (details.open) {
      mountContent();
    }
    return details;
  }

  function createActivityAction(
    action,
    wireTimelineDetails,
    { isNew = false, includeExecutorTools = false } = {},
  ) {
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
    if (isNew) {
      const clearEntryState = (event) => {
        if (event.target !== group) return;
        group.classList.remove("is-entering");
        group.removeEventListener("animationend", clearEntryState);
      };
      group.addEventListener("animationend", clearEntryState);
    }
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
    const stepExecutionFeed = getStepExecutionFeed();
    let insertionPoint = list.firstElementChild;

    calls.forEach((call) => {
      const key = String(
        call.executorTaskKey || call.id || executorNodeId(call) || call.timelineId || call.name,
      );
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
        stepExecutionFeed.bindRootHost(row.host, executorNodeId(call), call.input?.action || "");
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
    const completed = options.completed || (
      !getActiveRequest() && actions.every((action) => action.status !== "running")
    );
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
      body.append(actionList, more);
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

  return {
    createTimelineReasoning,
    createActivityAction,
    createDelegationGroupShell,
    reconcileDelegationGroup,
    createAgentActivity,
  };
}
