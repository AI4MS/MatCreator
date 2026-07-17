---
name: e2b
description: Submit and manage tracked remote E2B sandboxes on Bohrium platform.
metadata:
  tools:
    - submit_e2b_sandbox
    - get_e2b_job_status
    - pause_e2b_sandbox
    - terminate_e2b_sandbox
  tags: [e2b, remote-job, sandbox, bohrium]
---

# E2B Remote Sandbox Management

Use the E2B tools for remote sandbox work. They persist the sandbox ID against
the current session and graph node, enabling the FastAPI frontend to monitor and
control the sandbox even after the agent or browser reconnects.

## Submission

1. Choose `template` explicitly for every `submit_e2b_sandbox` call. When the
   template name is unknown, run `lbg sdbx template ls -q` to list available
   templates. Install the command with `pip install -U --pre lbg` when needed.
2. Call `submit_e2b_sandbox` once for the current step. It is idempotent for
   the current session, node, and template.
3. Use `upload_e2b_input` for workspace files, then use `run_e2b_command` for
   the sandbox command. Both require the returned `job_id`.
4. Record the returned `job_id` in the step result and use it for status and
   sandbox control.
5. Call `terminate_e2b_sandbox` to RELEASE the sandbox when work is complete. 

## Controls

- `get_e2b_job_status` reads the persisted provider snapshot.
- `pause_e2b_sandbox` preserves the sandbox and pauses remote execution.
- `terminate_e2b_sandbox` releases the sandbox.

The frontend can issue the same controls. Before continuing dependent work after
a pause or termination, return a `needs_replanning` step result with the job ID
and the observed state.