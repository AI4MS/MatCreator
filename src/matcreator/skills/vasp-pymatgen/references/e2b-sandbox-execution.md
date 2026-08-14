# VASP Execution on E2B Sandboxes

When running VASP inside a Bohrium cloud sandbox via the E2B skill tools,
follow this reference for environment setup and execution.

## Sandbox lifecycle

1. **Submit**: Call `submit_e2b_sandbox` with `template` and project info.
   It is idempotent for the current session, node, and template — safe to
   retry. Record the returned `job_id`.
2. **Upload**: Use `upload_e2b_input` (requires `job_id`) for each VASP
   input file.
3. **Run**: Use `run_e2b_command` (requires `job_id`) to execute the VASP
   command inside the sandbox.
4. **Download**: Use `download_e2b_output` (requires `job_id`) to pull output
   files into the local workspace. This streams file bytes via the E2B
   filesystem API and is the only reliable way to retrieve large outputs
   (CHGCAR, WAVECAR, vasprun.xml, PNG). Do **not** use `run_e2b_command` with
   `cat`/`cp` for large files — command output is truncated (~4000 chars) and
   binary content will be corrupted.
5. **Terminate**: Call `terminate_e2b_sandbox` to release the sandbox when
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
- Pass this as a single string to `run_e2b_command`.

## Completion verification

Do not rely on exit code alone — Intel MPI may return exit code 255 even on
success. Instead, check OUTCAR for the "General timing and account" marker:

```bash
grep "General timing" OUTCAR
```

If the marker is present, VASP completed successfully.

## Files

### Upload (via `upload_e2b_input`)

| Calculation | Files to upload |
|-------------|----------------|
| Relaxation | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR` |
| SCF | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR` |
| NSCF/BAND | `INCAR`, `POSCAR`, `KPOINTS`, `POTCAR`, `CHGCAR` (+ `WAVECAR` if available) |

### Download (via `download_e2b_output`)

| Calculation | Files to download |
|-------------|-------------------|
| Relaxation | `CONTCAR`, `OUTCAR`, `vasprun.xml`, `OSZICAR` |
| SCF | `CHGCAR`, `WAVECAR`, `OUTCAR`, `vasprun.xml` |
| NSCF/BAND | `vasprun.xml`, `OUTCAR`, `EIGENVAL`, `DOSCAR`, `PROCAR` |

`source_path` is the absolute path inside the sandbox (e.g.
`/home/user/CHGCAR`); `destination_path` must resolve inside the current
workspace (relative paths are resolved against it). Each call streams one
file to disk, so call it once per file.

## API key format (Bohrium vs e2b SDK)

> ⚠️ If `submit_e2b_sandbox` fails with
> `Invalid API key format: expected "e2b_" followed by hex characters`,
> this is **not** a wrong key or wrong endpoint — it is the e2b SDK's
> client-side format check, not server-side auth.

Since e2b SDK **2.20.0**, `ApiClient` validates the API key before any
network call and rejects anything that does not match `e2b_<hex>`. The
Bohrium E2B endpoint uses **bare hex keys** (no `e2b_` prefix), so the SDK
raises `AuthenticationException` during sandbox creation/connect and the
request never reaches the server.

**Do not** "fix" this by prepending `e2b_` to the key — that corrupts the
key and Bohrium will reject it server-side. The correct fix is to disable
the client-side format check while leaving the key untouched:

```bash
export E2B_VALIDATE_API_KEY=false
```

The adapter already sets `E2B_VALIDATE_API_KEY=false` by default at import
time (`matcreator/control_plane/providers/e2b.py`), so this is handled for Bohrium
deployments out of the box. An explicit value in the environment takes
precedence — set `E2B_VALIDATE_API_KEY=true` only if you are talking to
the public e2b.dev API with a standard `e2b_`-prefixed key.

Symptom vs. cause quick map:

| Symptom | Likely cause |
|---------|--------------|
| `Invalid API key format: expected "e2b_"...` at submit | SDK client-side check; `E2B_VALIDATE_API_KEY` not `false` |
| Auth/401 from the endpoint after disabling the check | Wrong `E2B_API_KEY` / `E2B_API_URL` / `BOHRIUM_PROJECT_ID` (real creds) |
| Key works only when you manually add `e2b_` | You are on the public e2b.dev API, not Bohrium — keep the prefix and set `E2B_VALIDATE_API_KEY=true` |
