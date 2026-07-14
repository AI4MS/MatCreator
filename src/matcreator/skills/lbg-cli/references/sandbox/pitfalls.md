# Default-behavior pitfalls

Every item below is a **default** that is safe for quick interactive use but
bites long-running / ML-training workloads. Read this once before driving a
sandbox through an agent; each row links to the deep doc.

| # | Default | When it bites | Do this instead |
|---|---------|---------------|-----------------|
| 1 | Overlay (writable root) disk is **fixed at 30Gi**; SKU sizes CPU/mem/GPU only, **not disk** | A 7B + 1.5B model plus torch/transformers and a dataset overflow 30Gi → `OSError: No space left on device` | Disk is **set at template-create time**, not on `sdbx create`: build a private template with `--extra-ephemeral-storage-gb N` (quota-gated), or keep big weights off the overlay with `sdbx create --mount-user-storage`. See [`templates.md`](./templates.md). |
| 2 | `files read` transfers as **text** unless told otherwise | Reading a binary (`.pt`, `.tar`, `.gz`, `.bin`, ...) as text decodes+re-encodes the bytes → wrong file size, `torch.load`/unzip fails with "invalid header or archive is corrupted" | Now **auto-detected from the extension** (binary → bytes, with a stderr warning). For an unrecognized binary extension pass `--format bytes`. See [`files.md`](./files.md). |
| 3 | `exec` foreground **`--timeout` is 60s** | A 30-min training/install run is killed at 60s (and a flaky link can drop it sooner) | Use `exec --background` (defaults to unlimited `--timeout 0`) and retrieve outputs before kill. See [`execution-modes.md`](./execution-modes.md). |
| 4 | `--background` with a **finite `--timeout`** kills the remote command at that boundary | `exec --background --timeout 60` silently dies at 60s even though it's "background" | Omit `--timeout` or pass `--timeout 0` for true long-running work. The CLI warns, but the job still dies. |
| 5 | `kill` **permanently destroys** the sandbox; a stopped sandbox's files **cannot be read** | Killing before pulling outputs loses everything under `/workspace/` | Retrieve every file you need with `files read --output ...` **before** `kill`. Non-TTY `kill` with live processes is refused unless you pass `--force`. See [`execution-modes.md`](./execution-modes.md). |
| 6 | Sandbox **auto-destroys after 12h** (CLI default `timeout=43200`); idle sandboxes may also be reclaimed by the platform after a period of no API activity | A multi-day job, or one left idle, gets torn down underneath you | `sdbx create --timeout N` to set the lifetime, or `--never-timeout` to opt out (then `kill` yourself — long-lived sandboxes keep billing). See [`lifecycle.md`](./lifecycle.md). |
| 7 | Outbound **HTTP proxy is off** | `pip install` from `pypi.org`, `git clone` GitHub, HuggingFace downloads hang/fail; domestic Aliyun mirror still works | Toggle the `pai.ga.op.xdptech.com` proxy on for overseas access, then off when done. See [`network.md`](./network.md). |
| 8 | No `--project-id` → bills your **personal wallet** | Create is rejected if the personal wallet balance is ≤ 0; project budget is not used | Pass `--project-id <id>` to bill a project budget (find IDs with `lbg project ls`). See [`lifecycle.md`](./lifecycle.md). |
| 9 | **No user storage mounted** | Expecting your personal/share disk inside the sandbox; it isn't there | `sdbx create --mount-user-storage`. See [`lifecycle.md`](./lifecycle.md). |
| 10 | `terminal send` has **no implicit newline and no stdout echo** | Treating `terminal send` as run-and-capture returns only `sent_bytes`, not the command output | For "run a command, get output" use `exec`. In a PTY, append `\n` and read the redirected file. See [`terminal.md`](./terminal.md). |
| 11 | A template image with a mutable `:latest` tag is **rejected** by `template create` / `template update --image` | A template's image cache is keyed by the image tag; a `:latest` cannot be reused (old cache deleted, then rebuilt on every create/update), and sharing one `…:latest` across templates churns the cloud image-cache quota → other templates' **new** sandboxes fall back to a slow cold pull (can hit the create timeout) until the cache rebuilds | Point template images at an **immutable / unique tag** (a version, or the unique tag `image build` / `image commit` already produce). `image commit`/`build` also **warn** (non-fatal, `--name` is only the user-visible portion) when you name a new image `…latest`, so you don't mint one you can't use as a template base. See [`templates.md`](./templates.md) and [`images.md`](../images.md). |

## Quick checklist for an ML training run

```bash
# 1. Enough disk: build a template with extra overlay storage (one-time)
lbg sdbx template create --name train-tpl --image <img> --sku-name <sku> \
    --extra-ephemeral-storage-gb 80

# 2. Create with an explicit lifetime + project billing
lbg sdbx create train-tpl --timeout 0 --project-id <id> --json   # 0 = no auto-destroy

# 3. (overseas deps) proxy on, install, proxy off  — see network.md

# 4. Run long jobs in the background, log to a persistent path
lbg sdbx exec --background <id> 'mkdir -p /workspace/out && python train.py > /workspace/out/run.log 2>&1'

# 5. Retrieve BEFORE kill; binaries download losslessly (auto bytes)
lbg sdbx files read <id> /workspace/out/run.log    --output ./run.log
lbg sdbx files read <id> /workspace/out/model.pt   --output ./model.pt

# 6. Done -> kill (frees resources; long-lived sandboxes keep billing)
lbg sdbx kill <id>
```

## See also

- [`lifecycle.md`](./lifecycle.md) — timeout / billing / mount / kill semantics
- [`execution-modes.md`](./execution-modes.md) — foreground vs background vs PTY, retrieve-before-kill SOP
- [`files.md`](./files.md) — text vs bytes download
- [`templates.md`](./templates.md) — disk size via `--extra-ephemeral-storage-gb`
- [`network.md`](./network.md) — outbound proxy toggle
