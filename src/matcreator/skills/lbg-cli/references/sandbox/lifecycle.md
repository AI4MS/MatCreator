# Sandbox lifecycle

Create, list, describe, and kill on-demand sandboxes; plus best practices.

```bash
lbg sdbx doctor --json                                # verify configuration and SDK
lbg sdbx create my-template --json                    # create from explicit template (personal wallet)
lbg sdbx create my-template --project-id <id> --json  # bill the sandbox to a project budget
lbg sdbx create my-template --timeout 1800 --json     # set sandbox auto-destroy lifetime (seconds)
lbg sdbx create my-template --never-timeout --json    # opt out of auto-destroy entirely
lbg sdbx create my-template --mount-user-storage --json # mount caller's personal + share disks
lbg sdbx create my-template --project-id <id> --mount-user-storage --share-subpath sub/dir --json # mount only a sub-dir of the project share disk
lbg sdbx create my-template --env HELLO=WORLD --env DEBUG=1 --json # inject environment variables
lbg sdbx create my-template --image <image-path> --json # swap the template's container image in place
lbg sdbx create my-template --session-id <session-id> --json # tag with a session id (shared user-storage subPath)
lbg sdbx list --json                                  # list your sandboxes
lbg sdbx describe <sandbox_id> --with-processes --json # metadata + running processes
lbg sdbx ps <sandbox_id> --json                       # list processes in a sandbox
lbg sdbx exec <sandbox_id> python script.py --json    # foreground command
lbg sdbx files write --source ./local_dir <sandbox_id> /workspace/dir --json  # upload a file or directory
lbg sdbx files read <sandbox_id> /workspace/result.csv --output ./result.csv
lbg sdbx kill <sandbox_id> --json                     # destroy a sandbox
```

`lbg sdbx create` POSTs to the sandbox workspace OpenAPI endpoint with
body `{"templateID": "<template-name>"}`. `--project-id` is **optional**
and only affects billing: without it the sandbox bills against your
personal wallet; with it the value is sent as the `X-PROJECT-ID`
request header and the sandbox bills against that project's budget
(look up project IDs with `lbg project ls`). The response is the raw
backend payload — it carries `sandboxID` (the ID to use with `exec`,
`kill`, etc.), `templateID`, `state`, `cpuCount`, `memoryMB`, and a
`metadata` dict populated by the platform.

**Sandbox lifetime.** When neither flag is set, the CLI sends
`"timeout": 43200` (12 hours) so multi-hour agent runs aren't cut off
by the much shorter platform default; the create command also prints
a one-line stderr hint reminding you that the default is 12h and
explaining how to extend. Override with one of:

- `--timeout N` adds `"timeout": N` (seconds, non-negative integer) to
  the create body — the sandbox is auto-destroyed N seconds after
  creation. `--timeout 0` is the explicit "unlimited" sentinel and is
  passed through as-is, never substituted.
- `--never-timeout` adds the metadata flag
  `e2b.agents.kruise.io/never-timeout=true`, which tells the platform
  to never auto-destroy the sandbox; no `timeout` field is sent. Pair
  with explicit `lbg sdbx kill <sandbox_id>` when done — long-lived
  sandboxes keep billing.

`--timeout` and `--never-timeout` are mutually exclusive; passing both
fails the request with exit code 2 before any HTTP call.

**Storage mounts.** Pass `--mount-user-storage` to ask the platform to
mount the caller's personal disk and the share disks the caller has
access to into the sandbox. This adds the metadata flag
`bohr.launching.io/mount-user-storage=true` on the create body and is
independent of every other flag (combine with `--timeout`,
`--never-timeout`, `--reserve-failed-sandbox`, or `--project-id` as
needed). Default is off — sandboxes start with no user storage mounted.

**Share disk sub-path.** Pass `--share-subpath <rel>` to mount only a
sub-directory *inside* the project share disk instead of its root. This
adds the metadata flag `bohr.launching.io/share-subpath=<rel>` on the
create body. It only affects the project **share** disk (personal disk is
unchanged) and only takes effect with `--mount-user-storage` (+ a
`--project-id` that grants a share disk); otherwise the CLI prints a
stderr hint and the flag is a no-op. `<rel>` is relative to the project
share root and is **confined to it server-side**: a leading `/` or any
`..` segment is clamped back inside the share (e.g. `../../etc` →
`<share-root>/etc`), so it can never escape the project's share area.
Leave unset to mount the share root as before.

