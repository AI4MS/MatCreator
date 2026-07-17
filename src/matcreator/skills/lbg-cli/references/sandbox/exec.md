# Sandbox exec

`lbg sdbx exec` usage, including `--background` mechanics and `--user`.

For the foreground vs background vs PTY mental model and the retrieve-before-kill SOP, see [`execution-modes.md`](./execution-modes.md).

## CRITICAL — always pass `--user root`

**Every `lbg sdbx exec` call MUST include `--user root`.** Without it, the
command runs as the sandbox's default non-root user (e.g. uid 1001), which
cannot read files uploaded via `--ti` (owned `root:root`, mode 640), cannot
write to system paths, and cannot install software. There is no scenario
where `--user root` should be omitted.

```bash
lbg sdbx exec --user root <sandbox_id> 'cd /workspace && python train.py'
lbg sdbx exec --user root <sandbox_id> 'cat log.txt | grep ERROR | wc -l'
lbg sdbx exec --user root <sandbox_id> 'echo hello > /tmp/out.txt'
```

## exec command passing

`exec` uses SSH-style pass-through: positional args are joined with spaces
and sent as a single shell string to `bash -l -c` inside the sandbox.
Shell operators work as written — no extra quoting is needed.

## Background jobs (`--background`)

`--background` returns immediately with a `pid` and lets the job keep
running. When `--background` is set, `--timeout` defaults to `0`
(unlimited); do **not** pass a finite `--timeout` unless you actually
want the job killed after that many seconds (the CLI prints a warning
in that case). Re-check state with `lbg sdbx ps <sandbox_id>` or by
reading output files via `lbg sdbx files read`.

```bash
lbg sdbx exec --background --user root <sandbox_id> 'python train.py > /tmp/out.log 2>&1'
lbg sdbx ps <sandbox_id> --json
lbg sdbx files read <sandbox_id> /tmp/out.log
```
