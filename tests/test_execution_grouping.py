import unittest

from pydantic import ValidationError

from agents.MatCreator.agents.execution_agent.agent import (
    build_execution_groups,
    build_execution_waves,
)
from agents.MatCreator.agents.execution_agent.step_executor import StepExecutorInput


class _DummyToolContext:
    def __init__(self, state: dict):
        self.state = state


class TestExecutionGrouping(unittest.TestCase):
    def test_groups_consecutive_same_skill(self) -> None:
        ctx = _DummyToolContext(
            {
                "current_step_index": 0,
                "plan": {
                    "steps": [
                        {"step_number": 1, "skill": "vasp", "action": "Prepare input files."},
                        {"step_number": 2, "skill": "vasp", "action": "Run relaxation."},
                        {"step_number": 3, "skill": "plot", "action": "Plot total energy."},
                    ]
                },
            }
        )

        result = build_execution_groups(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["step_numbers"], [1, 2])
        self.assertEqual(result["groups"][0]["skill_name"], "vasp")
        self.assertEqual(result["groups"][1]["step_numbers"], [3])
        self.assertEqual(result["groups"][1]["skill_name"], "plot")

    def test_dependency_marker_starts_new_group(self) -> None:
        ctx = _DummyToolContext(
            {
                "current_step_index": 0,
                "plan": {
                    "steps": [
                        {"step_number": 1, "skill": "vasp", "action": "Run static calculation."},
                        {
                            "step_number": 2,
                            "skill": "vasp",
                            "action": "Using previous step results, extract DOS.",
                        },
                    ]
                },
            }
        )

        result = build_execution_groups(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["step_numbers"], [1])
        self.assertEqual(result["groups"][1]["step_numbers"], [2])

    def test_build_execution_waves_parallelizes_distinct_skills(self) -> None:
        groups = [
            {
                "group_id": "group_1_2",
                "skill_name": "vasp",
                "step_numbers": [1, 2],
                "actions": ["Prepare inputs.", "Run relax."],
            },
            {
                "group_id": "group_3_3",
                "skill_name": "plot",
                "step_numbers": [3],
                "actions": ["Plot band structure."],
            },
        ]

        result = build_execution_waves(groups)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["waves"]), 1)
        self.assertEqual(len(result["waves"][0]), 2)

    def test_build_execution_waves_serializes_dependency_marked_group(self) -> None:
        groups = [
            {
                "group_id": "group_1_1",
                "skill_name": "vasp",
                "step_numbers": [1],
                "actions": ["Run SCF."],
            },
            {
                "group_id": "group_2_2",
                "skill_name": "plot",
                "step_numbers": [2],
                "actions": ["Using previous step results, plot DOS."],
            },
        ]

        result = build_execution_waves(groups)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["waves"]), 2)
        self.assertEqual(result["waves"][0][0]["group_id"], "group_1_1")
        self.assertEqual(result["waves"][1][0]["group_id"], "group_2_2")


class TestStepExecutorInputNormalization(unittest.TestCase):
    def test_legacy_fields_are_normalized(self) -> None:
        payload = StepExecutorInput(
            step_number=2,
            action="Run calculation.",
            skill_name="vasp",
            workspace_dir="/tmp/work",
        )
        self.assertEqual(payload.step_numbers, [2])
        self.assertEqual(payload.actions, ["Run calculation."])

    def test_mismatched_lengths_fail_validation(self) -> None:
        with self.assertRaises(ValidationError):
            StepExecutorInput(
                step_numbers=[1, 2],
                actions=["one"],
                skill_name="vasp",
                workspace_dir="/tmp/work",
            )


if __name__ == "__main__":
    unittest.main()
