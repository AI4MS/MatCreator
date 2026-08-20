---
name: remote-job
description: Submit, monitor, and control tracked remote jobs on the Bohrium platform — interactive sandboxes (submit_bohr_sandbox) and batch/HPC jobs (submit_bohr_job). Jobs persist against the session with a durable job_id, so status, commands, file transfer, and termination survive reconnects and step restarts.
metadata:
  tools:
    - submit_bohr_sandbox
    - submit_bohr_job
    - get_remote_job_status
    - run_remote_job_command
    - start_remote_job_command
    - poll_remote_job_command
    - upload_remote_job_input
    - download_remote_job_output
    - collect_remote_job_outputs
    - pause_remote_job
    - terminate_remote_job
  dependent_skills:
    - bohrium
  tags: [remote-job, sandbox, batch, bohrium, hpc]
---

# Remote Job Management (Bohrium)

Use the tracked remote-job tools for any computation that must run on the
Bohrium platform. Each submission persists a durable `job_id` against the
current session and graph node, so the FastAPI frontend can monitor and
control the job, and a restarted step can re-attach instead of resubmitting.
Submissions are idempotent per step: repeating the same submit call for the
same step returns the existing job record.

## Choosing sandbox vs. batch

- `submit_bohr_sandbox` — interactive sandbox via the `bohr` CLI. Use it when
  the work needs command execution or file transfer after submission. An
  explicit `template` is required (ask the user if unknown; discover options
  with `run_bash("bohr sandbox template list --json")` — the `--json` flag is
  mandatory or the command hangs on an interactive TUI). Optional: `gpu`
  shortcut (`4090`/`5090`/`l20`), `image`, `timeout`/`never_timeout`, `env`.
  `project_id` falls back to the `BOHRIUM_PROJECT_ID` environment variable.
- `submit_bohr_job` — fire-and-forget batch/HPC job via `bohr job submit`.
  There is no interactive execution: the entire computation must be expressed
  in `command`, with inputs staged once via `input_directory`. Requires
  `job_name`, `machine_type`, and `image_address` — see the `bohrium` skill
  for machine SKUs, images, and project-ID discovery.

## Sandbox lifecycle

1. Call `submit_bohr_sandbox` once for the current step and record the
   returned `job_id` in the step result.
2. Upload each workspace input file with `upload_remote_job_input`
   (`source_path` must resolve inside the workspace; `destination_path` is an
   absolute sandbox path such as `/home/user/input.in`).
3. Run commands — see "Short vs. long commands" below.
4. Download each output file with `download_remote_job_output`. This is the
   only reliable way to retrieve large or binary outputs.
5. Call `terminate_remote_job` to RELEASE the sandbox when work is complete.

## Batch lifecycle

1. Call `submit_bohr_job` once for the current step and record the `job_id`.
2. Poll `get_remote_job_status` until it reports `status: succeeded`.
3. Call `collect_remote_job_outputs` to pull the declared outputs into the
   workspace (repeat calls are durable no-ops).

Never call `run_remote_job_command`, `start_remote_job_command`, or
`upload_remote_job_input` on a batch job — the provider has no interactive
execution and returns an error.

## Short vs. long commands (CRITICAL)

- `run_remote_job_command` BLOCKS until the command finishes, with no timeout.
  Only use it for commands expected to finish well under a minute (`ls`,
  `grep`, `mkdir`, checking a file).
- For any real computation (training, `vasp_std`/`mpirun`, anything that might
  take more than a minute), use `start_remote_job_command` and then
  `poll_remote_job_command`. The background command is tracked durably on the
  job: after a step timeout, crash, or lost connection, poll the same
  `job_id` first — never re-run a computation just because you lost track of
  it.
- There is at most one in-flight background command per job; poll the current
  one to completion before starting another.

## File transfer limits

Each upload/download call streams exactly one file. Command output is
truncated (around 4000 characters) and corrupts binary content — never move
large files with `cat`/`cp` through `run_remote_job_command`; use
`download_remote_job_output` instead.

## Monitoring and controls

- `get_remote_job_status` reads the persisted provider snapshot for the job.
- `terminate_remote_job` releases a sandbox or cancels a batch job.
- `pause_remote_job` is NOT supported by either bohr provider (the `bohr` CLI
  has no sandbox pause subcommand) — terminate instead if the job must stop.
- A `user_control` entry in the status means the user paused or terminated
  the job from the frontend: do not retry or resubmit; report the observed
  state.

## When to return needs_replanning

If the job is still running, or was paused/terminated by the user, return
`submit_step_result(status="needs_replanning", ...)` quoting the `job_id` and
the observed state instead of looping on polls inside one step.

## Reference

For full parameter tables, the re-attach protocol, and worked examples, load
whichever submission method applies to the current step:

```
load_skill_resource(skill_name="remote-job", path="references/bohr-sandbox-ref.md")
load_skill_resource(skill_name="remote-job", path="references/bohr-job-ref.md")
```
