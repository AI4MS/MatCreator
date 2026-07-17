# VASP Bohrium Submission Reference

Submit through the `bohrium` skill. See the `bohrium` skill documentation
for the canonical submission schema, polling, and download.

This file covers only **VASP-specific** parameters.

## VASP-specific environment variable

| Variable | Description | Example |
|---|---|---|
| `BOHRIUM_VASP_IMAGE` | Docker image URI for VASP | `registry.dp.tech/dptech/vasp:5.4.4` |
| `BOHRIUM_VASP_MACHINE` | `scass_type`, e.g. `c32_m64_cpu` | — |

All other Bohrium variables (`BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD`,
`BOHRIUM_PROJECT_ID`) are shared with the `bohrium` skill.

## VASP run command

```bash
export FI_PROVIDER=tcp && source /opt/intel/oneapi/setvars.sh && mpirun -np <N_CORES> vasp_std > log 2> err
```

> **⚠️ CRITICAL:** `export FI_PROVIDER=tcp` is **MANDATORY** for VASP jobs on
> Bohrium. Without it, MPI communication fails with fabric errors, wasting
> compute time and credits. Always include it before `source setvars.sh`.

Match `<N_CORES>` to the machine's core count.

## Choosing CPU machines

Choose based on system size:
- **Small systems (a few atoms)**: `c16_m32_cpu` — good balance of cores and memory
- **Medium systems (10–50 atoms)**: `c32_m64_cpu` for better parallelization
- **Large systems or k-point-heavy calculations**: `c32_m64_cpu` or larger

Set `NCORE` in the INCAR to ≈ √(number of cores) for optimal performance.

## submission.template.json (VASP single-point)

```json
{
  "work_base": "<calc_dir>",
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
        "scass_type": "${BOHRIUM_VASP_MACHINE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_VASP_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "export FI_PROVIDER=tcp && source /opt/intel/oneapi/setvars.sh && mpirun -np 32 vasp_std > log 2> err",
      "task_work_path": ".",
      "forward_files": ["INCAR", "POSCAR", "KPOINTS", "POTCAR"],
      "backward_files": ["OUTCAR", "vasprun.xml", "CONTCAR", "log", "err"]
    }
  ]
}
```

## Batch submission (many DFT single-points)

For 50+ DFT label calculations, list each structure as a separate task in one
`submission.json` `task_list` (shared INCAR/KPOINTS template via
`forward_common_files`). This submits under one job group — far fewer API calls
than per-task submission.

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_VASP_MACHINE} ${BOHRIUM_VASP_IMAGE} ${JOB_NAME}' \
```

Export `JOB_NAME` before substituting, following the naming convention in the
`bohrium` skill:
e.g. `export JOB_NAME="vasp-scf-Al2O3-50frames"` for a 50-frame SCF label batch.
    < submission.template.json > submission.json
```

Submit via the `bohrium` skill.

## Handling failed jobs

Recover the submission and inspect a failed task's `log`/`err` (downloaded into
its `task_work_path`). See the `bohrium` skill for recovery and re-download
procedures.

Modify the INCAR/settings, write a fresh `submission.json`, and re-submit.

---

## DFT label collection → extxyz (do this immediately after DFT)

When DFT results feed downstream finetuning (e.g. the `dpa4` / `deepmd` skills),
**convert results to a labelled extxyz immediately** rather than deferring it.
A labelled extxyz stores, per frame:

| Label | ASE location | Notes |
|---|---|---|
| `energy` | `atoms.info["energy"]` | total DFT energy (eV) |
| `forces` | `atoms.arrays["forces"]` | per-atom forces (eV/Å) |
| `virial` | `atoms.info["virial"]` | 3×3 stress×volume (eV) |

`vasp_tools.py collect_results` already writes these fields. The `dpa4` and
`deepmd` prepare scripts read them back (with a `SinglePointCalculator`
fallback), so a freshly-collected `scf_result.extxyz` can be passed straight to
`prepare-finetune` / `convert-data` without manual reformatting.

> **Do not** leave DFT labels sitting only in a calculator's `calc.results`
> without also writing them to a standard extxyz — re-reading such frames later
> triggers "energy not found" errors in the prepare scripts.
