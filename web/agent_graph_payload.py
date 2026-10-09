"""Small graph snapshots; execution history is served only as node detail."""

NODE_FIELDS = (
    "id", "type", "parent_id", "dependency_ids", "batch_id", "execution_batch_id",
    "status", "label", "summary", "start_time", "end_time",
)
INPUT_FIELDS = ("node_id", "step_id", "step_number", "action")


def node_summary(node: dict, legacy_revision=None) -> dict:
    result = {key: node[key] for key in NODE_FIELDS if key in node}
    # Old logs have no per-node counter. Use the file generation until the
    # logger next updates that node, without hashing its execution history.
    result["revision"] = node.get("revision", legacy_revision)
    node_input = node.get("input") or {}
    batch_id = node.get("batch_id") or node.get("execution_batch_id") or node_input.get("batch_id") or node_input.get("execution_batch_id")
    if batch_id is not None:
        result["batch_id"] = batch_id
    result["input"] = {
        key: value
        for key, value in node_input.items()
        if key in INPUT_FIELDS and isinstance(value, (str, int, float, bool))
    }
    for key in ("label", "summary"):
        if isinstance(result.get(key), str):
            result[key] = result[key][:1024]
    for field, count in (("conversation", "conversation_count"),
                         ("tool_calls", "tool_call_count"), ("artifacts", "artifact_count")):
        result[count] = len(node.get(field) or [])
    return result


def graph_summary(data: dict, legacy_revision=None) -> dict:
    return {
        "session_id": data.get("session_id"),
        "updated_at": data.get("updated_at"),
        "nodes": {
            node_id: node_summary(node, legacy_revision or data.get("updated_at"))
            for node_id, node in (data.get("nodes") or {}).items()
            if isinstance(node, dict)
        },
        "edges": data.get("edges") or [],
    }
