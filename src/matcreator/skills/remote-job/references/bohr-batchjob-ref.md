# submit_bohr_batchjob Reference (Batch Job)

Provider: `bohr_batchjob`, backed exclusively by `bohr batchjob`. Use tracked
tools for submission and control; CLI examples below explain the adapter
contract, not an alternative submission to run alongside the tool.

## Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | yes | Explanatory name (type, content, variation), e.g. `SiO2-md-1000K-run2`. |
| `image` | yes | Explicit container image; GPU jobs need a GPU-enabled image. |
| `command` | yes | The ENTIRE computation — there is no interactive exec afterward. |
| `machine_type` | exactly one selector | Machine type from the Batch Job catalog; mutually exclusive with `sku_id`. |
| `sku_id` | exactly one selector | SKU ID from the Batch Job catalog; mutually exclusive with `machine_type`. |
| `project_id` | resolved value required | Falls back to `BOHRIUM_PROJECT_ID`; missing both is an error. Never silently select a project. |
| `input_path` | no | Workspace-relative path to a regular file or nonempty directory staged once as job input; a directory's contents land at the root of the remote job's working directory. |
| `out_files` | no | List of result paths to retain, including outputs AND logs; not a comma-separated string. |
| `max_run_time` | no | Duration string, default `"24h"`. |
| `max_wait_time` | no | Queue-wait duration string, default `"30m"`. |

Durations use CLI Go-duration syntax, e.g. `90s`, `30m`, `2h`, `1h30m`,
with a minimum of one second. CLI preflight validates them; do not pass
numeric seconds or legacy minute counts.

Pass `out_files` as a real JSON array of path strings. A value accidentally
serialized as one string (JSON or Python literal) is coerced by the tool, but
malformed shapes are rejected before any job record is created.

Returns durable `job_id` (use for every tracked tool call) and provider-side
`batchjob_id` (the nonempty string `jobId` returned by the CLI). These IDs are
not interchangeable. Submission is idempotent per session, node, and name:
replay returns the existing record, not a second job.

Capabilities: submit-time inputs and batch output collection only — no
interactive command execution, no per-command file upload/download, no
pause/resume. Only `BATCH_COLLECT` is supported.

## Discovery and input preflight

```bash
bohr batchjob machine list -o json
```

Select exactly one verified machine type or SKU ID. Do not use legacy node
lists, hardcoded historical SKUs, or the sandbox machine catalog. See the
`bohrium` skill for access setup; select an explicit image compatible with the
workload and machine.

`input_path` maps to `--input` and is resolved relative to the step workspace —
the adapter runs the CLI with the workspace as its working directory, so pass
`si_scf` or `./si_scf`, never an absolute or fabricated path. It accepts a
regular file or nonempty directory inside the workspace. A directory's contents
are unpacked at the root of the remote job's working directory, so `command`
references them by bare names (hence `--input ./calculation` with
`--command 'bash run.sh'` below). Root/nested symlinks, empty directories,
FIFOs, sockets, devices and other special files are rejected. Keep only
intended input files in the staged tree. The adapter runs matching submit
arguments with `--dry-run` before submitting local input and stops if
preflight fails.

The equivalent CLI shape is:

```bash
bohr batchjob submit --name "$NAME" --image "$IMAGE" \
  --machine-type "$MACHINE_TYPE" --project-id "$BOHRIUM_PROJECT_ID" \
  --command 'bash run.sh' --input ./calculation \
  --out-file result.json --out-file stdout.log --out-file stderr.log \
  --max-run-time 24h --max-wait-time 30m --dry-run -o json
```

Use `--sku-id` INSTEAD OF `--machine-type` when selecting a SKU. `--out-file`
is repeatable. The real submit removes only `--dry-run`; tracked submission
performs it once. Do not submit manually in addition to calling the tool.

Input upload or final-submit errors may leave a `prepared` job. Preserve the
reported identity and inspection command; do not automatically retry even
when a durable record lacks an external ID. Ambiguous failures need inspection,
not speculative resubmission.

## Lifecycle

For an existing externally submitted Batch Job, register it with
`attach_bohr_batchjob(batchjob_id="<provider-jobId>")` instead of submitting.
This verifies its status and returns the durable `job_id` without creating
remote compute. Repeated attachment reuses the owning session's existing record.
Do not use this to import legacy IDs or take over another session's tracked job.

