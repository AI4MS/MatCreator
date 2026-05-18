---
name: dpdisp
description: Run Shell commands as computational jobs, on local machines or HPC clusters, through Shell, Slurm, PBS, LSF, Bohrium, etc. USE WHEN the user needs to submit batch jobs to a cluster, run commands on a remote server, execute tasks via job schedulers (Slurm, PBS, LSF), or safely run long-term/background shell commands that require state tracking and auto-recovery.
metadata:
  tools:
    - run_bash
  tags:
    - dpdispatcher
    - bohrium
    - hpc
    - slurm
---

# dpdisp-submit

This skill uses the DPDispatcher tool to run Shell commands as computational jobs, on local machines or HPC clusters, through Shell, Slurm, PBS, LSF, Bohrium, etc.

## Agent responsibilities

1. Collect enough information from the user in plain language.
1. Generate the submission JSON file based on collected user input.
1. Validate `submission.json` before submission.
1. Submit with `uvx --from dpdispatcher dpdisp submit submission.json`.
1. **Poll until the job fully completes and output files are downloaded locally** — do not report success at submission time.
1. Automatically manage job interruptions and retries by relying on built-in state tracking (see the "Resuming Jobs" section for details).
1. **CRITICAL SECURITY CONSTRAINT: DO NOT attempt direct SSH connections.** Never attempt to connect to the remote HPC directly using `ssh`, write custom Paramiko/Fabric Python scripts, or manually execute remote commands. Just generate the `submission.json` and use the `dpdisp submit` tool. DPDispatcher will handle all remote connections, file transfers, and job management safely.

## Autonomous Information Gathering & User Prompts

Before this step, always execute `uvx --with dpdispatcher dargs doc dpdispatcher.entrypoints.submit.submission_args` to learn what information needs to be filled.

If information is missing, ask questions users can understand, for example:

- Where should this run: your local machine or a remote HPC cluster?
- Are there any existing configuration files?
- Is any sensitive information in the environment variables?
- What shell command should be executed?
- How many CPUs/GPUs/nodes do you need?
- Which queue/partition/account should we use (if applicable)?
- Which input files should be uploaded, and which output files should be collected?

## Generate `submission.json` from user input

According the result of `uvx --with dpdispatcher dargs doc dpdispatcher.entrypoints.submit.submission_args`, translate user answers into:

- `machine` (where/how to run),
- `resources` (compute resources),
- `task_list` (which shell commands/files to run).

If the user indicates that a specific value (like a username, token, or remote path) should be read from a local environment variable, format that value in the JSON template as `${ENV_VAR_NAME}`.
*Example:* `"remote_root": "${USER_HPC_WORKSPACE}"`

### Handling Environment Variables

If the user specifies values that must be loaded from local environment variables (e.g., sensitive tokens, dynamic paths), do **not** write them directly into the final JSON. Instead:

1. Generate a `submission.template.json` file using the `${VAR_NAME}` syntax **only for the variables you intend to substitute**.
1. Use `envsubst` with an explicit variable list to inject only those variables and create the final file:
   `envsubst '${USER_HPC_WORKSPACE}' < submission.template.json > submission.json`
1. **CRITICAL SECURITY CONSTRAINT: DO NOT read or print the contents of the newly generated `submission.json` file.** Once `envsubst` replaces the variables, the file contains raw sensitive data.

If multiple environment variables are needed, list them all explicitly:
`envsubst '${USER_HPC_WORKSPACE} ${USER_OTHER_VAR}' < submission.template.json > submission.json`
If no environment variables are needed, simply generate `submission.json` directly.

### Simple local shell tasks

When the user asks for a simple local shell task, prefer these defaults to avoid common failures:

- `machine.context_type = "LazyLocalContext"`
- `machine.batch_type = "Shell"`
- `task_list[0].task_work_path = "."` (avoid non-existing subdirectory failures)
- `resources.group_size = 1`

## Required commands

### Core Flow

```bash
# 1) Print full submission schema
uvx --with dpdispatcher dargs doc dpdispatcher.entrypoints.submit.submission_args

# 2) [Optional] Substitute environment variables if a template was generated
envsubst '${USER_VAR} ${USER_OTHER_VAR}' < submission.template.json > submission.json

# 3) Syntax check JSON
uv run -m json.tool submission.json >/dev/null

# 4) Validate generated submission.json
uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json

# 5) Submit (wrap in tmux for long-running jobs)
tmux new-session -d -s dpdisp_job "uvx --from dpdispatcher dpdisp submit submission.json"

# If using Bohrium/DP Cloud (context_type: BohriumContext), add --with oss2:
tmux new-session -d -s dpdisp_job "uvx --from dpdispatcher --with oss2 dpdisp submit submission.json"
```

### Monitoring a tmux Job

After launching the tmux session you **must** poll until it finishes AND verify that output files are present locally before declaring the step complete:

```bash
# Poll every 30 s; exit when the session is gone
while tmux has-session -t dpdisp_job 2>/dev/null; do
  echo "Still running... $(date)"
  sleep 30
done
echo "tmux session ended"

# Read captured output to check for errors
tmux capture-pane -t dpdisp_job -p 2>/dev/null || true

# Verify expected backward_files exist locally (adjust paths as needed)
ls -lh <task_work_path>/log <task_work_path>/err 2>/dev/null && echo "Outputs present" || echo "WARNING: outputs missing"
```

Only after the session has exited **and** the expected output files exist is the job truly complete. If outputs are missing after the session ends, re-run `dpdisp submit` to resume (see "Resuming Jobs").

### More Useful Flags

- **`--dry-run`**: Parses the configuration and validates the schema without submitting. Useful for a final safety check.
- **`--allow-ref`**: Required when `submission.json` uses `{"$ref": "other.json"}` for reusable config snippets. Pass it to **all** related commands (`dargs check` and `dpdisp submit`).

## Completion criteria

A job is **fully completed** only when **both** of the following are true:

- The backend tasks have finished successfully.
- All required output files (e.g., `log`, `err`, result files) have been retrieved to the local `task_work_path`.

Submitting a job is **not** completing it. Never call `submit_step_result` with `status=success` until outputs are present locally.

## Resuming Jobs (Failure Handling & Recovery)

DPDispatcher is inherently idempotent and features built-in state tracking. It will automatically resume unfinished tasks without duplicating completed ones.

**When to trigger recovery:**

- A job fails, times out, or gets unexpectedly interrupted.
- The user explicitly asks to "resume", "retry", or "recover" a previously interrupted or failed job.
- The Agent's SSH or network connection drops during job monitoring.

**Action:** Do **NOT** modify `submission.json` or attempt to clean up the remote directories. Simply re-execute the exact same submission command in the same directory. The tool will safely skip successful tasks and only resubmit or resume the pending/failed ones.

## Examples

For full worked examples with complete JSON templates and commands, load the reference files:

- **Slurm + SSHContext:** `load_skill_resource(skill_name="dpdisp", path="references/slurm-example.md")`
- **Bohrium (DP Cloud):** `load_skill_resource(skill_name="dpdisp", path="references/bohrium-example.md")`

## What to report back to the user

- A short summary of what the user asked for (where to run, command, resources).
- Submission status (started/running/finished/failed).
- Output locations (for example `log` and `err` when `task_work_path` is `.`).
- If the job encounters an interruption or partial failure, provide the user with detailed information and offer to re-run the command to resume the unfinished tasks.
