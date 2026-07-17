# LAMMPS Bohrium Submission Reference

## Required environment variables

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_DEEPMD_MACHINE` | Machine/scass type, e.g. `c32_m128_cpu` |
| `BOHRIUM_DEEPMD_IMAGE` | Container image URI — includes LAMMPS, default: `registry.dp.tech/dptech/deepmd-kit:3.1.3` |
| `DEEPMD_MODEL_PATH` | Default pretrained DPA3 model path |

> The Bohrium DeepMD image (`registry.dp.tech/dptech/deepmd-kit`) already includes LAMMPS — no separate installation is needed.

## Example submission.template.json

```json
{
  "work_base": "<job_dir>",
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
        "job_name": "${JOB_NAME}",
        "log_file": "log",
        "scass_type": "${BOHRIUM_DEEPMD_MACHINE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_DEEPMD_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "lmp -in in.lammps",
      "task_work_path": ".",
      "forward_files": ["in.lammps", "conf.lmp", "frozen_model.pth"],
      "backward_files": ["log.lammps", "traj.dump", "job_config.json", "log", "err"]
    }
  ]
}
```

## Submission flow

1. Generate `submission.template.json` as above, using `${VARNAME}` for environment variables.
2. Export `JOB_NAME` (see the naming convention in the `bohrium` skill),
   e.g. `export JOB_NAME="lammps-npt-Al-500K"`, then substitute:
   ```bash
   envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_DEEPMD_MACHINE} ${BOHRIUM_DEEPMD_IMAGE} ${JOB_NAME}' \
       < submission.template.json > submission.json
   ```
3. Validate and submit via the `bohrium` skill:
   ```bash
   uv run -m json.tool submission.json >/dev/null
   ```

## Multi-job submission (all frames)

When `--frame -1` produces multiple job directories, submit each independently with `work_base` set to the individual `job_dir`, or list each as a separate task in `task_list`.
