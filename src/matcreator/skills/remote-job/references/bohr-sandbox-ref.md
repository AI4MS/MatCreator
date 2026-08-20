# submit_bohr_sandbox Reference (Interactive Sandbox)

Detailed parameters and protocols for the interactive bohr-sandbox path. For
machine SKUs, images, access-key setup, and project-ID discovery, see the
`bohrium` skill.

## Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `template` | yes | e.g. `doc-compiler`; list with `bohr sandbox template list`. Ask the user if unknown. |
| `project_id` | no | Falls back to the `BOHRIUM_PROJECT_ID` environment variable. |
| `gpu` | no | GPU shortcut template: `4090`, `5090`, or `l20`. |
| `image` | no | Override the template's container image. |
| `timeout` | no | Sandbox lifetime in seconds. |
| `never_timeout` | no | Keep the sandbox alive until terminated. |
| `env` | no | Dict of environment variables set inside the sandbox. |

Returns `job_id` (use it for every later call) and `sandbox_id` (the
provider-side ID). Idempotent per session, node, and template: repeating the
call for the same step returns the existing record instead of creating a
second sandbox.

Capabilities: interactive command execution and per-file transfer. No pause —
terminate when done.

## Discovering available templates

`template` names are not fixed — list what's actually available on the
platform with `run_bash` before guessing or asking the user unnecessarily:

```bash
timeout 60 bohr sandbox template list --json
```

- **Always pass `--json`.** Without it, `bohr sandbox template list` opens an
  interactive TUI that hangs forever in a non-interactive `run_bash` call —
  never run it without `--json`.
- Wrap the call in `timeout` — the Bohrium API can be slow.
- Pipe through `jq` to pull out just the names, e.g.
  `bohr sandbox template list --json | jq -r '.[].name'` (inspect one raw
  entry first if the field name differs).
- If a GPU shortcut (`4090`/`5090`/`l20`) covers the need, prefer passing
  `gpu` to `submit_bohr_sandbox` instead of a raw `template` — it resolves to
  the right GPU template without a separate lookup.
- If the listing is empty, unclear, or the user already named a template
  they know exists, ask the user to confirm rather than guessing.

## Lifecycle

1. Call `submit_bohr_sandbox` once for the current step and record `job_id`.
2. Upload each workspace input file with `upload_remote_job_input`.
3. Run commands — see "Background command walkthrough" below.
4. Download each output file with `download_remote_job_output`.
5. Call `terminate_remote_job` to release the sandbox when work is complete.

## Background command walkthrough

```
start_remote_job_command(job_id, "source /opt/env.sh && mpirun -np 32 vasp_std", user="root")
# → returns immediately with a durable handle (log_path, exit_path)

poll_remote_job_command(job_id)
# → {"running": true, "log_path": ...} while executing — do other useful work
#   or return needs_replanning between polls; do not spin in a tight loop.
# → {"running": false, "exit_code": 0, "output_tail": "...", "log_path": ...}
#   once finished. output_tail is only the last few KB — for the full log use
#   download_remote_job_output on log_path.
```

The handle is persisted on the job record, so a fresh step attempt after a
crash or timeout finds the same in-flight command via
`get_remote_job_status` + `poll_remote_job_command`. One background command
per job at a time: starting a new one overwrites the tracked handle of the
previous one.

`run_remote_job_command` BLOCKS until the command finishes, with no timeout —
only use it for commands expected to finish well under a minute.

## File transfer

- `upload_remote_job_input(job_id, source_path, destination_path)` —
  `source_path` must resolve inside the current workspace;
  `destination_path` is an absolute remote path (e.g. `/home/user/POSCAR`).
- `download_remote_job_output(job_id, source_path, destination_path)` —
  `source_path` is an absolute remote path; `destination_path` must resolve
  inside the workspace.
- One file per call. Command stdout is truncated (~4000 chars) and mangles
  binary data — never substitute `run_remote_job_command` + `cat` for a
  download.

## Re-attach protocol ("REMOTE JOB ALREADY SUBMITTED")

If the step's prior context says a sandbox was already submitted for this
exact step:

1. Call `get_remote_job_status` with the given `job_id` FIRST.
2. NEVER call `submit_bohr_sandbox` again for that step — it would duplicate
   a running sandbox.
3. If the status `snapshot` contains `background_command`, call
   `poll_remote_job_command` before issuing anything new. Re-running a
   non-idempotent computation can corrupt output or double-charge compute.
4. If the command finished, download outputs with `download_remote_job_output`
   and report success.
5. If still running, return `needs_replanning` quoting the `job_id` and status.

## Controls and user intervention

- `terminate_remote_job(job_id)` — releases the sandbox.
- `pause_remote_job(job_id)` — returns an error for the bohr sandbox provider
  (the `bohr` CLI has no sandbox pause subcommand) — terminate instead.
- `get_remote_job_status` may include a `user_control` payload when the user
  paused/terminated from the web UI. Treat it as authoritative: stop, do not
  resubmit, and report `needs_replanning` with the job ID and observed state.
