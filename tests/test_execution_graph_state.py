import pytest

from matcreator.agents.thinking_agent.planning import (
    NodeStatus,
    block_graph_dependents,
    graph_nodes_with_status,
    is_graph_complete,
    ready_nodes_from_graph,
    transition_graph_node,
)


def _graph() -> dict:
    return {
        "nodes": {
            "step_a": {
                "node_id": "step_a",
                "label": "A",
                "action": "Run A",
                "suggested_skills": [],
                "status": "pending",
            },
            "step_b": {
                "node_id": "step_b",
                "label": "B",
                "action": "Run B",
                "suggested_skills": [],
                "status": "pending",
            },
            "step_c": {
                "node_id": "step_c",
                "label": "C",
                "action": "Run C",
                "suggested_skills": [],
                "status": "pending",
            },
        },
        "edges": [["step_a", "step_b"], ["step_b", "step_c"]],
    }


def test_ready_nodes_follow_predecessor_success() -> None:
    graph = _graph()

    assert [node["node_id"] for node in ready_nodes_from_graph(graph)] == ["step_a"]

    transition_graph_node(graph, "step_a", NodeStatus.running)
    transition_graph_node(graph, "step_a", NodeStatus.success, "A done")

    assert [node["node_id"] for node in ready_nodes_from_graph(graph)] == ["step_b"]


def test_waiting_node_can_resume_but_cannot_skip_to_running() -> None:
    graph = _graph()
    transition_graph_node(graph, "step_a", NodeStatus.running)
    transition_graph_node(graph, "step_a", NodeStatus.waiting)

    assert graph_nodes_with_status(graph, NodeStatus.waiting) == ["step_a"]
    with pytest.raises(ValueError, match="waiting -> running"):
        transition_graph_node(graph, "step_a", NodeStatus.running)

    transition_graph_node(graph, "step_a", NodeStatus.pending)
    assert graph["nodes"]["step_a"]["status"] == "pending"


def test_block_dependents_records_failure_source() -> None:
    graph = _graph()
    transition_graph_node(graph, "step_a", NodeStatus.running)
    transition_graph_node(graph, "step_a", NodeStatus.failed, "A failed")

    assert block_graph_dependents(graph, "step_a") == ["step_b", "step_c"]
    assert graph_nodes_with_status(graph, NodeStatus.blocked) == ["step_b", "step_c"]
    assert graph["nodes"]["step_b"]["blocked_by"] == ["step_a"]
    assert not is_graph_complete(graph)


def test_graph_is_complete_only_when_every_node_succeeds() -> None:
    graph = _graph()
    for node_id in graph["nodes"]:
        graph["nodes"][node_id]["status"] = "success"

    assert is_graph_complete(graph)