**Environment variables.** Pass `--env KEY=VALUE` (repeatable) to inject
environment variables into the sandbox at creation time. The create body
carries the map under **both** `envVars` (the field the e2b orchestrator
actually applies to the sandbox process environment) and `envs` (persisted
by launching into its `bohr_sandbox.envs` record). Splitting is on the
first `=` only, so values may themselves contain `=` (e.g.
`--env URL=a=b=c`). Empty `--env` is omitted from the body so backend
defaults apply. The injected vars are visible to every later
`lbg sdbx exec` / `terminal` shell (any user), e.g.
`lbg sdbx exec <id> 'echo $HELLO'`.

**In-place image override.** Pass `--image <image-path>` to launch the
chosen template but replace its container image with `<image-path>` for
this one sandbox. This adds the metadata flag
`e2b.agents.kruise.io/image=<image-path>` on the create body; the
platform keeps the template's SKU / CPU / memory / GPU shape and only
swaps the running container image. Use this to try an image without
creating a dedicated template (for a persistent change, use
`lbg sdbx template update <name> --image <new-image>` instead). Find
image references with `lbg sdbx image ls`. Independent of every other
create flag (combine with `--timeout`, `--never-timeout`,
`--mount-user-storage`, `--env`, `--project-id` as needed).

**Session id.** Pass `--session-id <id>` to tag the sandbox with a
caller-defined session id. This adds the metadata flag
`bohr.launching.io/session-id=<id>` on the create body; the platform
records it for per-session lookup and uses it as the user-storage mount
subPath (`{prefix}/users/{userId}/{sessionId}/`), so sandboxes created
with the same session id share the same persisted directory (pairs with
`--mount-user-storage`). Independent of every other create flag.

**`lbg sdbx list` — age column and stale-sandbox warning.** The default
table view appends an `age` column showing how long each sandbox has
been alive (`Ns` / `Nm` / `NhMm` / `NdMh`). Rows older than 30 minutes
are highlighted (yellow on a TTY, ⚠ glyph everywhere — including
captured non-TTY output) and a one-line footer reminds you to kill them
if no longer in use. The footer is suppressed under `-q` and on
machine-readable channels (`--json`, `--csv`, `--yaml`). Structured
outputs add an `age_seconds` integer per entry so consumers can apply
their own highlight policy; the human ⚠ glyph is intentionally absent
from JSON/CSV/YAML.

**Pass the template `name`, not a SKU or numeric `id`.** Despite the
request field being called `templateID`, the backend accepts the
template's string `name` — the `name` column of `lbg sdbx template ls`,
or a platform shortcut. If you have a SKU value instead, list templates
with `lbg sdbx template ls` and list SKUs with `lbg sdbx machine list`.

For GPU workloads, use one of the GPU template shortcuts from
[`platform-snapshot.md`](../platform-snapshot.md):

```bash
lbg sdbx create <gpu-template> --json
lbg sdbx exec <sandbox_id> nvidia-smi
lbg sdbx exec <sandbox_id> python train.py --json
lbg sdbx kill <sandbox_id> --json
```

## Best practices

- **Reuse** running sandboxes whenever possible — check `lbg sdbx list` before creating new ones.
- **Check before killing** — run `lbg sdbx ps <sandbox_id>` (or `describe --with-processes`) to confirm no job is still running.
- **Save your work** before killing — data is permanently lost after kill. Use `lbg sdbx files` to retrieve important files first (see `lbg sdbx files --help`). For the full retrieve-before-kill SOP for long-running jobs, see [`execution-modes.md`](./execution-modes.md).
- **Kill promptly** when done — release resources with `lbg sdbx kill <sandbox_id>`. Safety behaviour:
  - No running processes → kill proceeds silently.
  - Running processes + TTY → interactive confirmation.
  - Running processes + non-TTY (agents, CI, piped shells) → the kill is **refused** with a clear error; pass `--force` to acknowledge and proceed.
  - Pass `--force` any time you know there is pending work you want to discard.
