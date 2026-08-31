from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from matcreator.agents.execution_agent.step_executor import StepExecutorInput
from matcreator.agents.thinking_agent.planning import (
    ExecutionGraph,
    get_ready_nodes,
    ready_nodes_from_graph,
)


def _graph() -> dict:
    return {
        "nodes": {
            "step_prepare": {
                "node_id": "step_prepare",
                "label": "Prepare",
                "action": "Prepare calculation inputs.",
                "suggested_skills": [],
                "status": "pending",
            },
            "step_analyze": {
                "node_id": "step_analyze",
                "label": "Analyze",
                "action": "Analyze an independent reference dataset.",
                "suggested_skills": [],
                "status": "pending",
            },
            "step_report": {
                "node_id": "step_report",
                "label": "Report",
                "action": "Combine the prepared and analyzed results.",
                "suggested_skills": [],
                "status": "pending",
            },
        },
        "edges": [
            ["step_prepare", "step_report"],
            ["step_analyze", "step_report"],
        ],
        "additional_notes": "The two root nodes may run concurrently.",
    }


def test_execution_graph_accepts_a_dag_and_rejects_a_cycle() -> None:
    graph = ExecutionGraph(**_graph())

    assert set(graph.nodes) == {"step_prepare", "step_analyze", "step_report"}
    assert graph.edges == [
        ["step_prepare", "step_report"],
        ["step_analyze", "step_report"],
    ]

    cyclic = _graph()
    cyclic["edges"].append(["step_report", "step_prepare"])
    with pytest.raises(ValidationError, match="cycle"):
        ExecutionGraph(**cyclic)


def test_ready_nodes_from_graph_follows_all_dependencies() -> None:
    graph = ExecutionGraph(**_graph()).model_dump(mode="json")

    assert [node["node_id"] for node in ready_nodes_from_graph(graph)] == [
        "step_prepare",
        "step_analyze",
    ]

    graph["nodes"]["step_prepare"]["status"] = "success"
    assert [node["node_id"] for node in ready_nodes_from_graph(graph)] == [
        "step_analyze"
    ]

    graph["nodes"]["step_analyze"]["status"] = "success"
    assert [node["node_id"] for node in ready_nodes_from_graph(graph)] == [
        "step_report"
    ]


def test_get_ready_nodes_reads_the_current_atomic_session_graph() -> None:
    graph = ExecutionGraph(**_graph()).model_dump(mode="json")
    graph["nodes"]["step_prepare"]["status"] = "success"
    tool_context = SimpleNamespace(state={"execution_graph": [graph]})

    result = get_ready_nodes(tool_context)

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["ready_nodes"] == [
        {
            "node_id": "step_analyze",
            "label": "Analyze",
            "action": "Analyze an independent reference dataset.",
            "suggested_skills": [],
        }
    ]
    assert result["waiting_nodes"] == []


def test_step_executor_input_uses_the_current_single_step_contract() -> None:
    payload = StepExecutorInput(
        step_number=2,
        action="Run the calculation.",
        suggested_skills=["vasp-pymatgen"],
        workspace_dir="/tmp/work",
        output_dir="/tmp/work/output",
        prior_context="Inputs were prepared successfully.",
    )

    assert payload.model_dump() == {
        "step_number": 2,
        "action": "Run the calculation.",
        "suggested_skills": ["vasp-pymatgen"],
        "workspace_dir": "/tmp/work",
        "output_dir": "/tmp/work/output",
        "prior_context": "Inputs were prepared successfully.",
    }


def test_step_executor_input_requires_suggested_skills() -> None:
    with pytest.raises(ValidationError, match="suggested_skills"):
        StepExecutorInput(
            step_number=1,
            action="Run the calculation.",
            workspace_dir="/tmp/work",
        )
