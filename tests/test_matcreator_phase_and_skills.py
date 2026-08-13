import unittest

from agents.MatCreator.agent import before_agent_callback_root
from agents.MatCreator.planning_agent import planning_agent
from agents.MatCreator.prompts.workflow import get_all_workflow_types, search_skills


class _DummySession:
    def __init__(self):
        self.id = "test-session"
        self.user_id = "test-user"
        self.app_name = "test-app"
        self.state = {}


class _DummyInvocationContext:
    def __init__(self):
        self.session = _DummySession()


class _DummyCallbackContext:
    def __init__(self):
        self._invocation_context = _DummyInvocationContext()


class TestMatCreatorPhaseAndSkills(unittest.TestCase):
    def test_before_agent_callback_sets_default_phase(self) -> None:
        callback_context = _DummyCallbackContext()
        before_agent_callback_root(callback_context)
        self.assertEqual(callback_context._invocation_context.session.state["phase"], "thinking")

    def test_skill_registry_loads_expected_workflows(self) -> None:
        workflow_types = get_all_workflow_types()
        self.assertIn("default", workflow_types)
        self.assertIn("pfd", workflow_types)

    def test_skill_search_returns_matching_workflow(self) -> None:
        results = search_skills("fine-tune distillation active learning", workflow_type="pfd", top_k=2)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].workflow_type, "pfd")

    def test_planning_agent_has_toolized_subagents(self) -> None:
        tools = getattr(planning_agent, "tools", []) or []
        self.assertGreaterEqual(len(tools), 3)


if __name__ == "__main__":
    unittest.main()
