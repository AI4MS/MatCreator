# Sandbox exec

`lbg sdbx exec` usage, including `--background` mechanics.

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
