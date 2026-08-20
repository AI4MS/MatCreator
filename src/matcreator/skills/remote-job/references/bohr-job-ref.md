# submit_bohr_job Reference (Batch/HPC Job)

Detailed parameters and protocols for the batch bohr-job path. For
machine SKUs, images, access-key setup, and project-ID discovery, see the
`bohrium` skill.

## Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `job_name` | yes | Explanatory name (type, content, variation), e.g. `SiO2-md-1000K-run2`. |
| `machine_type` | yes | Machine SKU — see the `bohrium` skill machine reference. |
| `image_address` | yes | Container image; GPU jobs need a GPU-enabled image. |
| `command` | yes | The ENTIRE computation — there is no interactive exec afterward. |
| `project_id` | no | Falls back to `BOHRIUM_PROJECT_ID`. |
| `input_directory` | no | Local directory staged once as job input. |
| `result_path` | no | Remote path whose contents are collected as outputs. |
| `max_run_time` | no | Wall-time limit in seconds. |

Returns `job_id` (use it for every later call). Idempotent per session, node,
and job name: repeating the call for the same step returns the existing
record instead of creating a second job.

Capabilities: submit-time inputs and batch output collection only — no
interactive command execution, no per-command file upload/download.

## Lifecycle

1. Call `submit_bohr_job` once for the current step and record the `job_id`.
2. Poll `get_remote_job_status` until it reports `status: succeeded`.
3. Call `collect_remote_job_outputs(job_id, destination_path)` to pull the
   declared outputs into the workspace (repeat calls are durable no-ops).

Never call `run_remote_job_command`, `start_remote_job_command`,
`poll_remote_job_command`, or `upload_remote_job_input` on a batch job — the
provider has no interactive execution and returns an error.

Outputs may arrive as a zip that needs extraction; download promptly —
Bohrium retains completed job results only for a limited time.

## Re-attach protocol ("REMOTE JOB ALREADY SUBMITTED")

If the step's prior context says a batch job was already submitted for this
exact step:

1. Call `get_remote_job_status` with the given `job_id` FIRST.
2. NEVER call `submit_bohr_job` again for that step — it would duplicate a
   running job and waste compute.
3. If `status: succeeded`, call `collect_remote_job_outputs` and report
   success.
4. If still running (`queued`/`running`), return `needs_replanning` quoting
   the `job_id` and status rather than looping polls inside one step.

## Controls and user intervention

- `terminate_remote_job(job_id)` — cancels the batch job.
- `pause_remote_job(job_id)` — returns an error for the bohr job provider (no
  CLI pause support for batch jobs).
- `get_remote_job_status` may include a `user_control` payload when the user
  terminated the job from the web UI. Treat it as authoritative: stop, do not
  resubmit, and report `needs_replanning` with the job ID and observed state.
