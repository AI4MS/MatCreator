function nodeLayoutValue(node) {
  return JSON.stringify([
    node?.id,
    node?.type,
    node?.parent_id,
    node?.batch_id ?? node?.execution_batch_id,
    node?.start_time,
    node?.end_time,
    node?.label,
    node?.input?.node_id ?? node?.input?.step_id,
  ]);
}

/** Materialize a compact graph SSE patch while retaining cold node objects. */
export function applyGraphUpdate(previous, incoming) {
  if (!incoming?.delta) {
    return {
      graph: incoming,
      changedNodeIds: new Set(Object.keys(incoming?.nodes || {})),
      layoutChanged: true,
      isDelta: false,
    };
  }

  const nodes = { ...(previous?.nodes || {}) };
  const changedNodeIds = new Set();
  let layoutChanged = Boolean(incoming.layout_changed || Array.isArray(incoming.edges));
  for (const id of incoming.removed_node_ids || []) {
    if (nodes[id]) layoutChanged = true;
    delete nodes[id];
    changedNodeIds.add(id);
  }
  for (const [id, node] of Object.entries(incoming.nodes || {})) {
    if (nodeLayoutValue(nodes[id]) !== nodeLayoutValue(node)) layoutChanged = true;
    nodes[id] = node;
    changedNodeIds.add(id);
  }

  const graph = {
    ...(previous || {}),
    ...incoming,
    nodes,
    edges: Array.isArray(incoming.edges) ? incoming.edges : (previous?.edges || []),
  };
  delete graph.delta;
  delete graph.layout_changed;
  delete graph.removed_node_ids;
  return { graph, changedNodeIds, layoutChanged, isDelta: true };
}
