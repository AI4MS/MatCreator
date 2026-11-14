from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool
from matcreator.tools.aidb_archive_tools import aidb_archive_vasp
from matcreator.tools.aidb_tools import aidb_query, aidb_detail, aidb_bind
from matcreator.tools.aidb_tools import aidb_export
from matcreator.tools.xrd_tools import xrd_list_workspaces, xrd_get_results, xrd_action
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ...llm_cards import LLMCard
from ...skill import ALL_SKILLS_TOOLSET
from ...knowledge.query import get_related_skills, query_knowledge_graph, read_knowledge_node
from ...tools.remoteagent_tool import load_remote_a2a_agents
from ...tools.util_tools import show_artifact, show_plot, show_structure
from ...tools.workspace_tools import get_user_skills_root, run_bash, run_python
from .eos_tools import collect_eos_async, resolve_eos_member_async, submit_eos_async
from .matpes_tools import collect_matpes_batch_async, resolve_matpes_frame_async, submit_matpes_batch_async
from .remote_job_tools import (
    attach_bohr_batchjob,
    collect_remote_job_outputs_async,
    create_remote_job_group,
    download_remote_job_output,
    get_remote_job_status,
    pause_remote_job,
    poll_remote_job_command,
    run_remote_job_command,
    start_managed_vasp_sandbox_async,
    start_remote_job_command,
    submit_bohr_batchjob,
    submit_bohr_sandbox,
    terminate_remote_job,
    upload_remote_job_input,
)

logger = logging.getLogger(__name__)

STEP_EXECUTOR_AGENT_NAME = "step_executor"

# ADK's LiteLlm adapter parses streamed function-call arguments as JSON.  Some
# OpenAI-compatible endpoints occasionally finish a stream with malformed tool
# arguments, which escapes as JSONDecodeError.  Recovery is handled at the
# orchestrator level (see ``orchestrator/agent.py:_stream_execution_with_recovery``
# and ``_stream_planning_with_recovery``), which wraps agent streams in
# try/except json.JSONDecodeError.  The legacy ``RetryConfig`` did nothing
# here because ``LlmAgent`` runs through ``llm_flows``, which does not
# consume ``retry_config``; LiteLLM's own HTTP retry setting only covers
# transport/status failures.


class StepExecutorInput(BaseModel):
    step_number: int = Field(description="1-based index of this step in the plan")
    action: str = Field(description="Action description from the plan step")
    suggested_skills: List[str] = Field(description="Ordered list of skill names suggested by the planner for this step")
    workspace_dir: str = Field(description="Absolute path to the root workspace directory available to this step")
    output_dir: Optional[str] = Field(
        default=None,
        description="Absolute path where generated files for this session should be written",
    )
    prior_context: Optional[str] = Field(
        default=None,
        description="Condensed summaries of prior completed steps for context",
    )


