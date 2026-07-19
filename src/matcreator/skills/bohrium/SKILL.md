---
name: bohrium
description: Submit and manage computational jobs on Bohrium (bohrium.com) cloud platform using bohr CLI. Recommended for submitting to Bohrium.
metadata:
  tools:
    - run_bash
  tags: [bohrium, hpc, job-submission, cloud-computing]
---

# Bohrium Cloud Job Management

Submit and manage computational jobs on [Bohrium](https://bohrium.com) cloud platform via `bohr` CLI.

## Prerequisites

### 1. Verify bohr CLI installation
Verify: `bohr version`

If not installed, run the following command to install it:
- **Linux/macOS**: `curl -fsSL https://bohrium.com/download/bohr | sh`


### 2. Verify user access

```bash
echo $ACCESS_KEY
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
And select the first available project ID in the list. If multiple projects are available, notify the users as
the first available project ID may not be the one they want to use, and they had better set the environment variable
`BOHRIUM_PROJECT_ID` to the desired project ID.

To the users that the project ID can also be found in the Bohrium Cloud dashboard under `Projects`.

## Machine Types
For available machine types and suggestions for choosing machines (Machine SKU reference),
see [references/bohrium-machines-ref.md](references/bohrium-machines-ref.md).

## Job submission and management


For submitting, managing and downloading a single job and a batch of jobs,
see example and `bohr` CLI usage details in [references/bohrium-cli-ref.md](references/bohrium-cli-ref.md).


## Tips and Pitfalls

- **Always check machine availability** before submitting — popular GPU configs may be out of stock
- **Use `--backward_files` to specify outputs** — comma-separate values, not making an array
- **Set explanatory job names** — job name should indicate its type, content and the variation branch
    (for example, "SiO2-md-1000K-10000steps-repeat-2") to make tracking easier with many jobs.
- **Download results promptly** — completed jobs are only retained for a limited time on Bohrium cloud!
- **Wrap complex commands in a shell script** — write a shell script, upload, and replace command with `bash script.sh`.
      This is more reliable and maintainable than long inline `--command` strings.
- **GPU jobs need GPU-enabled images** — not all images have CUDA/cuDNN.
- **MPI jobs** — use `mpirun -np N` where N matches your machine's CPU cores.
- **Memory-intensive jobs** — pick machines with higher memory ratio (e.g., c8_m64 vs c8_m8)
- **`bohr` commands can be slow** — because API endpoint can be slow. Wrap commands with `timeout 60-120`.
  When submitting very large directories and files, it may require even longer timeouts. Expect upload speed averaging
  ~ 1~5 MB/s.
- **Never use `bohr <whatever> list` commands interactively**  —  without `--json` keyword, `bohr <whatever> list` opens
   interactive TUI that hangs in non-terminal. Never do that!
- **Error reports** — check error messages in `STDOUTERR` (shell stdout/stderr) that comes with logs,
   if not redirected to a result file by something like `> output.txt 2>&1`.
- **Extract zip files** — Results are downloaded as a zip file to the specified output directory, may need to extract!
- **Use `--help` to explore more options** — e.g., `bohr job submit --help` for all job submission parameters

## Troubleshooting

| Issue | Solution                                  |
|-------|-------------------------------------------|
| Login fails | `bohr login` again, check credentials     |
| Machine not available | Try a different SKU or wait               |
| Job stuck in pending | Check quota, try other machines           |
| No output files | Check logs for errors, verify command ran |
| Out of memory | Use machine with more RAM                 |

## References
- [references/bohrium-cli-ref.md](references/bohrium-cli-ref.md) — Bohr CLI command reference
- [references/bohrium-machines-ref.md](references/bohrium-machines-ref.md) — Machine SKU reference
- Full docs online: https://bohrium.com/docs/cli
