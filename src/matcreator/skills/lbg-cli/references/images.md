# Image commit & dockerfile build

`lbg sdbx image commit` / `image build`: produce container images.

Two ways to mint a new container image; both are async, both surface in
`lbg sdbx image ls`, and the `buildType` column tells them apart:

| Subcommand | When to use | Source |
|---|---|---|
| `image commit` | snapshot a *running* sandbox's filesystem | needs `--sandbox-id` |
| `image build`  | reproducible build from source              | needs `--dockerfile <file>` |

Both jobs return the new image record's `id` *and* its final `imageUrl`
**immediately** on submission — the URL is reserved up-front so it can
be plugged into a later `lbg sdbx template create --image <imageUrl>`
without polling. Polling `get <id>` is only needed if you actually want
to wait for `success` / `failed` before consuming the image.

```bash
# A) commit — snapshot a running sandbox
lbg sdbx image commit \
    --sandbox-id <sandbox_id> \
    --name <image-name> \
    --desc "training-day-3 snapshot" \
    --project-id <project_id> \
    --json

# B) build — from a local Dockerfile (no sandbox needed)
lbg sdbx image build \
    --dockerfile ./Dockerfile \
    --name <image-name> \
    --desc "pinned cuda 12.1" \
    --project-id <project_id> \
    --json

# B') tag the build with the sandbox it originated from (provenance only)
lbg sdbx image build \
    --dockerfile ./Dockerfile \
    --name <image-name> \
    --sandbox-id <sandbox_id> \
    --project-id <project_id>

# poll either kind (status: 0=creating 1=pending 2=success 3=failed)
lbg sdbx image get <id> --json

# fetch the kaniko log of a dockerfile build (commit records have no log)
lbg sdbx image build-log <id>            # raw text snapshot to stdout, pipe-friendly
lbg sdbx image build-log <id> --json     # {"id": ..., "log": "..."}
lbg sdbx image build-log <id> --follow   # stream chunks as kaniko writes them,
                                         # auto-EOFs when the build reaches a
                                         # terminal status (success/failed).
                                         # Mutually exclusive with --json.

# browse your images across all sandboxes / build types
lbg sdbx image ls                                  # default table (incl. statusReason)
lbg sdbx image ls --json                           # full envelope (items+page+total)
lbg sdbx image ls --sandbox-id <sandbox_id>        # one sandbox's records
lbg sdbx image ls --status 2 --status 3            # successes + failures
lbg sdbx image ls --name train --start-time '2026-04-01 00:00:00'
lbg sdbx image ls -q                               # ids only, one per line
```

## Naming — avoid a `latest` image name

`--name` is the **user-visible portion** of the image name; the backend may
prepend a user/project prefix and controls the final registry tag, so the CLI
does *not* hard-reject it here. But if the name ends in `latest`
(`--name latest`, `--name img:latest`, `--name img:dev-latest`, …) the CLI
prints a **stderr hint** (stdout / `--json` stay clean) nudging you toward an
immutable/unique name. Reason: a `latest` image is a poor template base —
`lbg sdbx template create/update` **rejects** a `:latest` template image, and
the platform's prewarmed image cache cannot be reused for a mutable tag. Prefer
a version (`:v1`) or a date/build tag so the image you just minted can be
plugged straight into `template create --image` without a rename.

## Status & failure reason

`status` is the coarse lifecycle (`0=creating 1=pending 2=success 3=failed`).
For `failed` records, `statusReason` is a **structured machine-readable code**
that explains *why* it failed — pair it with the free-form `errorMsg` for the
full picture. The field is `omitempty`: it never appears on success rows, and
it is also absent on historical failed rows created before this column was
introduced (no backfill).

| `statusReason` | What happened |
|---|---|
| `build_timeout`     | Dockerfile build did not finish within the backend timeout window (default 1800s) — typically still in `ImageBuildPushing[6]` when the deadline hit. Run `lbg sdbx image build-log <id>` to see how far kaniko got. |
| `kaniko_failed`     | mid-lbg-image reported a non-zero kaniko exit (image-not-found, RUN command non-zero, etc.). `lbg sdbx image build-log <id>` shows the raw kaniko log. |
| `commit_failed`     | ACS Commit CRD reported `phase=Failed`. `errorMsg` carries the controller-level reason; there is no kaniko-style stdout log for commit builds. |
| `commit_timeout`    | Sandbox-commit did not reach `Succeeded` within the backend timeout window. |
| `register_failed`   | Commit succeeded and the image is already in the registry, but the post-commit `RegisterImage` call to mid-lbg-image failed (rare, usually transient — safe to retry). |
| `crd_create_failed` | Commit CRD could not be created (sandbox missing, permissions, or a same-name CRD already exists). |

Use `--follow` only with **dockerfile** builds (`buildType=2`); the CLI rejects
`build-log --follow` on commit records (`buildType=1`) immediately, because
commit builds run inside Alibaba Cloud ECI and only expose ~5 coarse phase
messages, not per-line kaniko stdout. Inspect commit failures via `image get
<id>` (`statusReason` + `errorMsg`) instead.

Both `commit` and `build` require `projectId` in the request body. The
CLI sends the same value both in the body and as the `X-PROJECT-ID`
header, so either channel — `--project-id <id>` or any future global
X-PROJECT-ID source picked up by `SdbxSettings.project_id` — satisfies
the requirement. If neither is set, the CLI refuses the call with
`missing project id` (exit 2) instead of letting the backend return a
generic 400.

`image build` reads the Dockerfile locally and sends it inline; the
hard cap is **64 KiB** (matches the launching backend's
`BohrSandboxImageBuildReq` limit). The CLI rejects oversized files
client-side with exit 2. The `--json` echo of the request elides the
Dockerfile body — only `dockerfileSize` is shown — so logs stay clean;
the response (`id`, `imageUrl`) is unmodified.