class StepExecutorResult(BaseModel):
    status: Literal["success", "needs_replanning"]
    key_results: str = Field(
        default="",
        description="Bullet-point list of key findings, values, and produced files",
    )
    artifacts: list[str] = Field(
        default_factory=list,
        description="Absolute paths of generated files or artifacts",
    )

    @field_validator("artifacts", mode="before")
    @classmethod
    def _coerce_artifacts(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return [v] if v else []
        return v
    concise_summary: str = Field(
        default="",
        description="Short user-facing paragraph describing what was done",
    )
    replan_reason: Optional[str] = Field(
        default=None,
        description="Why replanning is needed (only set when status=needs_replanning)",
    )

    @model_validator(mode="after")
    def _fill_missing_fields(self) -> "StepExecutorResult":
        # If the LLM returned only one of the two summary fields, mirror it to the other.
        if not self.key_results and self.concise_summary:
            self.key_results = self.concise_summary
        elif not self.concise_summary and self.key_results:
            self.concise_summary = self.key_results
        return self


_STEP_EXECUTOR_INSTRUCTION = """
A managed VASP submit may return waiting_choice. The runtime then returns to chat for a user decision. Do not submit again or choose a candidate.
For a VASP EOS, use the eos skill's tracked route. Prepare a cell relaxation and one SCF template, then use submit_eos after the existing submission authorization for the whole EOS. Use collect_eos with the same EOS ID after relaxation and during recovery. On an authorized human turn, use advance=true to prepare and submit unmatched static points. Job status updates use advance=false. Use resolve_eos_member only for the explicit human decision for the named relaxation or volume point. Report missing points and distinguish calculation from archive status.

For VASP MatPES labeling, use prepare_matpes.py --batch then submit_matpes_batch once. Report the batch ID and each frame waiting for a decision. Use resolve_matpes_frame only for an explicit decision for that frame. Use collect_matpes_batch with the original batch ID for a status update, wakeup, or to assemble available labels. MatPES group names carry the batch ID after "matpes:". Do not recreate inputs or submit replacement jobs during collection.

AIDB requests: discover/load the installed ai-ready-db skill, then use aidb_query/aidb_detail for session-bound records and clickable chat presentation. Use explicit user conditions; clarify ambiguity. Record editing/deletion is unavailable.
For a natural-language AIDB export, call aidb_export directly after skill discovery. Use the actual record ID and, for a prior result row, its result ID. Return only the download formats confirmed in the receipt and explain any missing labels.
aidb_detail directly fresh-reads the referenced ID and opens its full presenter. Answer from its returned facts; do not plan raw JSONL inspection or request extra approval for this supported read. Omitted arrays remain in the UI snapshot. Preserve unknown, false, not-applicable and truncation states. Label presence does not establish alignment, convergence, compatibility or training admission. Never infer that records differ only in functional from the bounded summary; distinguish native match counts, visible rows and verified frames.

You are a focused step executor. Execute the single plan step provided in your input.
If the step explicitly saves existing VASP relaxation, static SCF or MD outputs, call aidb_archive_vasp with the workspace directories. Report its per-directory receipts and do not submit VASP for this operation.
For all AIDB reads, omission from model facts is not evidence that a field is missing or not applicable. Never claim fields were not saved/extracted just because they are unlisted. Use optional_results availability and evidence; not_provided refers to the returned record, not raw-output capability. PAW projected electron populations are not oxidation states. Null magnetic moments or spin settings do not establish material magnetism; preserve not_assessed.

## Your task
1. Review `suggested_skills` from your input. Call `load_skill` for each skill you deem
   relevant to the action. Use `query_knowledge_graph` to discover additional skills if the
   suggested list is insufficient. Inspect `load_skill`'s `attached_context`; only
   when it reports L3/L4 entries, call `read_knowledge_node` to retrieve that guidance.
   Inspect its `bundled_files` listing: read bundled files with
   `load_skill_resource(skill_name, file_path)` and run bundled `scripts/` files with
   `run_skill_script` — never guess file paths that were not listed.
2. Decompose task into sub-tasks. Directly execute them (**simple** cases) or **Delegate** them to child executors by calling `run_sub_agent` tool (**complex** cases).
       
## Reporting results (REQUIRED)
When done, call `submit_step_result` with:
- `status`: "success" or "needs_replanning"
- `key_results`: bullet-point list of key findings, values, and produced files
- `concise_summary`: short user-facing paragraph describing what was done
- `artifacts`: list of absolute paths of all generated files
- `replan_reason`: why replanning is needed (only when status=needs_replanning, else omit)

If `submit_step_result` returns a validation error, fix the fields and call it again.

## Execution rules
- Use `workspace_dir` as the working directory and for reading shared inputs.
- If `output_dir` is provided, write all generated files and artifacts under `output_dir`.
- Exception: when the loaded `skill-creation` guide requires authoring a reusable
  user skill, call `get_user_skills_root` and write only inside that returned root.
- Use `run_python` or `run_bash` for computation. Do not fabricate outputs.
- Include ALL generated files with their absolute paths in `artifacts`.
- Do not retry indefinitely on failure — call `submit_step_result` with needs_replanning.

## Resume-awareness (CRITICAL for remote jobs)
When `prior_context` mentions a previously submitted remote job (e.g. Bohrium/Slurm):
1. **Check for existing submission.json** in the workspace — if found, do NOT create a new one.
2. **Check for already-downloaded output files** (e.g. `frozen.pt2`, `lcurve.out`). If they exist, the job already completed — use the existing results directly.
3. **If submission.json exists but outputs are missing**, reuse the same submission file (dpdispatcher is idempotent — it skips completed tasks). Do NOT regenerate submission.json.
4. **Never resubmit a job that already completed** — this wastes GPU time and creates duplicate training runs.

## Re-attaching to an existing remote job (CRITICAL)
If your `prior_context` contains "REMOTE JOB ALREADY SUBMITTED", a tracked remote job
(sandbox or batch job) for this exact step is already running:
1. Call `get_remote_job_status` with the given `job_id` FIRST.
2. NEVER call `submit_bohr_sandbox` or `submit_bohr_batchjob` for that
   step — it would duplicate a running job.
3. If its `snapshot` contains `background_command`, a command is (or was) running in the
   background — call `poll_remote_job_command` FIRST rather than issuing a new command. Never
   call `start_remote_job_command`/`run_remote_job_command` again for the same computation
   just because you lost track of it; re-running a non-idempotent command (e.g. a training
   run) can corrupt output or double-charge compute.
4. For a managed VASP sandbox, poll the tracked command and call
   collect_remote_job_outputs after it ends, even when its exit code is nonzero.
   For a Batch Job, call collect_remote_job_outputs when status is succeeded, collecting,
   or collected. Use the original job ID for an interrupted collection or failed archive.
   For other interactive work, use download_remote_job_output for individual files.
   When collection returns a VASP completion, report its compute and archive statuses
   independently and keep the collected artifact paths. A provider status alone is
   insufficient to report VASP convergence or AIDB archival.
   For a band-structure chain, deliver the SCF job ID for a computed result
   or the record ID for a reused result, plus the verified CHGCAR (and WAVECAR
   if needed). Submit NSCF with upstream_job_id or upstream_record_id and the
   actual local restart files. A verified SCF can feed NSCF before AIDB archival. NSCF is complete when the managed receipt verifies bands.json
   and bands.svg, or the reuse receipt delivers both files.
5. If it is still running, call `submit_step_result(status="needs_replanning", ...)` stating
   that the job has not finished yet and quoting its job_id and status.
6. Legacy `bohr_job` records are unsupported. Report needs_replanning with the recorded
   job_id and error; never reinterpret its external ID or automatically submit a replacement.

## Choosing a remote-job submit tool
- When submitting two or more similar or simultaneous Batch Jobs in this step,
  ALWAYS call `create_remote_job_group` once first and pass its returned
  `group_id` to every related `submit_bohr_batchjob` or `attach_bohr_batchjob`
  call. Set `expected_jobs` to the exact number of jobs in that group.
- `attach_bohr_batchjob`: track an already-submitted Batch Job using its explicit string
  `batchjob_id`. It only reads status and never submits. Use it for externally submitted
  jobs instead of creating a replacement; then use the returned `job_id` for status and outputs.
- `submit_bohr_sandbox`: interactive sandbox via the `bohr` CLI — the default for any work
  that needs command execution or file transfer after submission. Requires an explicit
  `template`.
- For VASP inside a Bohrium sandbox, use start_managed_vasp_sandbox with the
  prepared workspace directory. It checks for reusable records and stages files
  before starting the tracked command. Poll and collect with the same job_id.
- `submit_bohr_batchjob`: sandbox-based batch job via `bohr batchjob submit`. Requires
  `name`, `image`, `command`, and exactly one of `machine_type` or `sku_id`. Discover
  machines with `bohr batchjob machine list -o json`. Use `input_path` (a path relative
  to your working directory, e.g. `si_scf` — never absolute; a directory's contents
  appear at the root of the remote job's working directory) and `out_files` (a JSON
  array of path strings, e.g. `["vasprun.xml", "OUTCAR", "log"]`);
  durations include units, e.g. `max_run_time="2h"`. There is no interactive
  command execution for this provider — express the entire computation in `command`, then
  poll `get_remote_job_status` until `succeeded` and call `collect_remote_job_outputs`
  with a new, nonexistent workspace destination directory.

## Running commands inside a sandbox: blocking vs. background (CRITICAL)
- `run_remote_job_command` BLOCKS until the command finishes, with no timeout. Only use it
  for short commands expected to finish in well under a minute (`mkdir`, `grep`, checking a
  file, listing a directory).
- For any real computation (a training run, `vasp_std`/`mpirun`, anything that might run
  longer than a minute), use `start_remote_job_command` instead — it launches the command in
  the background and returns almost immediately — then call `poll_remote_job_command` to check
  on it. Do useful work in between polls (e.g. `submit_step_result(status="needs_replanning", ...)`
  reporting the job is still running, rather than looping tool calls back-to-back) so a single
  step doesn't sit blocked. Re-attaching to the same `job_id` later always finds the same
  tracked command via `poll_remote_job_command`, so it's always safe even after a step timeout.

## User controls for remote jobs
`get_remote_job_status` may return `user_control` when the user paused or terminated
the job from the UI. This does not cancel your executor. Treat it as the
user's explicit instruction: do not retry the interrupted command or
submit a replacement job. Report the pause or termination accurately with
`submit_step_result(status="needs_replanning", replan_reason=...)`.

## MANDATORY: Always call submit_step_result
You MUST call `submit_step_result` before finishing. If you exit without calling it, the step will be marked as `needs_replanning` after timeout. Never end a step with just a text response — always use `submit_step_result` to report your outcome.
"""


async def run_sub_agent(
    step_number: int,
    action: str,
    suggested_skills: List[str],
    tool_context: ToolContext,
    prior_context: Optional[str] = None,
) -> dict:
    """Spawn a child step executor to handle a sub-task.

    Call once per sub-task. For truly independent sub-tasks, issue multiple
    `run_sub_agent` calls in a SINGLE response turn — the runtime executes them
    concurrently. For dependent sub-tasks, call sequentially and pass the returned
    result as `prior_context` to the next call.

    Returns a result dict with keys: status, key_results, concise_summary, artifacts.
    If status is "cancelled", call submit_step_result with needs_replanning immediately.

    Args:
        step_number: Sub-task index (1-based, unique within this step)
        action: What the sub-task should do
        suggested_skills: Skills to preload in the child executor
        prior_context: Summary of earlier sub-tasks' results (for sequential chains)
    """
    from .step_executor_runner import run_step_executor  # lazy import avoids circular dep

    return await run_step_executor(
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir="",  # computed internally by run_step_executor
        prior_context=prior_context,
        tool_context=tool_context,
    )


def submit_step_result(
    status: str,
    key_results: str,
    concise_summary: str,
    tool_context: ToolContext,
    artifacts: Optional[List[str]] = None,
    replan_reason: Optional[str] = None,
) -> dict:
    """Submit the result of this step execution.

    Call this tool when you have finished executing the step. Replaces writing
    a JSON response. If validation fails, fix the reported fields and retry.

    Args:
        status: "success" or "needs_replanning"
        key_results: Bullet-point list of key findings and produced files
        concise_summary: Short user-facing paragraph describing what was done
        artifacts: Absolute paths of all generated files (empty list if none)
        replan_reason: Why replanning is needed (only when status=needs_replanning)
    """
    try:
        result = StepExecutorResult(
            status=status,  # type: ignore[arg-type]
            key_results=key_results,
            concise_summary=concise_summary,
            artifacts=artifacts or [],
            replan_reason=replan_reason,
        )
        tool_context.state["_step_result"] = result.model_dump()
        return {"status": "ok", "message": "Step result submitted successfully."}
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": exc.errors(),
            "message": "Validation failed. Fix the errors and call submit_step_result again.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Unexpected error: {exc}"}


