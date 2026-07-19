# Software install — references

On-demand notes for installing software inside a Bohrium sandbox or VM
node. Sourced from the public Bohrium documentation:

- <https://docs.bohrium.com/docs/userguide/image>
- <https://docs.bohrium.com/docs/software/OtherSoftwares>

## Workflow

1. **Check first.** Before installing anything, read
   [`pre-installed.md`](./pre-installed.md). Most common scientific
   tooling already ships with the platform image; reinstalling wastes
   time and bandwidth.
2. **Pick a domestic mirror.** If the package is not pre-installed, use
   [`mirrors.md`](./mirrors.md) to pick a domestic mirror for `pip`,
   `apt`, `yum`, or `conda`. Default upstream sources frequently time
   out from inside the platform.

> **On the default `sdbxagent` template** the image-level Aliyun PyPI
> mirror is already wired in, so domestic `pip install` is fast out of
> the box. Outbound HTTP proxy is **off by default**; for overseas
> indexes (`pypi.org`, GitHub, HuggingFace, Google Drive), toggle the
> `pai.ga.op.xdptech.com` proxy on for the duration of the overseas step and off
> when done — see [`../sandbox/network.md`](../sandbox/network.md) for
> the on / off snippets.
