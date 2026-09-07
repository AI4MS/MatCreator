from __future__ import annotations

import json

from matcreator.agents import graph_logger


def test_consecutive_streamed_text_is_coalesced(tmp_path, monkeypatch) -> None:
    """A running node exposes one growing response instead of token cards."""
    monkeypatch.setattr(graph_logger, "ADK_DIR", tmp_path)
    logger = graph_logger.AgentGraphLogger("streaming-session")
    logger.log_node_start("step-one", "step", "Step one")

    logger.log_conversation_event("step-one", {
        "timestamp": "first",
        "author": "step_executor",
        "type": "text",
        "content": "Calculating lattice",
    })
    # Cumulative snapshots and deltas are both emitted by supported providers.
    logger.log_conversation_event("step-one", {
        "timestamp": "second",
        "author": "step_executor",
        "type": "text",
        "content": "Calculating lattice parameters",
    })
    logger.log_conversation_event("step-one", {
        "timestamp": "third",
        "author": "step_executor",
        "type": "text",
        "content": " and checking convergence.",
    })

    graph_path = tmp_path / "agent_graphs" / "streaming-session.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    conversation = graph["nodes"]["step-one"]["conversation"]
    assert conversation == [{
        "timestamp": "third",
        "author": "step_executor",
        "type": "text",
        "content": "Calculating lattice parameters and checking convergence.",
    }]


def test_execution_batch_metadata_does_not_change_parent_edge(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(graph_logger, "ADK_DIR", tmp_path)
    logger = graph_logger.AgentGraphLogger("batched-session")
    logger.log_node_start("planning_0", "planning", "Planning", "orchestrator")
    logger.log_node_start(
        "execution_0",
        "execution",
        "Execution 1",
        "planning_0",
        batch_id="planning_0:round:0",
    )

    graph_path = tmp_path / "agent_graphs" / "batched-session.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert graph["nodes"]["execution_0"]["batch_id"] == "planning_0:round:0"
    assert {"from": "planning_0", "to": "execution_0"} in graph["edges"]


def test_explicit_step_dependencies_replace_execution_parent_edge(tmp_path, monkeypatch) -> None:
    """DAG joins retain only the predecessors declared by the execution plan."""
    monkeypatch.setattr(graph_logger, "ADK_DIR", tmp_path)
    logger = graph_logger.AgentGraphLogger("dependency-session")
    logger.log_node_start("execution_0", "execution", "Execution 1")
    logger.log_node_start("execution_0__node_1", "step", "Node 1", "execution_0")
    logger.log_node_start("execution_0__node_2", "step", "Node 2", "execution_0")
    logger.log_node_start(
        "execution_0__node_3",
        "step",
        "Node 3",
        "execution_0",
        dependency_ids=["execution_0__node_1", "execution_0__node_2"],
    )
    logger.log_node_start(
        "execution_0__node_4",
        "step",
        "Node 4",
        "execution_0",
        dependency_ids=["execution_0__node_2"],
    )

    graph_path = tmp_path / "agent_graphs" / "dependency-session.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    edges = {(edge["from"], edge["to"]) for edge in graph["edges"]}
    assert ("execution_0__node_1", "execution_0__node_3") in edges
    assert ("execution_0__node_2", "execution_0__node_3") in edges
    assert ("execution_0__node_2", "execution_0__node_4") in edges
    assert ("execution_0__node_1", "execution_0__node_4") not in edges
    assert ("execution_0", "execution_0__node_3") not in edges
    assert ("execution_0", "execution_0__node_4") not in edges