def build_step_executor_agent(llm_card: LLMCard) -> LlmAgent:
    """Build a step executor agent for one executor invocation."""
    return LlmAgent(
        name=STEP_EXECUTOR_AGENT_NAME,
        model=LiteLlm(
            model=llm_card.model,
            base_url=llm_card.base_url,
            api_key=llm_card.api_key,
        ),
        description=(
            "Executes a single plan step in an isolated session. "
            "Receives structured input with action and skill name; loads skill instructions autonomously."
        ),
        instruction=_STEP_EXECUTOR_INSTRUCTION,
        input_schema=StepExecutorInput,
        tools=[
            FunctionTool(run_sub_agent),
            FunctionTool(submit_step_result),
            FunctionTool(query_knowledge_graph),
            FunctionTool(read_knowledge_node),
            FunctionTool(get_related_skills),
            FunctionTool(get_user_skills_root),
            FunctionTool(run_python),
            FunctionTool(run_bash),
            FunctionTool(aidb_query),
            FunctionTool(aidb_detail),
            FunctionTool(aidb_export),
            FunctionTool(aidb_bind),
            FunctionTool(aidb_archive_vasp),
            FunctionTool(xrd_list_workspaces),
            FunctionTool(xrd_get_results),
            FunctionTool(xrd_action),
            FunctionTool(submit_bohr_sandbox),
            FunctionTool(create_remote_job_group),
            FunctionTool(submit_eos_async),
            FunctionTool(collect_eos_async),
            FunctionTool(resolve_eos_member_async),
            FunctionTool(submit_matpes_batch_async),
            FunctionTool(resolve_matpes_frame_async),
            FunctionTool(collect_matpes_batch_async),
            FunctionTool(submit_bohr_batchjob),
            FunctionTool(attach_bohr_batchjob),
            FunctionTool(get_remote_job_status),
            FunctionTool(run_remote_job_command),
            FunctionTool(start_managed_vasp_sandbox_async),
            FunctionTool(start_remote_job_command),
            FunctionTool(poll_remote_job_command),
            FunctionTool(upload_remote_job_input),
            FunctionTool(download_remote_job_output),
            FunctionTool(collect_remote_job_outputs_async),
            FunctionTool(pause_remote_job),
            FunctionTool(terminate_remote_job),
            ALL_SKILLS_TOOLSET,
            FunctionTool(show_plot),
            FunctionTool(show_structure),
            FunctionTool(show_artifact),
        ],
        sub_agents=load_remote_a2a_agents(),
    )
