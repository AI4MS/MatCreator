# Execution modes

Foreground vs background vs PTY: which `lbg sdbx` mode survives a local interruption, and how to retrieve outputs without losing them.

## The three modes

| Mode | Invocation | Local connection | Where it runs | Survives `--timeout` | Output retrieval |
|------|------------|------------------|---------------|----------------------|------------------|
| Foreground | `lbg sdbx exec <id> '<cmd>'` | blocks until done | sandbox | dies at `--timeout` (default 60s); local disconnect drops captured output | inline `stdout` / `stderr` from the same call |
| Background | `lbg sdbx exec --background <id> '<cmd>'` | returns immediately with `pid` | sandbox | unlimited by default (`--timeout 0`); finite `--timeout` kills the remote command at that boundary | read files the command wrote (`lbg sdbx files read`); pid status via `lbg sdbx ps` / `describe --with-processes` |
| Terminal (PTY) | `lbg sdbx terminal create / send / kill` | persistent PTY pid | sandbox | PTY pid lives until `terminal kill <pid>` or sandbox destruction (per-PTY `--timeout` available) | redirect each command (`cmd > /tmp/out 2>&1\n`) and read the file with `lbg sdbx files read` |

In all three cases the work happens inside the sandbox; what differs is the lifetime of the local connection and how the caller gets the output back.

## When to use which

- **Foreground** — quick, deterministic commands that finish well under a minute (`pwd`, `nvidia-smi`, `pip show <pkg>`, a single test). The caller blocks, the result comes back in one round-trip.
- **Background** — anything that might exceed your local connection's reliable window: model training, long downloads, Dockerfile-equivalent setup, dataset preprocessing, long test suites. The remote process keeps running even if your terminal closes or the agent times out.
- **Terminal (PTY)** — interactive workloads that genuinely need a TTY: REPLs, TUIs (`htop`, `vim`), or sending Ctrl-C to a stuck process. Not a substitute for `exec` when you just want "run this, give me stdout".

## Long-running jobs: the retrieve-before-kill SOP

This is the safe sequence for any `--background` job whose outputs you actually need:

1. **Decide where outputs go.** Pick a persistent path inside the sandbox — convention is `/workspace/out/`. Bake it into the command itself; do not rely on the default cwd.
2. **Launch with `--background`.** The default `--timeout 0` (unlimited) kicks in automatically; do not pass a finite `--timeout` unless you actually want the job killed at that boundary.
   ```bash
   lbg sdbx exec --background <id> 'mkdir -p /workspace/out && python train.py > /workspace/out/run.log 2>&1'
   ```
   The command returns a `pid` (or `{"pid": N, ...}` under `--json`). Record it.
3. **Poll until done.** Check liveness with either of:
   ```bash
   lbg sdbx ps <id> --json
   lbg sdbx describe <id> --with-processes --json
   ```
   The `pid` disappears from `running_processes` when the command finishes. You can also tail the log file periodically:
   ```bash
   lbg sdbx files read <id> /workspace/out/run.log
   ```
4. **Retrieve outputs BEFORE kill.** Pull every file you care about to local disk:
   ```bash
   lbg sdbx files read <id> /workspace/out/run.log --output ./run.log
   lbg sdbx files read <id> /workspace/out/model.bin --format bytes --output ./model.bin
   ```
5. **Verify locally.** Confirm what you needed is on disk (size > 0, expected lines present, checksum matches).
6. **Then kill.**
   ```bash
   lbg sdbx kill <id>
   ```

**Why this matters.** The platform does not currently support reading files from a stopped sandbox. A `--background --timeout 0` job will run to completion remotely — but if you `kill` the sandbox before step 4, the contents of `/workspace/` are gone for good. Treat the sandbox as an ephemeral compute node: outputs you want to keep have to be pulled out before teardown.

## Common mistakes

- **Foreground for a 30-minute job.** The default 60s `--timeout` kills the captured output, and a flaky network can drop the connection long before that. Use `--background`.
- **`--background` then immediate `kill` with no `files read` in between.** The remote command may have succeeded — you still lost its output. Always retrieve first.
- **`--background --timeout 60` (or any finite value).** Setting a finite `--timeout` under `--background` kills the remote command at that boundary. The CLI prints a warning, but the job dies anyway. Pass `--timeout 0` (or omit `--timeout`) for true long-running work.
- **Treating `terminal send` as a run-and-capture.** `send` returns only `sent_bytes` — the PTY's stdout is not echoed back. For "run a command, get its output" use `exec`. When the workload truly needs a TTY, redirect each command (`cmd > /tmp/out 2>&1\n`) and read the file with `files read`.

## See also

- [`exec.md`](./exec.md) — `lbg sdbx exec` usage including `--background`
- [`terminal.md`](./terminal.md) — PTY sessions
- [`lifecycle.md`](./lifecycle.md) — `kill` semantics and best practices
- [`files.md`](./files.md) — `files read` / `files write`
