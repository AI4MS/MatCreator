# VASP on Tracked Bohrium Batch Jobs

Load `bohrium` for access/project discovery and `remote-job` for tracked
submission and recovery. Read the
[Batch Job tool contract](../../remote-job/references/bohr-batchjob-ref.md)
before submitting. Input generation via pymatgen sets is unchanged.
This is noninteractive Batch Job execution, not the
[interactive sandbox workflow](remote-sandbox-execution.md).

## 1. Verify inputs, image, machine, and approval

- Prepare a calculation directory containing `INCAR`, `POSCAR`, `POTCAR`, and
  `KPOINTS` when required (KSPACING-based labeling need not have `KPOINTS`).
  Include required restart inputs such as `CHGCAR` for NSCF.
- `input_path` must be a **workspace-relative** path (e.g. `./vasp-scf-Al`,
  never absolute) to a regular file or nonempty directory inside the
  workspace. For VASP, use the calculation directory; its contents are
  unpacked at the root of the remote working directory, which is why
  `command` is `bash run.sh`, not a prefixed path. No root/nested symlinks,
  empty directories, or special files: materialize inputs as regular files.
  Keep unrelated data and credentials out of the uploaded tree.
- Resolve `project_id` explicitly or from `BOHRIUM_PROJECT_ID`. Stop if missing;
  never silently choose a billing project.
- Choose an explicit, authorized VASP image, for example the user's
  `BOHRIUM_VASP_IMAGE`. Confirm it contains the intended `vasp_std` (or required
  variant), Intel oneAPI environment, and compatible MPI. Do not guess an image
  tag or assume this environment variable is consumed automatically by the tool.
- Discover current CPU machines and their core/memory counts:

  ```bash
  bohr batchjob machine list -o json
  ```

  Choose exactly one verified `machine_type` or `sku_id`. Do not copy historical
  SKUs from `bohr.md`, use legacy node discovery, or use a sandbox machine list.
- **Submission approval:** Before submitting, state the number of jobs,
  selected machine, and estimated core-hours. Never batch-submit more than
  **50 jobs** without explicit user approval. One tracked submission is one
  calculation; do not bypass the limit with a loop or legacy job groups.

## 2. Prepare the complete command and retained outputs

Write `run.sh` inside the calculation directory:

```bash
#!/usr/bin/env bash
set -e
export FI_PROVIDER=tcp
source /opt/intel/oneapi/setvars.sh
export OMP_NUM_THREADS=1
mpirun -np <N_CORES> vasp_std > log 2> err
```

Replace `<N_CORES>` with the actual allocated CPU core count from the selected
Batch Job machine (for example, `32` only for a verified 32-core machine).
`FI_PROVIDER=tcp` is mandatory for this Bohrium VASP/oneAPI recipe to avoid
MPI fabric errors. Initialize oneAPI before `mpirun`. Set INCAR `NCORE` through
`user_incar_settings` to a suitable divisor of the rank count; it is not the
MPI rank count and is not named `NCORES`.

The submit command `bash run.sh > stdout.log 2> stderr.log` also captures
environment-initialization failures. Keep both these wrapper logs and VASP's
`log`/`err`. The script must propagate VASP's failure exit status; do not append
an unconditional successful command that masks it.

Declare retained outputs explicitly in `out_files`:

- Always: `vasprun.xml`, `OUTCAR`, `OSZICAR`, `CONTCAR`, `log`, `err`,
  `stdout.log`, `stderr.log`.
- Retain provenance inputs `INCAR`, `POSCAR`, `KPOINTS` (when used), and `run.sh`.
- SCF → NSCF requires `CHGCAR`; retain it when `LCHARG=True`.
- Retain `WAVECAR` only when generated and needed for restart, and `EIGENVAL`,
  `DOSCAR`, `PROCAR`, or other property outputs when needed.