1. Call `submit_bohr_batchjob` once and record `job_id` and `batchjob_id`.
2. Call `get_remote_job_status(job_id)`. If queued/running, return
   `needs_replanning` with the job identity and observation, not a polling loop.
3. Call `collect_remote_job_outputs(job_id, destination_path)` to pull the
   declared outputs after `status: succeeded`. `destination_path` must be a
   new, nonexistent directory inside the workspace. Never pre-create it or
   merge into an input/results directory. Already-collected replay is a durable
   no-op and returns the previous collection rather than downloading elsewhere.
   A failed collection (e.g. an occupied destination) returns the tracked job
   to `succeeded` with the error recorded — the computation did NOT fail;
   retry collection with a new directory.

Monitoring belongs to the harness, not the agent: the running control plane
polls tracked jobs and durably schedules an agent turn on completion or failure,
including scheduling/runtime timeout. Busy sessions defer the notification;
stopped sessions do not restart automatically. When notified, read the current
status and process the existing outputs or failure. Do not repeat side effects.

Never call `run_remote_job_command`, `start_remote_job_command`,
`poll_remote_job_command`, `upload_remote_job_input`, or
`download_remote_job_output` on a Batch Job.

The CLI downloads and safely extracts the archive, then atomically installs
the new directory. Do not manually unzip the results or pre-create the target.
Its bounded download timeout defaults to two hours; do not wrap collection in
a short 60–120 second timeout. Download promptly while results remain available.

Provider-side inspection/control uses positional IDs:

```bash
bohr batchjob describe "$BATCHJOB_ID" -o json
bohr batchjob download "$BATCHJOB_ID" --dest ./new-results -o json
bohr batchjob kill "$BATCHJOB_ID" -o json
```

For tracked jobs, prefer the generic status, collection, and termination tools
so durable state and ownership controls are preserved.

## Semantic status and errors

Branch on normalized tool `status` and provider `status_name` / `terminal`,
never numeric backend status or `exitCode`.

| Provider `status_name` | Normalized state / action |
|---|---|
| `prepared` | `queued`, but submission is incomplete: inspect; never submit again automatically. |
| `pending` | `queued`; return `needs_replanning`. |
| `active` / `running` | `running`; return `needs_replanning`. |
| `succeeded` | `succeeded`; collect and validate the scientific outputs. |
| `failed` | `failed`; inspect `errorMessage` / `errorCode`, not an assumed successful exit. |
| `deleted` / `killed` | `cancelled`; cannot resume. |
| `unknown` or unrecognized | Nonterminal observation; no guessed transition or successful outcome. |

Execution success is not scientific convergence or proof that all expected
artifacts were retained. Inspect downloaded outputs before reporting success.

## Re-attach protocol ("REMOTE JOB ALREADY SUBMITTED")

If the step's prior context says a batch job was already submitted for this
exact step:

1. Call `get_remote_job_status` with the given `job_id` FIRST.
2. NEVER call `submit_bohr_batchjob` again for that step.
3. If `status: succeeded`, call `collect_remote_job_outputs` and report
   the validated result. If already `collected`, reuse the recorded artifacts.
4. If still running (`queued`/`running`), return `needs_replanning` quoting
   the `job_id` and status rather than looping polls inside one step.

## Controls and user intervention

- `terminate_remote_job(job_id)` — requests confirmed cancellation. The CLI
  waits by default; do not use `--no-wait`. `ok: true` with `confirmed: false`
  is acceptance only, not evidence of termination; surface the failure.
- `pause_remote_job(job_id)` — returns an error for the Batch Job provider (no
  CLI pause support for batch jobs).
- `get_remote_job_status` may include a `user_control` payload when the user
  terminated the job from the web UI. Treat it as authoritative: stop, do not
  resubmit, and report `needs_replanning` with the job ID and observed state.

## Unsupported legacy records

Legacy `bohr_job` records remain readable as history, but their provider
operations are unsupported. `submit_bohr_job` has been removed, with no alias.
Never reinterpret legacy external IDs as Batch Job IDs, auto-resubmit old
records, or bypass a user-control event. Report the unsupported-provider error
and return `needs_replanning`; any replacement requires an explicit new plan.
