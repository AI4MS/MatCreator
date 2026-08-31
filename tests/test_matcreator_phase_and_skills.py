from types import SimpleNamespace

from matcreator.agent import app, orchestrator
from matcreator.agents.execution_agent.agent import execution_agent
from matcreator.agents.orchestrator.agent import PlanningExecutionOrchestrator
from matcreator.agents.thinking_agent.agent import (
    before_agent_callback,
    thinking_agent,
)
from matcreator.skill import ALL_SKILLS, get_default_skill_names, load_skills


def test_before_agent_callback_initializes_thinking_context() -> None:
    state = {
        "session_id": "test-session",
        "workdir": "/tmp/matcreator-test",
    }
    callback_context = SimpleNamespace(
        state=state,
        _invocation_context=SimpleNamespace(
            session=SimpleNamespace(state=state),
        ),
    )

    before_agent_callback(callback_context)

    assert state["execution_graph"] is None
    assert state["goal"] is None
    assert state["summarize"] is None
    assert state["trajectory_step"] == 0
    assert state["workspace_dir"] == "/tmp/matcreator-test"
    assert "PLANNING ONLY" in state["instruction_body"]
    assert "Wait for explicit user confirmation" in state["instruction_body"]


def test_skill_registry_contains_all_bundled_skills() -> None:
    default_names = get_default_skill_names()
    loaded_names = {skill.name for skill in load_skills()}
    registry_names = {skill.name for skill in ALL_SKILLS}

    assert default_names
    assert default_names <= loaded_names
    assert default_names <= registry_names
    assert len(registry_names) == len(ALL_SKILLS)


def test_thinking_agent_is_the_orchestrator_planning_agent() -> None:
    assert isinstance(orchestrator, PlanningExecutionOrchestrator)
    assert app.root_agent is orchestrator
    assert orchestrator.planning_agent is thinking_agent
    assert orchestrator.execution_agent is execution_agent
