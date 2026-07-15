# Sandbox terminal sessions (PTY)

PTY sessions for REPLs, TUIs, and Ctrl-C interaction.

> **For "run a command, get its stdout/stderr/exit_code" use `lbg sdbx exec`, not
> `terminal`.** `exec` returns structured output in one round-trip and is the
> agent-ready path. `terminal` exists for the rare cases that genuinely need a
> TTY: REPLs, TUIs (`htop`, `vim`), sending Ctrl-C to a stuck foreground process,
> programs that detect an interactive terminal and refuse to run otherwise.

Open long-lived PTY shells inside a sandbox for interactive workloads. Three
subcommands:

```bash
lbg sdbx terminal create <sandbox_id> --json                # open pty, default timeout=0 (long-lived)
lbg sdbx terminal create <sandbox_id> --cwd /workspace --user root --json
lbg sdbx terminal send   <sandbox_id> <pid> 'echo hi\n'     # write bytes to stdin (newline executes)
lbg sdbx terminal kill   <sandbox_id> <pid> --json          # kill the pty pid only
```

Key semantics:

- `terminal send` writes bytes **as-is** — there is no implicit newline. Append `\n`
  to make a shell run the line; send raw control bytes (e.g. `$'\x03'` for Ctrl-C)
  to interrupt a running foreground command.
- Multiple positional args after `<pid>` are joined with spaces, mirroring `lbg sdbx exec`.
- `terminal kill` terminates **only** the pty pid. The sandbox keeps running; use
  `lbg sdbx kill <sandbox_id>` to destroy the sandbox itself.
- `--timeout` defaults to `0` (no auto-destroy). Always `terminal kill` the pid when
  finished — long-lived terminals continue to consume sandbox resources.

## Capturing PTY output

`terminal send` returns only `sent_bytes` — the PTY's stdout is **not** echoed
back. The PTY is a streaming session inside the sandbox, but each `lbg`
invocation is a one-shot RPC, so there is no per-process buffer to read from
afterwards. Two patterns work:

- **Preferred — use `exec`:** `lbg sdbx exec --user root <sandbox_id> '<cmd>'` already
  returns `stdout` / `stderr` / `exit_code` in a single call with no escape-code
  handling required.
- **PTY-bound case — redirect, then read:** if the workload genuinely needs a
  TTY, redirect each command's output to a file inside the PTY and read it
  back. Redirect per-command rather than once at the top of the session — a
  whole-session `exec >/tmp/log` also captures the bash prompt's OSC title
  escapes, which makes the log harder for tools to consume.

  ```bash
  lbg sdbx terminal send <sandbox_id> <pid> $'cmd > /tmp/out 2>&1\n'
  lbg sdbx files read    <sandbox_id> /tmp/out
  ```
