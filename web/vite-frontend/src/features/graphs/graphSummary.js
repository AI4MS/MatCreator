// Keep execution payloads out of session models, graph snapshots and render keys.
const fields = [
  "id", "type", "parent_id", "dependency_ids", "batch_id", "execution_batch_id",
  "status", "label", "summary", "start_time", "end_time", "revision",
  "conversation_count", "tool_call_count", "artifact_count",
];
export function graphNodeSummary(node) {
  const summary = Object.fromEntries(fields.filter((key) => key in node).map((key) => [key, node[key]]));
  const batchId = node.batch_id ?? node.execution_batch_id ?? node.input?.batch_id ?? node.input?.execution_batch_id;
  if (batchId != null) summary.batch_id = batchId;
  summary.input = Object.fromEntries(["node_id", "step_id", "step_number", "action"]
    .filter((key) => key in (node.input || {})).map((key) => [key, node.input[key]]));
  return summary;
}

export function graphNodeRevision(node) {
  return [node.id, node.revision ?? node.updated_at ?? "", node.status, node.start_time, node.end_time].join(":");
}

export async function fetchAgentNodeDetail(sessionId, nodeId, signal) {
  const response = await fetch(`/api/agent-graph/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load task detail (${response.status})`);
  return response.json();
}
