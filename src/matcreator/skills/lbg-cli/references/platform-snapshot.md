# Platform snapshot (as of 2026-04-30)

Pinned CPU / GPU template shortcuts (ephemeral; review by 2026-06-01).

> Pinned snapshot of platform-side values that may drift over time.
> Everything in this section is ephemeral — review/remove after
> 2026-06-01 or when these stabilise. For a live view, use the
> commands noted below.

## CPU template shortcuts

Platform-managed CPU template shortcuts. Pass the `name` directly to
`lbg sdbx create`. Resource sizes are observed `cpuCount` / `memoryMB`
from a create response and may change.

| name | cpuCount | memoryMB |
| --- | --- | --- |
| `sdbx-cpu-mini` | 1 | 3200 |
| `sdbx-cpu-small` | 2 | 5248 |
| `sdbx-cpu-medium` | 4 | 9344 |
| `sdbx-cpu-large` | 16 | 33920 |

## GPU template shortcuts

Platform-managed GPU template shortcuts. Pass the `name` directly to
`lbg sdbx create`, or use the `--gpu` shortcut on the CLI. GPU shortcuts
are platform-managed and may change over time — consult
`lbg sdbx template ls` for the current list.

| name | image | sku | `--gpu` key |
| --- | --- | --- | --- |
| `scicomp-4090` | `pytorch20-scicomp:1.0.6` | `c16_m64_1 * NVIDIA 4090` | `--gpu` (default) or `--gpu 4090` |
| `scicomp-5090` | `pytorch20-scicomp:1.0.6` | `c16_m64_1 * NVIDIA 5090` | `--gpu 5090` |
| `scicomp-l20` | `pytorch20-scicomp:1.0.6` | `c16_m128_1 * NVIDIA L20` | `--gpu l20` |

`--gpu` is mutually exclusive with passing a positional template name.
Bare `--gpu` resolves to `scicomp-4090` (the default GPU template).

## SKU catalog

SKU availability is dynamic. Use the live list instead of copying values
from documentation:

```bash
lbg sdbx machine list          # list default SKU category
lbg sdbx machine list -c gpu   # list GPU SKUs
lbg sdbx machine list --json   # machine-readable SKU data
```

Pass the returned `sku_name` value to `lbg sdbx template create --sku-name`.
Quote SKU names that contain spaces or shell metacharacters.

## Example image

- Example image: `registry.dp.tech/dptech/abacus:LTSv3.10.1` (live list:
  `lbg image ls`).
