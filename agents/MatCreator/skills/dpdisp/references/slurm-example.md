# Slurm + SSHContext Example

**User request:** Run `run_simulation.sh` on a Slurm cluster. Load username from `$HPC_USER` and workspace path from `$HPC_WORKDIR`. Reuse `resource_defaults.json` for base compute settings; add the `debug` queue.

## `submission.template.json`

```json
{
  "work_base": ".",
  "machine": {
    "batch_type": "Slurm",
    "context_type": "SSHContext",
    "remote_profile": {
      "hostname": "login.cluster.edu",
      "username": "${HPC_USER}",
      "port": 22
    },
    "remote_root": "${HPC_WORKDIR}/dpdisp_run"
  },
  "resources": {
    "$ref": "resource_defaults.json",
    "queue_name": "debug",
    "group_size": 1
  },
  "task_list": [
    {
      "command": "bash run_simulation.sh",
      "task_work_path": "task_000",
      "forward_files": ["run_simulation.sh", "input.dat"],
      "backward_files": ["result.out", "log", "err"]
    }
  ]
}
```

## Commands

```bash
envsubst '${HPC_USER} ${HPC_WORKDIR}' < submission.template.json > submission.json
uv run -m json.tool submission.json >/dev/null
uvx --with dpdispatcher dargs check --allow-ref -f dpdispatcher.entrypoints.submit.submission_args submission.json
tmux new-session -d -s dpdisp_job "uvx --from dpdispatcher dpdisp submit --allow-ref submission.json"
tmux ls
```

Then poll until the tmux session exits and `task_000/result.out`, `task_000/log`, and `task_000/err` exist locally (see "Monitoring a tmux Job" in SKILL.md).