- Do not request nonexistent conditional outputs for a recipe that disables
  them (e.g. charge density/wavefunctions in labeling), or retain a huge
  wildcard tree unnecessarily. A file not retained may be unavailable later.

## 3. Submit once through the tracked tool

Illustrative SCF call (replace the uppercase placeholders with verified values;
remove conditional files not produced by the chosen calculation):

```python
submit_bohr_batchjob(
    name="vasp-scf-Al",
    image=VERIFIED_VASP_IMAGE,
    command="bash run.sh > stdout.log 2> stderr.log",
    machine_type=VERIFIED_BATCHJOB_MACHINE_TYPE,
    project_id=SELECTED_PROJECT_ID,
    input_path="./vasp-scf-Al",
    out_files=[
        "vasprun.xml", "OUTCAR", "OSZICAR", "CONTCAR", "CHGCAR",
        "INCAR", "POSCAR", "KPOINTS", "run.sh",
        "log", "err", "stdout.log", "stderr.log",
    ],
    max_run_time="24h",
    max_wait_time="30m",
)
```

Use `sku_id=VERIFIED_BATCHJOB_SKU_ID` INSTEAD OF `machine_type` if selecting a
SKU. `name`, `image`, and `command` are required. `project_id` can be omitted
only when `BOHRIUM_PROJECT_ID` resolves it. Durations are CLI duration strings,
not integer seconds; these are the tool defaults. `out_files` is a list, mapped
to repeatable `--out-file`, not legacy comma-separated output flags.

The adapter runs matching `bohr batchjob submit ... --input <directory>
--dry-run` preflight before the real local-input submission. Fix preflight
errors first. Never also run a manual CLI submit after using the tool.

Save returned durable `job_id` and provider `batchjob_id`. All tracked tools use
`job_id`; raw `bohr batchjob describe <batchjob_id>` takes the string provider
ID positionally. Never substitute a legacy numeric job ID.

## 4. Reattach, collect, then validate VASP

1. If prior context says "REMOTE JOB ALREADY SUBMITTED", call
   `get_remote_job_status(job_id)` FIRST. Do not call `submit_bohr_batchjob`
   again for that step, even after a timeout/restart.
2. Use normalized status and semantic provider `status_name` / `terminal`.
   Never decide success from numeric status or `exitCode`. Inspect
   `errorMessage` / `errorCode` on failure.
3. For queued/running work, return `needs_replanning` with the job ID and
   observation rather than looping polls inside the step. `prepared` means
   incomplete submission; preserve CLI inspection guidance after upload or
   final-submit errors, and never blindly retry a potentially created job.
   Unknown states are nonterminal, not success.
4. Respect `user_control` before doing further work: stop and return
   `needs_replanning`, never replace a user-terminated job. Batch Jobs have no
   interactive exec, per-file transfer, or pause/resume. A cancellation accepted
   with `confirmed: false` is not confirmed termination.
5. Once `status: succeeded`, call:

   ```python
   collect_remote_job_outputs(job_id, destination_path="./vasp-scf-Al-results")
   ```

   The destination must be new and nonexistent; do not `mkdir` it, use the input
   directory, or merge into existing results. The CLI's `--dest` download
   safely extracts and atomically installs results; no manual unzip is needed.
   Already-collected replay is a durable no-op: reuse the recorded artifact paths.
6. Only then parse local `vasprun.xml` with
   [read-results.md](read-results.md). Check the expected outputs and
   electronic convergence, plus ionic convergence for relaxation; inspect
   `OUTCAR`, `OSZICAR`, and retained logs when incomplete. Remote `succeeded`
   establishes execution success only, **not VASP convergence** or a trustworthy
   scientific result. Report unconverged/invalid results rather than claiming
   a completed calculation.

Legacy `bohr_job` records remain readable but operations are unsupported.
Never reinterpret their IDs or automatically resubmit them. Changes to a
failed/unconverged calculation require a new plan and the same approval rules,
not an automatic replacement of the tracked job.
