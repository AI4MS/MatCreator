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
