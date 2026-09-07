# Paratera

Paratera supercomputing cloud, accessed via SSH login node + Slurm scheduler.

## Connection

| Env var | Purpose |
|---------|---------|
| `PARATERA_SSH_HOST` | Login node hostname, e.g. `ssh.paracloud.com` |
| `PARATERA_SSH_PORT` | SSH port (default `22`) |
| `PARATERA_SSH_USER` | Username in `user@PARTITION` form, e.g. `pxyl319@GUANGZHOUXYL` |
| `PARATERA_SSH_API_KEY` | Auth secret — see "Auth method" below |
| `PARATERA_WORK_DIR` | Default remote working directory, e.g. `/XYFS01/HOME/paratera_xy/username` |

Map these onto `scripts/ssh_client.py` explicitly — `PARATERA_SSH_API_KEY`
maps to the generic `--password` flag (it's a token, not a scheduler
concept):

```python
import os

host = os.environ["PARATERA_SSH_HOST"]
port = os.environ.get("PARATERA_SSH_PORT", "22")
user = os.environ["PARATERA_SSH_USER"]
api_key = os.environ["PARATERA_SSH_API_KEY"]
workdir = os.environ["PARATERA_WORK_DIR"]

run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --port {port} --user {user} --password {api_key} --workdir {workdir} connect")
```

## Auth method

`PARATERA_SSH_API_KEY` is a 32-character token (not PEM format) — pass it as
the `--password` value. There is no separate key-file auth for Paratera.

## Remote working directory convention

Uploads and job scripts live under `PARATERA_WORK_DIR`
(`/XYFS01/HOME/paratera_xy/username`-style path). Create per-job
subdirectories under it (e.g. via `mkdir`) to keep runs isolated.

## Job scheduler: Slurm

Paratera uses Slurm. There is no bundled Slurm support in `ssh_client.py` —
issue these as raw `exec` commands.

**Submit a job** (`sbatch` prints `Submitted batch job <id>`):
```python
result = run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} "
    f"exec sbatch -D {workdir}/run1 -p mars_l {workdir}/run1/job.sh")
# parse job id: r"Submitted batch job (\d+)"
```

**Check status** (`squeue` while pending/running, `sacct` once it has left
the queue):
```python
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} exec squeue -j {job_id}")
# if squeue returns nothing, fall back to:
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} "
    f"exec sacct -j {job_id} --format=JobID,State,ExitCode --noheader")
```

**Cancel a job:**
```python
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} exec scancel {job_id}")
```

**Poll until done** — loop `squeue -j <id> -h` on an interval (e.g. 30s);
treat empty output as "left the queue", then confirm the final state with
`sacct` as above. Respect a timeout and surface it rather than looping
forever.

**Job history:**
```python
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} "
    "exec sacct --starttime now-1days --format=JobID,JobName,State,Elapsed --noheader")
```

## Module system / login-shell quirks

`module` is only initialized in **login shells** (sourced from
`.bashrc`/`.bash_profile`). A plain `exec module avail` over non-interactive
SSH can fail with "command not found". Work around it by forcing a login
shell:

```python
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} "
    "exec bash --login -c 'module avail 2>&1 | grep -i qe'")

run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --user {user} --password {api_key} "
    "exec bash --login -c 'module load qe && which pw.x'")
```

## Known gotchas

- `PARATERA_SSH_USER` includes the partition suffix (`user@PARTITION`) —
  don't strip it when composing `--user`.
- Complex remote commands (pipes, redirections, `&&`) must be wrapped in
  `bash --login -c '...'` (single quotes inside) since `exec` sends the raw
  joined string to a non-interactive shell.
- Check quota/disk usage before large uploads:
  `exec bash --login -c 'df -h {workdir} 2>&1'` (or the cluster's documented
  quota command, if different).

## Worked example

Upload an input deck, submit, poll, then download results:

```python
import os, re, time

host, user, api_key = os.environ["PARATERA_SSH_HOST"], os.environ["PARATERA_SSH_USER"], os.environ["PARATERA_SSH_API_KEY"]
workdir = os.environ["PARATERA_WORK_DIR"]
base = f"--host {host} --user {user} --password {api_key}"
remote_dir = f"{workdir}/run1"

run_skill_script("paramiko", "ssh_client.py", f"{base} mkdir {remote_dir}")
run_skill_script("paramiko", "ssh_client.py", f"{base} upload ./local_run {remote_dir} -r")

submit = run_skill_script("paramiko", "ssh_client.py",
    f"{base} exec sbatch -D {remote_dir} -p mars_l {remote_dir}/job.sh")
job_id = re.search(r"Submitted batch job (\d+)", submit).group(1)

while True:
    status = run_skill_script("paramiko", "ssh_client.py", f"{base} exec squeue -j {job_id} -h")
    if not status.strip():
        break
    time.sleep(30)

run_skill_script("paramiko", "ssh_client.py",
    f"{base} exec sacct -j {job_id} --format=JobID,State,ExitCode --noheader")
run_skill_script("paramiko", "ssh_client.py", f"{base} download {remote_dir} ./local_results -r")
```
