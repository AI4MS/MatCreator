# VASP Execution on Remote Sandboxes

When running VASP inside a Bohrium cloud sandbox via the `remote-job` skill
tools, follow this reference for environment setup and execution.

## Sandbox lifecycle

1. **Submit**: Call `submit_bohr_sandbox` with `template` and project info.
   It is idempotent for the current session, node, and template — safe to
   retry. Record the returned `job_id`.
2. **Upload**: Use `upload_remote_job_input` (requires `job_id`) for each VASP
   input file.
3. **Run**: VASP runs long enough to need the background path — use
   `start_remote_job_command` (requires `job_id`) to launch it, then
   `poll_remote_job_command` to check progress. Do not use the blocking
   `run_remote_job_command` for the VASP run itself.
4. **Download**: Use `download_remote_job_output` (requires `job_id`) to pull
   output files into the local workspace. This streams file bytes and is the
   only reliable way to retrieve large outputs (CHGCAR, WAVECAR, vasprun.xml,
   PNG). Do **not** use a command with `cat`/`cp` for large files — command
   output is truncated (~4000 chars) and binary content will be corrupted.
5. **Terminate**: Call `terminate_remote_job` to release the sandbox when
   work is complete.

## Environment setup (critical)

Before running `vasp_std`, source the Intel oneAPI environment:

```bash
source /opt/intel/oneapi/setvars.sh
```

This sets up:
- Intel MPI runtime (so `mpirun` works)
- MKL libraries (so VASP can load BLAS/LAPACK)
- **PATH** to include the VASP binary directory (so `vasp_std` is directly
  callable — no need to locate or hardcode the binary path)

## Running VASP

```bash
source /opt/intel/oneapi/setvars.sh && mpirun -np 32 vasp_std
```

- Use **`-np 32`** (all 32 CPUs) on the `vasp-544-vtst` template.
- Pass this as a single string to `start_remote_job_command`, then poll with
  `poll_remote_job_command` until it reports finished.

## Completion verification

Do not rely on exit code alone — Intel MPI may return exit code 255 even on
success. Instead, check OUTCAR for the "General timing and account" marker:

```bash
grep "General timing" OUTCAR
```

If the marker is present, VASP completed successfully.

## Files

### Upload (via `upload_remote_job_input`)

| Calculation | Files to upload |
|-------------|----------------|
| Relaxation | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR` |
| SCF | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR` |
| NSCF/BAND | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR`, `CHGCAR` (+ `WAVECAR` if available) |

### Download (via `download_remote_job_output`)

| Calculation | Files to download |
|-------------|-------------------|
| Relaxation | `CONTCAR`, `OUTCAR`, `vasprun.xml`, `OSZICAR` |
| SCF | `CHGCAR`, `WAVECAR`, `OUTCAR`, `vasprun.xml` |
| NSCF/BAND | `vasprun.xml`, `OUTCAR`, `EIGENVAL`, `DOSCAR`, `PROCAR` |

`source_path` is the absolute path inside the sandbox (e.g.
`/home/user/CHGCAR`); `destination_path` must resolve inside the current
workspace (relative paths are resolved against it). Each call streams one
file to disk, so call it once per file.
