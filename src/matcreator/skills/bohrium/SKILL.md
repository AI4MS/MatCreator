---
name: bohrium
description: Submit and manage computational jobs on Bohrium (bohrium.com) via the dpdispatcher Python API. NEVER use a `bohr` shell CLI — see the warning below.
metadata:
  tools:
    - run_bash
    - run_python_file
  tags: [bohrium, hpc, job-submission, cloud-computing, dpdispatcher]
---

# Bohrium Cloud Job Management

Submit and manage computational jobs on [Bohrium](https://bohrium.com) via the
**dpdispatcher Python API**.

## ⚠️ CRITICAL — Do NOT use the `bohr` CLI

The `bohr` executable available on this machine (and via `npm install -g bohr`)
is **[bohr.io](https://bohr.io)** — a static-site deployment tool. It is **not**
the Bohrium compute-platform CLI. Commands like `bohr job submit`,
`bohr job list`, `bohr job_group create` **do not exist** and will waste many
tool calls before failing.

> **Always use dpdispatcher** (Python API or `dpdisp` CLI) for every Bohrium
> action: submission, status polling, result download. There is no working
> `bohr` CLI on this platform.

---

## Prerequisites

### 1. Install dpdispatcher

```bash
uv pip install dpdispatcher oss2   # oss2 needed for Bohrium file transfer
```

Verify: `uv run python -c "import dpdispatcher; print(dpdispatcher.__version__)"`

### 2. Environment variables (authentication)

dpdispatcher authenticates with **email + password** — this always works. Do not
attempt JWT/OpenAPI token flows; they return 401 and are dead ends.

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |

> **Persist the submission to disk** after every submit (see
> [references/dpdispatcher.md](references/dpdispatcher.md)). The agent's context
> may be compressed or reset before a long job finishes; the on-disk
> `submission.json` lets a later session recover and poll the same job.

---

## Machine Types (Reference)

Bohrium offers CPU and GPU machines. Pricing and inventory change; the
`scass_type` string is passed straight through to dpdispatcher.

### CPU Machines

Format: `c{cores}_m{mem}_cpu` (some have `_H` suffix for high-performance)

| Range | Examples | Price (CNY/h) |
|-------|----------|---------------|
| 2C | c2_m2_cpu ~ c2_m16_cpu | 0.16-0.20 |
| 4C | c4_m4_cpu ~ c4_m32_cpu | 0.32-0.40 |
| 8C | c8_m8_cpu ~ c8_m64_cpu | 0.64-0.80 |
| 16C | c16_m16_cpu ~ c16_m128_cpu | 1.28-1.60 |
| 32C | c32_m32_cpu ~ c32_m256_cpu | 2.56-3.20 |
| 64C | c64_m64_cpu ~ c64_m512_cpu | 5.12-7.68 |
| 96C | c96_m192_cpu ~ c96_m384_cpu | 9.60-11.52 |

### GPU Machines

Format: `c{cores}_m{mem}_{count} * {GPU_MODEL}` or `{count} * {GPU_MODEL}_{vram}g`

| GPU | VRAM | Price Range (CNY/h) |
|-----|------|---------------------|
| NVIDIA T4 | 16GB | 2.5-12.0 |
| NVIDIA V100 | 16/32GB | 4.5-36.0 |
| NVIDIA A100 | 40/80GB | 10.0-80.0 |
| NVIDIA 3090 | 24GB | 4.5-36.0 |
| NVIDIA 4090 | 24GB | 5.5-44.0 |
| NVIDIA L4 | 24GB | 5.0-20.0 |
| NVIDIA L20 | 48GB | 8.0-64.0 |

**GPU-only vs CPU+GPU:** Entries like `1 * NVIDIA V100_32g` are GPU-only (no
CPU/RAM). Entries like `c12_m64_1 * NVIDIA L4` bundle CPU+RAM+GPU.

### Common Docker images

| Image | Use Case |
|-------|----------|
| `registry.dp.tech/dptech/lammps:2023.08.02` | LAMMPS MD simulations |
| `registry.dp.tech/dptech/deepmd-kit:3.1.3` | LAMMPS + DeePMD-kit |
| `registry.dp.tech/dptech/vasp:5.4.4` | VASP DFT calculations |
| `registry.dp.tech/dptech/python:3.10` | General Python |

---

## Submission workflow (canonical)

The full submission.json schema, variable substitution, polling, and download
recipes live in **[references/dpdispatcher.md](references/dpdispatcher.md)** —
load it before submitting.

```
load_skill_resource(skill_name="bohrium", path="references/dpdispatcher.md")
```

Summary of the flow:

1. **Write `submission.template.json`** using `${VARNAME}` placeholders, with
   `batch_type: "Bohrium"`, `context_type: "BohriumContext"`, and
   `remote_profile.input_data.job_type: "container"`.
2. **Substitute env vars** with `envsubst` → `submission.json`.
3. **Submit + block until done** with the Python API:
   ```python
   from dpdispatcher import Submission
   sub = Submission.submission_from_json("submission.json")
   sub.run_submission(check_interval=30)   # uploads, runs, polls, downloads
   ```
   `run_submission` handles upload → submit → poll → download in one call and
   serializes state to disk for crash recovery.

### Polling (when you cannot block)

If the session cannot block, submit with `exit_on_submit=True`, persist
`submission.json`, then poll in a later session:

```python
sub = Submission.submission_from_json("submission.json")
if sub.check_all_finished():
    sub.download_jobs()
```

> **Do NOT** invent methods like `job_status()`. The correct polling primitives
> are `Submission.check_all_finished()` (non-blocking) and
> `Submission.run_submission()` (blocking). Status detail per job comes from the
> internal `get_job_detail` — never call it directly; use the two methods above.

### Batch submission (many tasks)

List every task in a single `submission.json` `task_list`. dpdispatcher uploads
shared files once (`forward_common_files`) and runs all tasks under one job
group — far fewer API calls than per-task submission. See the reference for the
multi-task template.

---

## Tips & Pitfalls

- **`local_root` must be `"."`** (or an existing local dir). A wrong path makes
  upload fail silently.
- **`job_type: "container"` is required** in `input_data`. Without it the
  submission is rejected.
- **`input_data.job_name` is required** — every submission must carry a
  descriptive name (`{skill}-{task}-{descriptor}`, e.g. `vasp-scf-Al2O3-50frames`).
  See [references/dpdispatcher.md](references/dpdispatcher.md#job-naming-mandatory)
  for the naming convention. An unnamed job is untraceable on the dashboard.
- **Set `--backward_files`/`backward_files`** to every output you need; files
  not listed are **not** downloaded and are lost when the job is cleaned up.
- **MPI jobs** — use `mpirun -np N` where N matches the machine's CPU cores.
- **Memory-intensive jobs** — pick machines with a higher memory ratio
  (e.g. c8_m64 vs c8_m8).
- **Always persist `submission.json`** to the work dir so a restarted session
  can recover the job.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `bohr job submit` not found | You are using bohr.io. Use dpdispatcher instead. |
| 401 from OpenAPI / JWT | Dead end — use `BOHRIUM_EMAIL`+`BOHRIUM_PASSWORD` via dpdispatcher. |
| Upload fails | Check `local_root` is `"."` and forward_files exist relative to `work_base`. |
| Submission rejected | Ensure `input_data.job_type = "container"`. |
| Job stuck pending | Check project quota / try a smaller `scass_type`. |

## References
- [references/dpdispatcher.md](references/dpdispatcher.md) — canonical submission.json schema, Python API, polling & download
- [references/bohrium-cli-ref.md](references/bohrium-cli-ref.md) — `bohr` CLI reference (**deprecated / non-functional on this platform**)
- Full docs: https://bohrium.com/docs/cli
