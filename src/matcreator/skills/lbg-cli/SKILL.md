---
name: lbg-cli
description: >-
  CLI for operating Bohrium cloud sandboxes (sdbx), managing templates,
  committing container images, and installing software inside sandboxes.
  Use when the user mentions lbg, sdbx, sandbox, or any Bohrium sandbox
  operation.
---

# lbg CLI — Sandbox (sdbx)

CLI for managing **cloud sandboxes (sdbx)** on the Bohrium platform.
Covers sandbox lifecycle (`create`, `exec`, `kill`, `list`, `describe`),
interactive PTY sessions, file transfer, template management, image
commit/build, and software installation inside sandboxes.

> **Proxy toggle.** Sandboxes default to no outbound HTTP proxy.
> For overseas access (PyPI `pypi.org`, GitHub, HuggingFace, etc.), toggle
> the `pai.ga.op.xdptech.com` proxy on with the snippet in
> [`references/sandbox/network.md`](references/sandbox/network.md),
> then toggle it off when done. Domestic access via the image-level Aliyun
> mirror is unaffected.

> **Default-behavior pitfalls (read first for ML / long-running jobs).**
> Several defaults are fine for quick interactive use but bite training
> workloads: the overlay disk is **fixed at 30Gi** (SKU has no disk field),
> `exec` foreground **`--timeout` is 60s**, `files read` transfers as **text**
> (corrupts binaries), `kill` is **irreversible** and a stopped sandbox's
> files can't be read, the outbound proxy is **off**, and a template image
> with a mutable `:latest` tag is **rejected** (it churns the prewarmed image
> cache — use an immutable tag). See the consolidated list
> in
> [`references/sandbox/pitfalls.md`](references/sandbox/pitfalls.md) before
> driving a sandbox through an agent.

## Prerequisites

```bash
lbg login --ak <your-access-key>
```

Or set `BOHRIUM_ACCESS_KEY` in the environment, or pass `--api-key` per call.

## Default sandbox template

`lbg sdbx create` with no template argument creates a sandbox from the
`sdbxagent` template — `pytorch20-scicomp:1.0.1` with the image-level Aliyun
PyPI mirror, so domestic `pip install` is fast out of the box. The outbound
HTTP proxy is **off by default**; for overseas access toggle it on demand via
the `proxy on` / `proxy off` snippets in
[`references/sandbox/network.md`](references/sandbox/network.md).

For GPU work, pass `--gpu` as a shortcut: bare `--gpu` resolves to
`scicomp-4090` (the default GPU template); `--gpu 5090` and `--gpu l20` pick
the other two GPU templates. Mutually exclusive with the positional
`template` argument.

## Command surface

| Command | Purpose |
| --- | --- |
| `lbg login` / `logout` | Persist a Bohrium access key |
| `lbg sdbx` | Operate cloud sandboxes (sdbx) |
| `lbg sdbx image` | Commit sandbox snapshots / build from Dockerfile |
| `lbg skill` | Export / diff / update this agent skill |

## Skill self-maintenance

Keep the installed copy of this skill current so agents never operate on stale
sandbox / sdbx docs. Each export writes a `skill-manifest.json` (lbg version,
export time, per-file `sha256`).

| Subcommand | Description |
| --- | --- |
| `skill export [--output <parent>]` | Write the skill tree to `<parent>/lbg-cli/` (fails on conflict) |
| `skill diff --target <skill-dir>` | Report drift vs the bundled version; exits non-zero when out of sync (`--json` for a machine-readable report) |
| `skill update --target <skill-dir> --backup` | Snapshot the old copy to `<name>.bak.<timestamp>`, then reinstall (`--force` overwrites in place; `install` is an alias) |

`--target` points at the skill directory itself (e.g.
`~/.codex/skills/lbg-cli`); `--output` on `export` is the parent directory.

## Sandbox commands

| Subcommand | Description |
| --- | --- |
| `create [template]` | Launch a new sandbox (default: `sdbxagent` template) |
| `exec <id> <cmd>` | Run a command inside a sandbox (foreground / background); pass `--user root` when root privileges are needed |
| `kill <id>` | Terminate a sandbox |
| `list` | List active sandboxes |
| `describe <id>` | Get sandbox details (incl. IP, status, template) |
| `terminal <id>` | Open an interactive PTY session |
| `upload <id> <src> <dst>` | Upload files or directories |
| `download <id> <src> [dst]` | Download files or directories |
| `image commit` | Snapshot a running sandbox's filesystem |
| `image build` | Build an image from a Dockerfile |
| `image get <id>` | Get image build status |
| `image ls` | List images (filterable by sandbox, status, name) |
| `image build-log <id>` | Fetch or follow build logs |
| `template create` | Create a new sandbox template |
| `template list` | List available templates |
| `template update` | Update a template |
| `template delete` | Delete a template |

## Discovering commands

```bash
lbg sdbx --help
lbg sdbx <subcommand> --help
```

## Reference docs

Deep usage docs under `references/`. Load only what's relevant.

**Sandbox lifecycle & usage:**

- [`references/sandbox/pitfalls.md`](references/sandbox/pitfalls.md) — default-behavior traps (disk 30Gi, timeouts, binary downloads, kill, proxy, billing) + an ML-training checklist
- [`references/sandbox/lifecycle.md`](references/sandbox/lifecycle.md) — create / list / describe / kill, plus best practices
- [`references/sandbox/execution-modes.md`](references/sandbox/execution-modes.md) — foreground vs background vs PTY, plus the retrieve-before-kill SOP for long-running jobs
- [`references/sandbox/exec.md`](references/sandbox/exec.md) — `lbg sdbx exec` usage, including `--background`
- [`references/sandbox/terminal.md`](references/sandbox/terminal.md) — PTY sessions for REPLs / TUIs / Ctrl-C interaction
- [`references/sandbox/files.md`](references/sandbox/files.md) — upload / download files and directories
- [`references/sandbox/templates.md`](references/sandbox/templates.md) — list, create, update, and delete sandbox templates
- [`references/sandbox/network.md`](references/sandbox/network.md) — on-demand `pai.ga.op.xdptech.com` HTTP proxy toggle

**Container images (sandbox snapshots & builds):**

- [`references/images.md`](references/images.md) — `lbg sdbx image commit` / `image build` for producing container images

**Software install (inside sandboxes):**

- [`references/software-install/README.md`](references/software-install/README.md) — pre-installed inventory + domestic mirrors for sandbox installs

## Output formats

`lbg sdbx` commands accept `--json` for machine-readable output suitable
for scripting and agent use.