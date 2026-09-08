---
name: remote-job
description: Submit, monitor, and control tracked remote jobs on the Bohrium platform — interactive sandboxes (submit_bohr_sandbox) and Batch Jobs (submit_bohr_batchjob). Jobs persist against the session with a durable job_id, so status, commands, file transfer, and termination survive reconnects and step restarts.
metadata:
  tools:
    - submit_bohr_sandbox
    - submit_bohr_batchjob
    - attach_bohr_batchjob
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

For a Batch Job already submitted outside these tools, call
`attach_bohr_batchjob(batchjob_id=...)` once to register it without submitting
new compute. A raw provider ID alone is not a tracked job.

## Choosing sandbox vs. batch

- `submit_bohr_sandbox` — interactive sandbox via the `bohr` CLI. Use it when
  the work needs command execution or file transfer after submission. An
  explicit `template` is required (ask the user if unknown; discover options
  with `run_bash("bohr sandbox template list --json")` — the `--json` flag is
  mandatory or the command hangs on an interactive TUI). Optional: `gpu`
  shortcut (`4090`/`5090`/`l20`), `image`, `timeout`/`never_timeout`, `env`.
  `project_id` falls back to the `BOHRIUM_PROJECT_ID` environment variable.
- `submit_bohr_batchjob` — noninteractive Batch Job via `bohr batchjob submit`.
  There is no interactive execution: the entire computation must be expressed
  in `command`, with inputs staged once via a workspace-relative `input_path`.
  Requires `name`, `image`, `command`, and exactly one of `machine_type` or
  `sku_id`.
  Discover selectors with `bohr batchjob machine list -o json`, not a legacy
  or sandbox machine catalog. `project_id` falls back to `BOHRIUM_PROJECT_ID`
  but must resolve explicitly; never choose a billing project automatically.
  Optional: `out_files` (list of retained paths), `max_run_time="24h"`,
  `max_wait_time="30m"` (duration strings, not numeric seconds).

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

1. Read [the Batch Job reference](references/bohr-batchjob-ref.md). Select an
   explicit image and outputs. `input_path` is relative to the workspace and
   accepts a regular file or nonempty directory; symlinks and special files
   are rejected. A directory's contents appear at the root of the remote
   working directory, so `command` uses bare names like `bash run.sh`. The
   adapter runs a matching CLI `--dry-run` before submitting local input.
2. Call `submit_bohr_batchjob` once and record durable `job_id` and provider
   `batchjob_id`. Use `job_id` for all tracked tools, not the provider ID.
3. Call `get_remote_job_status`. If still queued/running, return
   `needs_replanning` with the identity and observed state; do not loop polls.
4. On `status: succeeded`, call `collect_remote_job_outputs(job_id,
   destination_path)` into a new, nonexistent workspace directory. Do not
   pre-create it or merge with existing inputs. The CLI safely extracts outputs;
   already-collected replay is a durable no-op, not another download.

Provider observations use semantic `status_name` and `terminal`, with
`errorMessage` / `errorCode` for failures — never numeric status or `exitCode`.
`prepared` means submission is incomplete, not permission to submit again.
For upload/final-submit failures or ambiguous responses, preserve any reported
identity and inspection guidance; never blindly retry and risk duplicate jobs.
Unknown states are nonterminal observations, not guessed success.

When prior context says "REMOTE JOB ALREADY SUBMITTED", call
`get_remote_job_status` FIRST and never submit again for that step. Existing
legacy `bohr_job` records remain inspectable but provider operations are
unsupported; never reinterpret their IDs as Batch Job IDs or auto-resubmit them.

Never call `run_remote_job_command`, `start_remote_job_command`,
`poll_remote_job_command`, `upload_remote_job_input`, or
`download_remote_job_output` on a Batch Job — only batch collection is supported,
not interactive execution, per-file transfer, or pause/resume.

## Sandbox short vs. long commands (CRITICAL)

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

## Sandbox file transfer limits

Each upload/download call streams exactly one file. Command output is
truncated (around 4000 characters) and corrupts binary content — never move
large files with `cat`/`cp` through `run_remote_job_command`; use
`download_remote_job_output` instead.

## Monitoring and controls

- The harness polls tracked jobs while the control-plane server is running,
  even when the browser is closed or the step executor has returned.
  Completion, failure (including queue/runtime timeout), and background-command
  completion create durable notifications. The harness invokes the agent in the
  owning session to process results, deferring while another agent turn is busy.
  Do not create a cron job or spend agent turns repeatedly polling.
- On a harness status notification, read `get_remote_job_status` first and
  process the existing job. Reuse collected artifacts; never rerun computation
  or submit a replacement automatically. Explicit user stops suppress wakeups.
- `get_remote_job_status` reads the persisted provider snapshot for the job.
- `terminate_remote_job` releases a sandbox or cancels a batch job.
  A Batch Job kill request accepted with `confirmed: false` does not prove
  termination; report the error/observation rather than claiming it stopped.
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
load_skill_resource(skill_name="remote-job", path="references/bohr-batchjob-ref.md")
```
