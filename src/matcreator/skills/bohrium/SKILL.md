---
name: bohrium
description: Configure Bohrium access and discover Batch Job machines with bohr CLI. Use the remote-job skill for durable tracked Batch Job and interactive sandbox submission.
metadata:
  tools:
    - run_bash
  tags: [bohrium, hpc, job-submission, cloud-computing]
---

# Bohrium Cloud Job Management

Configure access to [Bohrium](https://bohrium.com) and discover resources via
`bohr`. For new computation, load the `remote-job` skill and use tracked tools
so the session can monitor and reattach without duplicate submission.

## Prerequisites

### 1. Verify bohr CLI installation
Verify: `bohr version`

If not installed, run the following command to install it:
- **Linux/macOS**: `curl -fsSL https://bohrium.com/download/bohr | sh`


### 2. Verify user access

```bash
test -n "$ACCESS_KEY" && echo "ACCESS_KEY is set"
```
If not set, notify the user to provide the access key as an environment variable, or set an environment variable
through MatCreator's WebUI.

Tell the users that access key can be found in the Bohrium main dashboard under `User Profile` -> `Access Key`.

### 3. Get your project ID

First, try
```bash
echo $BOHRIUM_PROJECT_ID
```

If not set, then run:
```bash
bohr project list --json
# Note the project ID you want to use
```
Ask the user to choose the billing project if it is not already specified.
Never select the first project automatically. Pass `project_id` explicitly or
set `BOHRIUM_PROJECT_ID`; tracked Batch Job submission requires a resolved ID
even though the raw CLI has a default.

To the users that the project ID can also be found in the Bohrium Cloud dashboard under `Projects`.

## Machine Types
For new Batch Jobs, discover current selectors and core/memory counts with:

```bash
bohr batchjob machine list -o json
```

Choose exactly one verified `machine_type` or `sku_id`. Do not use legacy
node lists, historical SKU tables, or sandbox catalogs for Batch Jobs.
Interactive sandboxes still use `bohr sandbox template list --json`; their
workflow is unchanged.

## Job submission and management


Use `submit_bohr_batchjob` from `remote-job`, with `name`, `image`, `command`,
exactly one machine selector, and the resolved project. Optional `input_path`
accepts a **workspace-relative** regular file or nonempty directory (no
symlinks/special files); its contents land at the root of the remote working
directory. The adapter runs matching `--dry-run` preflight before local input
submission.
Declare retained paths and logs in `out_files` (a list), with duration strings
`max_run_time="24h"` and `max_wait_time="30m"` by default.

Read the [tracked Batch Job reference](../remote-job/references/bohr-batchjob-ref.md).
For VASP, also read the [VASP Batch Job reference](../vasp-pymatgen/references/bohr-batchjob.md).
Record durable `job_id` and provider `batchjob_id`; use the durable ID in all
tracked status/control/collection tools. For queued/running jobs return
`needs_replanning`, not an in-step polling loop.


## Tips and Pitfalls

- **Always check machine availability** before submitting — popular GPU configs may be out of stock
- **Retain outputs explicitly** — tool `out_files` is a list; CLI `--out-file`
  is repeatable. Include computation logs as well as scientific results.
- **Set explanatory job names** — job name should indicate its type, content and the variation branch
    (for example, "SiO2-md-1000K-10000steps-repeat-2") to make tracking easier with many jobs.
- **Download results promptly** — completed jobs are only retained for a limited time on Bohrium cloud!
- **Wrap complex commands in a shell script** — write a shell script, upload, and replace command with `bash script.sh`.
      This is more reliable and maintainable than long inline `--command` strings.
- **GPU jobs need GPU-enabled images** — not all images have CUDA/cuDNN.
- **MPI jobs** — use `mpirun -np N` where N matches your machine's CPU cores.
- **Memory-intensive jobs** — pick machines with higher memory ratio (e.g., c8_m64 vs c8_m8)
- **Noninteractive output** — use `-o json` for Batch Job discovery/inspection,
  and the documented `--json` flag for sandbox template discovery.
- **Errors and state** — use semantic `status_name`, `terminal`,
  `errorMessage` / `errorCode`, never numeric status or `exitCode`. Preserve
  prepared-job inspection guidance after upload/final-submit errors; do not
  blindly retry a potentially created job.
- **Collection** — choose a new, nonexistent directory. CLI
  `bohr batchjob download <batchjob_id> --dest <new-directory>` safely extracts
  and installs results; do not manually unzip or pre-create the directory.
  Allow the bounded download timeout (default two hours), not a short timeout.
- **Use embedded help** — `bohr batchjob submit --help` and
  `bohr skills read bohr-batchjob references/commands.md` document this API.

## Troubleshooting

| Issue | Solution                                  |
|-------|-------------------------------------------|
| Login fails | `bohr login` again, check credentials     |
| Machine not available | Try a different SKU or wait               |
| Job stuck in pending | Check quota, try other machines           |
| No output files | Check logs for errors, verify command ran |
| Out of memory | Use machine with more RAM                 |

## References
- [Tracked Batch Job reference](../remote-job/references/bohr-batchjob-ref.md) — active submission and lifecycle guidance.
- [references/bohrium-cli-ref.md](references/bohrium-cli-ref.md) — historical legacy CLI material only; not for new tracked jobs.
- [references/bohrium-machines-ref.md](references/bohrium-machines-ref.md) — historical legacy SKU table only; not a Batch Job catalog.
- Full docs online: https://bohrium.com/docs/cli

Legacy `bohr_job` records remain inspectable but operations are unsupported.
Never reinterpret legacy IDs or auto-resubmit old jobs. Respect user controls
and reattach to existing tracked jobs before taking any new action.
