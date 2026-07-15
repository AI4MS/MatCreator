# Sandbox exec

`lbg sdbx exec` usage, including `--background` mechanics and `--user`.

For the foreground vs background vs PTY mental model and the retrieve-before-kill SOP, see [`execution-modes.md`](./execution-modes.md).

## exec command passing

`exec` uses SSH-style pass-through: positional args are joined with spaces
and sent as a single shell string to `bash -l -c` inside the sandbox.
Shell operators work as written — no extra quoting is needed:

```bash
lbg sdbx exec <sandbox_id> 'cd /workspace && python train.py'
lbg sdbx exec <sandbox_id> 'cat log.txt | grep ERROR | wc -l'
lbg sdbx exec <sandbox_id> 'echo hello > /tmp/out.txt'
```

## Running as root (`--user root`)

By default `exec` runs as the sandbox's default user (often a non-root uid
like 1001). **Pass `--user root` when the command needs root privileges** —
reading files uploaded via `--ti` (owned by `root:root`, mode 640), writing
to system paths, `chown`/`chmod`, `apt`/`pip install`, or anything that
touches `/opt`, `/etc`, etc.

```bash
lbg sdbx exec --user root <sandbox_id> 'cat /workspace/uploaded_by_ti.json'
lbg sdbx exec --user root <sandbox_id> 'chown -R 1001:1001 /workspace/data'
lbg sdbx exec --user root <sandbox_id> 'apt-get update && apt-get install -y build-essential'
```

> **Always use `--user root` for file-ownership fixes.** Files uploaded with
> `--ti` land as `root:root` 640 and are unreadable by the default sandbox
> user; `exec --user root` (or `chown`/`chmod` as root first) is the only way
> a non-root session can access them. See
> [`files.md`](./files.md) for the full upload-ownership note.

## Background jobs (`--background`)

`--background` returns immediately with a `pid` and lets the job keep
running. When `--background` is set, `--timeout` defaults to `0`
(unlimited); do **not** pass a finite `--timeout` unless you actually
want the job killed after that many seconds (the CLI prints a warning
in that case). Re-check state with `lbg sdbx ps <sandbox_id>` or by
reading output files via `lbg sdbx files read`.

```bash
lbg sdbx exec --background <sandbox_id> 'python train.py > /tmp/out.log 2>&1'
lbg sdbx ps <sandbox_id> --json
lbg sdbx files read <sandbox_id> /tmp/out.log
```
