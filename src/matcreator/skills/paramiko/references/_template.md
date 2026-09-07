# `<server-name>` reference

Copy this file to `references/<server-name>.md` when documenting a new SSH
target and fill in each section. Keep it factual and specific — this is
consulted by the agent before it connects, not read as a tutorial.

## Connection

| Env var | Purpose |
|---------|---------|
| `<PREFIX>_SSH_HOST` | Login node hostname |
| `<PREFIX>_SSH_PORT` | SSH port (default 22) |
| `<PREFIX>_SSH_USER` | Username (note any special format, e.g. `user@partition`) |
| `<PREFIX>_SSH_...` | Auth secret — password, API key, or path/content of a private key |
| `<PREFIX>_WORK_DIR` | Default remote working directory |

Map these to `scripts/ssh_client.py` flags explicitly, e.g.:

```python
run_skill_script("paramiko", "ssh_client.py",
    f"--host {host} --port {port} --user {user} --password {secret} --workdir {workdir} connect")
```

## Auth method

Describe how authentication works for this server (password, private key
file, API-key-as-password, etc.) and any format quirks (e.g. "the API key is
a 32-character token, not a PEM key").

## Remote working directory convention

Where uploaded input files and job outputs are expected to live, and any
per-user/per-partition path structure.

## Job scheduler

If the server has a job scheduler (Slurm, PBS, LSF, none), document its
command set here as raw `exec` examples built on `ssh_client.py`'s `exec`
subcommand — submit, check status, cancel, and a poll-until-done pattern.

## Module system / login-shell quirks

Note if commands like `module` require a login shell
(`exec bash --login -c '...'`) or other environment-sourcing quirks that
non-interactive SSH sessions don't pick up automatically.

## Known gotchas

Anything else that would trip up a naive first attempt (quota limits,
firewall/VPN requirements, node-specific restrictions, etc.).

## Worked example

One end-to-end example (e.g. upload → submit → poll → download) using
`ssh_client.py` subcommands plus raw `exec` for anything scheduler-specific.
