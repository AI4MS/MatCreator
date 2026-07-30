# Sandbox templates

List, create, and delete sandbox templates.

List templates you've already created, or create a new one.

```bash
lbg sdbx template ls              # table of your templates
lbg sdbx template ls --json       # machine-readable JSON
lbg sdbx template ls --page 2 --page-size 20 --json
lbg sdbx template ls -q           # names only (pipe into `lbg sdbx create`)
```

For compatibility, default `--json` output remains a template array. Passing
`--page` or `--page-size` switches listing to the paginated backend endpoint.
When paginating, the default page size is 100, and `--page-size` can be lowered
when an agent wants smaller responses. Paginated `--json` output is an envelope:
`page`, `page_size`, `total`, `total_pages`, and `list`. The template rows are
inside `list`; table output and `-q` render the selected page's `list` only.

**Compatibility note.** Older automation that uses
`lbg sdbx template ls --json` without pagination can keep reading the top-level
array. Automation that opts into `--page` or `--page-size` must read `list`
instead.

Creating a template requires an image reference and a SKU name:

```bash
lbg image ls                     # find an imagePath (paste into --image)
lbg sdbx machine list            # find a SKU name
lbg sdbx template create \
    --name <name> \
    --image <image-path> \
    --sku-name <sku>
```

> **`:latest` image tags are rejected.** `template create` and
> `template update --image` refuse any image whose tag ends in `latest`
> (e.g. `:latest`, `:dev-latest`) and ask you to use an immutable tag.
> Why: the backend pre-warms an image cache keyed by the image tag, and a
> mutable `:latest` cannot be reused — it is deleted-and-rebuilt on every
> create/update pointing at it. When several templates share one `…:latest`,
> that churns the cloud image-cache quota and makes every affected
> template's **new** sandboxes cold-pull the image (slow — can hit the
> create timeout) until the cache rebuilds. Use an **immutable / unique
> tag** instead: a version, or the unique tag that `lbg image build` /
> `lbg image commit` already produces.

## Disk size (extra ephemeral storage)

A sandbox's overlay (writable root) disk defaults to **30Gi**. The SKU only
sizes CPU/memory/GPU — it does **not** include disk. If models, datasets, or
Python envs fill the default disk you'll hit `OSError: No space left on device`
(e.g. a 7B + 1.5B model plus torch/transformers easily exceeds 30Gi).

Disk size is fixed per template at creation time (it cannot be changed on
`lbg sdbx create`). Request extra overlay storage with
`--extra-ephemeral-storage-gb` when creating the template:

```bash
lbg sdbx template create \
    --name <name> \
    --image <image-path> \
    --sku-name <sku> \
    --extra-ephemeral-storage-gb 50      # 30Gi default + 50Gi extra = 80Gi total
```

Rules:

- **Private templates only.** Public templates reject a non-zero value.
- **Quota-gated.** The amount is capped by your per-user quota. If the quota is
  unset / `0`, or the value exceeds it, the template is **still created
  successfully** but the extra storage is **not applied** (disk falls back to
  the default 30Gi) and the response carries a reminder to contact the
  administrator to raise the quota. Check the response `message`.
- Alternatively, mount a persistent personal/share disk with
  `lbg sdbx create <template> --mount-user-storage` to keep large model
  weights and datasets off the overlay disk entirely.

Repoint an existing template's image without recreating it. This is
atomic and image-only — `sku`/`cpu`/`mem`/`gpu` stay unchanged.

```bash
lbg sdbx template update <name> --image <new-image>          # repoint the image
lbg sdbx template update <name> --image <new-image> --json   # machine-readable result
```

Delete a template by name. TTY callers see a confirmation prompt;
non-TTY callers must pass `--force`. The call is idempotent — a missing
template comes back as `skipped` (exit 0).

```bash
lbg sdbx template rm <name>            # interactive confirm on TTY
lbg sdbx template rm <name> --force    # skip prompt (required for non-TTY)
lbg sdbx template rm <name> --json     # {"deleted": [...], "skipped": [...], "failed": []}
```

For concrete `<image-path>` and `<sku>` examples you can paste while
learning the command, see [`platform-snapshot.md`](../platform-snapshot.md).

Use `lbg image ls -q` to get image paths directly, suitable for shell
substitution. The default table view also shows `imageId`, `imageName`,
and `createTime` to help you pick a version.
