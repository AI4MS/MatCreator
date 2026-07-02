# ASE / DeePMD Bohrium Submission Reference

See the `bohrium` skill for general Bohrium usage: login, project ID discovery, machine availability, job monitoring, and result download.
This file covers only **ASE / DeePMD-specific** submission details.

## ASE / DeePMD-specific environment variables

| Variable | Description | Example |
|---|---|---|
| `BOHRIUM_DEEPMD_ASE_MACHINE` | Machine/scass type for ASE / DeePMD jobs | `c32_m128_cpu` |
| `BOHRIUM_DEEPMD_ASE_IMAGE` | Container image URI providing ASE + DeePMD | `registry.dp.tech/dptech/deepmd-kit:3.1.3` |
| `DEEPMD_MODEL_PATH` | Default local model path, used when `--model_path` is omitted | `/data/models/dpa2.pt` |

General Bohrium variables such as `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD`, and `BOHRIUM_PROJECT_ID` are shared with the `bohrium` skill.

Check required values before preparing a large batch:

```bash
python ase_deepmd_tools.py show_model_path
bohr project list --json
bohr node list
```

## Submission workflow

Prepare MD or relax job directories first with `ase_deepmd_tools.py prepare_md` or `ase_deepmd_tools.py prepare_relax`.
The prepare command returns:

- `batch_dir`: common parent containing `run_ase_job.py`, the shared `model.pt` when using a local model, and all per-job directories.
- `calc_dir_list`: individual job directories to add to `task_list`.

Use the returned `batch_dir` as `work_base`. Use each job directory basename as `task_work_path`.

## MD jobs

`submission.template.json`:

```json
{
  "work_base": "<batch_dir>",
  "machine": {
    "batch_type": "Bohrium",
    "context_type": "BohriumContext",
    "local_root": ".",
    "remote_profile": {
      "email": "${BOHRIUM_EMAIL}",
      "password": "${BOHRIUM_PASSWORD}",
      "program_id": ${BOHRIUM_PROJECT_ID},
      "input_data": {
        "job_type": "container",
        "log_file": "log",
        "scass_type": "${BOHRIUM_DEEPMD_ASE_MACHINE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_DEEPMD_ASE_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "forward_common_files": ["model.pt", "run_ase_job.py"],
  "task_list": [
    {
      "command": "python ../run_ase_job.py",
      "task_work_path": "<md_job_dir_name>",
      "forward_files": ["structure.extxyz", "ase_input.json"],
      "backward_files": ["trajectories", "md_simulation.log", "status.json", "log", "err"]
    }
  ]
}
```

- `work_base` must be the `batch_dir` returned by `prepare_md`.
- `task_work_path` is the basename of each job directory, relative to `batch_dir`, e.g. `md_20240324120000_abc12345`.
- Add one `task_list` entry per directory in `calc_dir_list`.
- `forward_common_files` uploads `model.pt` and `run_ase_job.py` once from `batch_dir` to the remote working directory, one level above each task directory.
- `ase_input.json` references the local model as `"../model.pt"`, and the task command calls `python ../run_ase_job.py` for the same reason.
- Omit `model.pt` from `forward_common_files` when `--remote_model_path` was used during preparation.

Before submitting, verify shared files exist in `batch_dir`:

```bash
ls "<batch_dir>/model.pt" "<batch_dir>/run_ase_job.py"
```

If `--remote_model_path` was used, only `run_ase_job.py` must exist locally.

## Relax jobs

Use the same `submission.template.json` structure as MD jobs. Replace each task entry with:

```json
{
  "command": "python ../run_ase_job.py",
  "task_work_path": "<relax_job_dir_name>",
  "forward_files": ["structure.extxyz", "ase_input.json"],
  "backward_files": [
    "structure_optimized.cif",
    "structure_optimization_traj.extxyz",
    "optimization.log",
    "status.json",
    "log",
    "err"
  ]
}
```

## File manifests

| Level | Files |
|---|---|
| `forward_common_files` at `work_base` / `batch_dir` | `model.pt`* `run_ase_job.py` |
| `forward_files` per MD task | `structure.extxyz` `ase_input.json` |
| `forward_files` per Relax task | `structure.extxyz` `ase_input.json` |
| `backward_files` MD | `trajectories` `md_simulation.log` `status.json` `log` `err` |
| `backward_files` Relax | `structure_optimized.cif` `structure_optimization_traj.extxyz` `optimization.log` `status.json` `log` `err` |

(*) Omit `model.pt` when `--remote_model_path` was used during preparation.

## Substitute, validate, and submit

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_DEEPMD_ASE_MACHINE} ${BOHRIUM_DEEPMD_ASE_IMAGE}' \
    < submission.template.json > submission.json

uv run -m json.tool submission.json >/dev/null
uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json

# Always use --with oss2 for Bohrium jobs
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
```

For long-running MD jobs, wrap submission in `tmux`:

```bash
tmux new-session -d -s ase_md \
    "uvx --from dpdispatcher --with oss2 dpdisp submit submission.json"
tmux ls
```

## Monitoring and result download

Use the `bohrium` skill for general monitoring and download commands. For dpdispatcher submissions, check generated job metadata and logs in the working directory first, then use the Bohrium job or job group IDs with `bohr job log`, `bohr job download`, or `bohr job_group download` as appropriate.

After results are available locally, collect them with the ASE / DeePMD tool:

```bash
python ase_deepmd_tools.py collect_md --calc_dirs /tmp/ase_deepmd_jobs/md_*
python ase_deepmd_tools.py collect_relax --calc_dirs /tmp/ase_deepmd_jobs/relax_*
```

## Handling failed jobs

Inspect `status.json`, `log`, and `err` in the downloaded task directory. Download Bohrium logs if needed:

```bash
bohr job log --job_id <JOB_ID>
```

Modify the input, image, machine type, or model path, then prepare or submit again.