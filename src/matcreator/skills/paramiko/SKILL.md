---
name: paramiko
description: Write and run paramiko-based Python scripts to connect to arbitrary SSH-accessible remote servers/clusters — execute commands, transfer files via SFTP. Use when a target server isn't covered by the Bohrium-backed remote-job tools. Server-specific connection details and job-scheduler commands live in this skill's references/ subdirectory (one file per server).
metadata:
  tools:
    - run_skill_script
    - run_python
    - run_bash
  dependent_skills: []
  tags: [ssh, paramiko, sftp, remote-server, hpc, general]
---

# Paramiko SSH/SFTP

Connect to arbitrary SSH-accessible remote servers or HPC clusters using
`paramiko` — execute commands and transfer files over SFTP.

## When to use this vs. `remote-job`

- Use `remote-job`/`bohrium` when the target is a Bohrium-backed sandbox or
  batch job — those tools track job/sandbox state for you.
- Use this `paramiko` skill when the user gives you SSH credentials for a
  server that is **not** wired into the Bohrium provider registry (e.g. a
  university or vendor HPC login node, a personal workstation, a bare VM).
  Raw SSH has no durable job tracker, so you are responsible for polling and
  bookkeeping yourself (see "Long-running commands" below).

## Before connecting

Check `references/<server-name>.md` first — it documents that server's
connection env vars, auth method, remote working-directory convention, job
scheduler (if any), and known quirks (e.g. login-shell requirements for
`module` commands).

If no reference file exists for the target server yet:
1. Ask the user for the connection details you need (host, port, user, auth
   method, working directory, scheduler if any).
2. Once the connection works, write a new `references/<server-name>.md` file
   (copy `references/_template.md`) so future sessions don't have to
   rediscover the same details.

## Quick start with the bundled CLI

`scripts/ssh_client.py` is a small, scheduler-agnostic SSH/SFTP CLI. Run it
with `run_skill_script(skill_name="paramiko", script_name="ssh_client.py", args="...")`.

> **`args` is a single STRING, not a list.** Spaces separate arguments like a
> shell command line — `args="exec hostname"`, not `args=["exec", "hostname"]`.

Subcommands:

| Command | Arguments | Description |
|---------|-----------|-------------|
| `connect` | — | Test the SSH connection |
| `exec` | `<cmd ...>` | Execute an arbitrary remote command |
| `upload` | `<local> <remote> [-r]` | Upload a file/dir (`-r` for recursive) |
| `download` | `<remote> <local> [-r]` | Download a file/dir (`-r` for recursive) |
| `ls` | `[path]` | List a remote directory (default: `--workdir`) |
| `mkdir` | `<path>` | Create a remote directory |

Connection flags (before the subcommand), each with a generic environment
variable fallback:

| Flag | Env var fallback |
|------|-------------------|
| `--host` | `SSH_HOST` |
| `--port` | `SSH_PORT` |
| `--user` | `SSH_USER` |
| `--password` | `SSH_PASSWORD` |
| `--key-file` | `SSH_KEY_FILE` |
| `--key-content` | `SSH_KEY_CONTENT` |
| `--workdir` | `SSH_WORKDIR` |

Exactly one of `--password` / `--key-file` / `--key-content` (or their env
var equivalents) must be set. A server-specific env var (e.g.
`PARATERA_SSH_HOST`) is not read automatically — pass its value explicitly as
a flag, e.g. `args=f"--host {os.environ['PARATERA_SSH_HOST']} ... connect"`.

Example:
```python
run_skill_script("paramiko", "ssh_client.py", "connect")
run_skill_script("paramiko", "ssh_client.py", "exec hostname")
run_skill_script("paramiko", "ssh_client.py", "upload /local/input.txt /remote/input.txt")
run_skill_script("paramiko", "ssh_client.py", "upload /local/dir /remote/dir -r")
run_skill_script("paramiko", "ssh_client.py", "ls /remote/path")
```

For commands with pipes, redirection, or shell builtins, wrap them so the
remote shell parses them correctly, e.g.
`args="exec bash --login -c 'module avail 2>&1 | grep -i qe'"`.

## Writing custom paramiko code

For anything the bundled CLI can't do — a scheduler-specific submit/poll
workflow, or programmatic use inside a larger script — write your own
paramiko code with `run_python`. The canonical pattern:

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # trusts unknown hosts; fine for scripted/CI use
try:
    ssh.connect(hostname=host, port=port, username=user,
                password=password,          # or: key_filename=path, or: pkey=<parsed key>
                allow_agent=False, look_for_keys=False, timeout=30)

    # Run a command
    _, stdout, stderr = ssh.exec_command("hostname")
    exit_code = stdout.channel.recv_exit_status()  # blocks until the command finishes
    out, err = stdout.read().decode(), stderr.read().decode()

    # Transfer files
    sftp = ssh.open_sftp()
    sftp.put("/local/file", "/remote/file")
    sftp.get("/remote/file", "/local/file")
    sftp.close()
finally:
    ssh.close()
```

Notes:
- Auth: pass exactly one of `password=`, `key_filename=` (path to a private
  key file), or `pkey=` (a parsed `paramiko.PKey`, built from key content with
  e.g. `paramiko.RSAKey.from_private_key(io.StringIO(key_content))` — try
  `Ed25519Key`/`RSAKey`/`ECDSAKey` in turn if the key type is unknown).
- `sftp.put`/`get` only transfer single files — paramiko has no built-in
  recursive transfer. Walk the local directory (`os.walk`/`Path.iterdir`) and
  call `sftp.mkdir`/`put`/`get` per entry, as `scripts/ssh_client.py`'s
  `upload_directory`/`download_directory` do.
- Always `ssh.close()` (and `sftp.close()`) in a `finally` block.

## Long-running commands

`exec_command`/`recv_exit_status()` blocks until the remote command exits —
fine for short commands, but risky for anything that might run past the SSH
session's or your own timeout. For long-running work (a multi-hour
simulation, a large data transfer job), detach it from the SSH session and
poll instead:

```bash
nohup <command> > /remote/path/job.log 2>&1 & echo $!
```

Capture the printed PID, then poll periodically with a fresh `exec`:
`kill -0 <pid>` (exit code 0 = still running, nonzero = finished/dead) and
`tail -n 50 /remote/path/job.log` to check progress/output. If the target
has a real job scheduler (Slurm, PBS, etc.), prefer that instead — see the
server's reference doc for its submit/status/cancel commands.

## Error handling

- `paramiko.AuthenticationException` — bad credentials/key; don't retry
  blindly, surface the error and ask the user to double check.
- `paramiko.SSHException` — general SSH/protocol failure (e.g. bad host key,
  channel errors).
- `socket.timeout` / connection refused — network/firewall issue or wrong
  host/port.

`scripts/ssh_client.py` catches all of these and prints a JSON
`{"status": "error", "error": "..."}` with a non-zero exit code.

## Security notes

- Read credentials from environment variables; never hardcode or print
  passwords, API keys, or private key content in code or logs.
- Prefer key-based auth over password/token auth when the server supports it.
- `AutoAddPolicy` trusts unknown host keys automatically — acceptable for
  scripted/CI-style use against a known server, but be aware it skips host
  key verification.
- Always disconnect (`ssh.close()`) in a `finally` block, even on error.

## Reference index

- `references/paratera.md` — Paratera supercomputing cloud (Slurm scheduler).
- `references/_template.md` — copy this when documenting a new server.
