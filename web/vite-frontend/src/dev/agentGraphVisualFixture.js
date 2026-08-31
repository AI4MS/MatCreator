// Development-only visual data for inspecting the Agent Graph canvas. The
// module is loaded dynamically from main.js, so production builds can remove
// the entire fixture branch. It contains no transport or persistence code.
export const AGENT_GRAPH_VISUAL_FIXTURE = {
  visual_fixture: "agent-droplets",
  updated_at: "2026-08-28T09:02:30.000Z",
  nodes: {
    orchestrator: {
      id: "orchestrator",
      type: "orchestrator",
      label: "Orchestrator",
      status: "success",
      summary: "Coordinates the visual fixture without starting a session run.",
      start_time: "2026-08-28T09:00:00.000Z",
      end_time: "2026-08-28T09:00:05.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "planning-fixture": {
      id: "planning-fixture",
      type: "planning",
      parent_id: "orchestrator",
      label: "Plan",
      status: "running",
      summary: "Running state demonstrates the droplet body's organic liquid motion.",
      start_time: "2026-08-28T09:00:06.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "execution-build": {
      id: "execution-build",
      type: "execution",
      parent_id: "planning-fixture",
      batch_id: "fixture-batch-build",
      label: "Build",
      status: "success",
      summary: "Completed execution node for type and state contrast.",
      start_time: "2026-08-28T09:00:20.000Z",
      end_time: "2026-08-28T09:01:05.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "execution-review": {
      id: "execution-review",
      type: "execution",
      parent_id: "planning-fixture",
      batch_id: "fixture-batch-review",
      label: "Review",
      status: "failed",
      summary: "Failed execution node demonstrates an independent status badge.",
      start_time: "2026-08-28T09:01:10.000Z",
      end_time: "2026-08-28T09:01:42.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "tester-fixture": {
      id: "tester-fixture",
      type: "tester",
      parent_id: "planning-fixture",
      label: "Test",
      status: "needs_replanning",
      summary: "Tester requests another planning pass.",
      start_time: "2026-08-28T09:01:45.000Z",
      end_time: "2026-08-28T09:02:00.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "step-waiting": {
      id: "step-waiting",
      type: "step",
      parent_id: "execution-build",
      label: "Await input",
      status: "waiting",
      summary: "Waiting step shows the neutral ellipsis badge.",
      start_time: "2026-08-28T09:00:24.000Z",
      input: { node_id: "fixture-await-input", step_number: 1 },
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
    "step-cancelled": {
      id: "step-cancelled",
      type: "step",
      parent_id: "execution-review",
      label: "Cancelled task",
      status: "cancelled",
      summary: "Cancelled step shows the muted face and cross badge.",
      start_time: "2026-08-28T09:01:14.000Z",
      end_time: "2026-08-28T09:01:36.000Z",
      input: { node_id: "fixture-cancelled-task", step_number: 2 },
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
  },
  edges: [
    { id: "fixture-orchestrator-plan", from: "orchestrator", to: "planning-fixture" },
    { id: "fixture-plan-build", from: "planning-fixture", to: "execution-build" },
    { id: "fixture-plan-review", from: "planning-fixture", to: "execution-review" },
    { id: "fixture-plan-test", from: "planning-fixture", to: "tester-fixture" },
    { id: "fixture-build-waiting", from: "execution-build", to: "step-waiting" },
    { id: "fixture-review-cancelled", from: "execution-review", to: "step-cancelled" },
  ],
};

// A second full snapshot is intentionally delayed by the dev fixture loader.
// It exercises the same cold-layout path used by polling fallback while
// making the child appear after its visual parent already exists.
export const AGENT_GRAPH_SPLIT_UPDATE_FIXTURE = {
  ...AGENT_GRAPH_VISUAL_FIXTURE,
  updated_at: "2026-08-28T09:02:34.000Z",
  nodes: {
    ...AGENT_GRAPH_VISUAL_FIXTURE.nodes,
    "planning-fixture": {
      ...AGENT_GRAPH_VISUAL_FIXTURE.nodes["planning-fixture"],
      status: "success",
      end_time: "2026-08-28T09:02:32.000Z",
    },
    "step-splitting": {
      id: "step-splitting",
      type: "step",
      parent_id: "execution-build",
      label: "Live child",
      status: "running",
      summary: "A new liquid node separates from its visible predecessor.",
      start_time: "2026-08-28T09:02:34.000Z",
      input: { node_id: "fixture-live-child", step_number: 2 },
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
  },
  edges: [
    ...AGENT_GRAPH_VISUAL_FIXTURE.edges,
    { id: "fixture-build-splitting", from: "execution-build", to: "step-splitting" },
  ],
};

export const AGENT_GRAPH_SPLIT_BASE_FIXTURE = {
  visual_fixture: "agent-droplet-split",
  updated_at: "2026-08-28T10:00:00.000Z",
  nodes: {
    orchestrator: {
      ...AGENT_GRAPH_VISUAL_FIXTURE.nodes.orchestrator,
      start_time: "2026-08-28T10:00:00.000Z",
      end_time: "2026-08-28T10:00:04.000Z",
    },
    "planning-split": {
      ...AGENT_GRAPH_VISUAL_FIXTURE.nodes["planning-fixture"],
      id: "planning-split",
      status: "success",
      start_time: "2026-08-28T10:00:05.000Z",
      end_time: "2026-08-28T10:00:10.000Z",
    },
  },
  edges: [
    { id: "split-orchestrator-plan", from: "orchestrator", to: "planning-split" },
  ],
};

export const AGENT_GRAPH_SPLIT_CHILD_FIXTURE = {
  ...AGENT_GRAPH_SPLIT_BASE_FIXTURE,
  updated_at: "2026-08-28T10:00:12.000Z",
  nodes: {
    ...AGENT_GRAPH_SPLIT_BASE_FIXTURE.nodes,
    "execution-split": {
      id: "execution-split",
      type: "execution",
      parent_id: "planning-split",
      batch_id: "fixture-split-batch",
      label: "Execute",
      status: "running",
      summary: "This running child visibly separates from the completed planning droplet.",
      start_time: "2026-08-28T10:00:12.000Z",
      artifacts: [],
      conversation: [],
      tool_calls: [],
    },
  },
  edges: [
    ...AGENT_GRAPH_SPLIT_BASE_FIXTURE.edges,
    { id: "split-plan-execution", from: "planning-split", to: "execution-split" },
  ],
};
