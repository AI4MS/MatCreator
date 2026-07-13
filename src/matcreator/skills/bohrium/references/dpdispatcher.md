# Bohrium Submission via dpdispatcher (Canonical Reference)

All Bohrium actions go through dpdispatcher. The `bohr` shell CLI is **not**
available on this platform (it is bohr.io, a web-deploy tool) — see the
`bohrium` skill warning.

## Environment variables

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `<JOB>_MACHINE` | `scass_type`, e.g. `c32_m128_cpu` or `1 * NVIDIA V100_32g` |
| `<JOB>_IMAGE` | Container image URI, e.g. `registry.dp.tech/dptech/vasp:5.4.4` |

Authentication uses **email + password only**. Do not attempt JWT / OpenAPI
token flows — they return 401.

---

## submission.template.json

A single-task template. Use `${VARNAME}` placeholders; substitute with
`envsubst` before submitting.

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
        "scass_type": "${JOB_MACHINE}",
        "platform": "ali",
        "image_name": "${JOB_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "<remote command>",
      "task_work_path": ".",
      "forward_files": ["<input files>"],
      "backward_files": ["<output files>"]
    }
  ]
}
```

### Required fields & common mistakes

| Field | Value | Mistake |
|---|---|---|
| `machine.local_root` | `"."` | Wrong path → silent upload failure |
| `input_data.job_type` | `"container"` | Missing → submission rejected |
| `input_data.job_name` | descriptive name (see below) | Omit → job shows as unnamed on Bohrium dashboard |
| `input_data.platform` | `"ali"` | Omit → default may be wrong |
| `work_base` | path to the job dir | Must contain every `forward_files` entry |
| `task_work_path` | `"."` or a subdir relative to `work_base` | One entry per task |

### Job naming (MANDATORY)

Every submission **must** set `input_data.job_name`. dpdispatcher passes it
straight through to the Bohrium API (as `jobName`); an unnamed job is
untraceable on the dashboard once you have many concurrent runs.

**Naming convention:** `{skill}-{task}-{descriptor}` — lowercase, hyphen-separated,
no spaces. Substitute the env var `${JOB_NAME}` in the template and export it
before `envsubst`:

```bash
export JOB_NAME="vasp-relax-Al-50frames"   # see examples below
envsubst '${BOHRIUM_EMAIL} ... ${JOB_NAME}' < submission.template.json > submission.json
```

| Skill / task | `job_name` pattern | Example |
|---|---|---|
| VASP relax | `vasp-relax-{formula}` | `vasp-relax-Al2O3` |
| VASP SCF (label batch) | `vasp-scf-{formula}-{N}frames` | `vasp-scf-Al2O3-50frames` |
| LAMMPS MD | `lammps-{mode}-{formula}-{T}K` | `lammps-npt-Al-500K` |
| ASE/DeePMD MD | `ase-md-{formula}-{T}K` | `ase-md-Cu-300K` |
| DPA4 finetune | `dpa4-finetune-{variant}` | `dpa4-finetune-neo` |
| DPA4 freeze | `dpa4-freeze-{variant}` | `dpa4-freeze-neo` |
| DeePMD train | `deepmd-train-{formula}` | `deepmd-train-CuAu` |

> For a multi-task batch, one `job_name` covers the whole group; the per-task
> identity is carried by `task_work_path`. Add the count when useful, e.g.
> `vasp-scf-Al2O3-50frames` for a 50-structure SCF batch.

### Multi-task (batch) template

Submit many tasks under one job group — far fewer API calls than per-task
submission. Shared inputs go in `forward_common_files` (uploaded once).

```json
{
  "work_base": "<batch_dir>",
  "machine": { "...": "same as above" },
  "resources": { "group_size": 1 },
  "forward_common_files": ["shared_file_1", "shared_file_2"],
  "task_list": [
    { "command": "lmp -in in.lammps", "task_work_path": "task_001",
      "forward_files": ["in.lammps", "conf.lmp"], "backward_files": ["log.lammps", "traj.dump"] },
    { "command": "lmp -in in.lammps", "task_work_path": "task_002",
      "forward_files": ["in.lammps", "conf.lmp"], "backward_files": ["log.lammps", "traj.dump"] }
  ]
}
```

> All `task_work_path` entries must share the same `work_base` (dpdispatcher
> requirement).

---

## Submit & wait (blocking — preferred)

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${JOB_MACHINE} ${JOB_IMAGE} ${JOB_NAME}' \
    < submission.template.json > submission.json
uv run -m json.tool submission.json >/dev/null   # validate JSON
```

```python
from dpdispatcher import Submission

sub = Submission.submission_from_json("submission.json")
sub.run_submission(check_interval=30)   # upload → submit → poll → download
```

`run_submission` does everything in one call and writes a recovery
`submission.json` to disk, so a restarted session can resume polling the same
job. **Use this whenever the session can block.**

---

## Submit & poll (non-blocking)

For long jobs where the session may be reset, submit without blocking, then
poll from any later session:

```python
from dpdispatcher import Submission

sub = Submission.submission_from_json("submission.json")
sub.run_submission(exit_on_submit=True, check_interval=30)   # submit only
```

The submission state is serialized next to `submission.json`. In a later
session, recover and poll:

```python
from dpdispatcher import Submission

sub = Submission.submission_from_json("submission.json")
if sub.check_all_finished():
    sub.download_jobs()
else:
    print("still running…")
```

> **Polling primitives:** `Submission.check_all_finished()` (non-blocking) and
> `Submission.run_submission()` (blocking). Do **not** invent methods like
> `job_status()`; do **not** call the internal `get_job_detail` directly.

---

## CLI alternative (`dpdisp`)

```bash
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
```

This submits but does **not** block. For blocking behaviour prefer the Python
`run_submission` API above.

---

## Recovery after session reset

dpdispatcher writes a `submission.json` (with remote state) into the work dir.
To resume a job whose submission you cannot remember:

1. Look for an existing `submission.json` / `.bohrium_*_submission.json` in the
   workspace — reuse it rather than re-submitting.
2. `Submission.submission_from_json("submission.json")` reconstructs the job.
3. `check_all_finished()` + `download_jobs()` retrieves results.

**Always search the workspace for an existing submission file before
re-submitting** — duplicate submissions waste credits.
