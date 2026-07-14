# Sandbox network — on-demand HTTP proxy toggle

Sandboxes default to **no outbound HTTP proxy**. The image-level
`/etc/pip.conf` Aliyun mirror keeps domestic PyPI fast out of the box,
so most workflows need no extra setup. For overseas access (PyPI
`pypi.org`, GitHub, HuggingFace, Google Drive), toggle the
`pai.ga.op.xdptech.com:3128` HTTP proxy on with the snippet below; remember to
toggle it off afterwards so domestic mirrors stay fast.

The snippets below are inline and self-contained — they work on any
sandbox image, not just `sdbxagent`, and do not require fetching any
external script.

## Proxy on — enable `pai.ga.op.xdptech.com` HTTP proxy

Single ready-to-paste shell block. Sets the five user-level proxy
configs idempotently.

```bash
# pip — user-level proxy (image-level /etc/pip.conf Aliyun mirror is left untouched)
mkdir -p ~/.pip && cat > ~/.pip/pip.conf <<'EOF'
[global]
proxy=http://pai.ga.op.xdptech.com:3128
EOF

# conda / mamba
cat > ~/.condarc <<'EOF'
proxy_servers:
  http: http://pai.ga.op.xdptech.com:3128
  https: http://pai.ga.op.xdptech.com:3128
ssl_verify: false
EOF

# wget
cat > ~/.wgetrc <<'EOF'
http_proxy = http://pai.ga.op.xdptech.com:3128
https_proxy = http://pai.ga.op.xdptech.com:3128
use_proxy = yes
EOF

# curl
cat > ~/.curlrc <<'EOF'
proxy = http://pai.ga.op.xdptech.com:3128
EOF

# git — global config
git config --global http.proxy http://pai.ga.op.xdptech.com:3128
git config --global https.proxy http://pai.ga.op.xdptech.com:3128
```

## Proxy off — clear all user-level proxy configs

Single ready-to-paste shell block. Removes/empties the five user-level
configs and clears env-var fallbacks. Image-level `/etc/pip.conf`
(Aliyun mirror) stays untouched, so domestic `pip install` continues
to work.

```bash
# remove user-level proxy configs
rm -f ~/.pip/pip.conf ~/.condarc ~/.wgetrc ~/.curlrc

# unset git's global proxy
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# clear env-var fallbacks for the current shell
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
```

## Side effects of proxy on

When the proxy is on, **all** HTTP/HTTPS traffic for the configured
tools routes through `pai.ga.op.xdptech.com:3128`, including domestic targets like
`mirrors.aliyun.com`. Domestic access becomes noticeably slower than
the default off state. Turn the proxy on only when you actually need
overseas reach, and flip it off as soon as the overseas step is done.

The proxy also exhibits intermittent `503 / context deadline / TLS
recv` errors on long-lived TLS sessions (`git clone`, HuggingFace
downloads, large overseas `wget`). Retries usually succeed; for a
single command that keeps failing while the proxy is on, see
*Per-command bypass* below.

## How to use from an agent / inside `lbg sdbx exec`

Wrap the snippet in `bash -c` and ship it through `exec`:

```bash
# turn proxy on
lbg sdbx exec <sandbox-id> -- bash -c '
mkdir -p ~/.pip && cat > ~/.pip/pip.conf <<EOF
[global]
proxy=http://pai.ga.op.xdptech.com:3128
EOF
cat > ~/.condarc <<EOF
proxy_servers:
  http: http://pai.ga.op.xdptech.com:3128
  https: http://pai.ga.op.xdptech.com:3128
ssl_verify: false
EOF
cat > ~/.wgetrc <<EOF
http_proxy = http://pai.ga.op.xdptech.com:3128
https_proxy = http://pai.ga.op.xdptech.com:3128
use_proxy = yes
EOF
cat > ~/.curlrc <<EOF
proxy = http://pai.ga.op.xdptech.com:3128
EOF
git config --global http.proxy http://pai.ga.op.xdptech.com:3128
git config --global https.proxy http://pai.ga.op.xdptech.com:3128
'

# do your overseas-reach work, e.g.
lbg sdbx exec <sandbox-id> -- pip install -i https://pypi.org/simple/ click

# turn proxy off
lbg sdbx exec <sandbox-id> -- bash -c '
rm -f ~/.pip/pip.conf ~/.condarc ~/.wgetrc ~/.curlrc
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true
'
```

The on/off snippets are idempotent — running them twice is safe.

## Per-command bypass (proxy on, one call needs to go direct)

When the proxy is on but a single command needs to skip it (e.g. it
keeps timing out), override per invocation without touching the
session-wide config:

```bash
# wget — explicit flag
wget --no-proxy https://example.com/file

# curl — explicit flag
curl --noproxy '*' https://example.com/file

# git — per-invocation override (empty string disables)
git -c http.proxy= -c https.proxy= clone https://github.com/owner/repo

# pip — explicit empty proxy
pip install --proxy '' some-package

# any subprocess that respects http_proxy / https_proxy env vars
HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= <your-cmd>
```

## Reference baselines

End-to-end validated on a fresh sandbox:

| State | Domestic (Aliyun mirror, `mirrors.aliyun.com`) | Overseas (PyPI `pypi.org`, GitHub, HuggingFace, Google Drive) |
|---|---|---|
| Proxy off (default) | fast | unreachable |
| Proxy on | slow (all traffic via `pai.ga.op.xdptech.com:3128`) | reachable; HuggingFace and large `git clone` may need retries |

Verified with proxy on: `pip install -i https://pypi.org/simple/ click`,
`uv pip install --index-url https://pypi.org/simple/ tabulate`,
`git clone https://github.com/octocat/Hello-World`,
Google Drive `wget` (~5 MB/s sustained on a 296 MB file),
`curl -I https://huggingface.co/bert-base-uncased/resolve/main/config.json`.

`apt` is not available in the user-mode sandbox (no `sudo`/root).
System packages need to be baked at image build time, not added at
runtime.

## See also

- [`exec.md`](./exec.md) — running commands inside the sandbox
- [`../software-install/mirrors.md`](../software-install/mirrors.md) — public domestic mirrors (an alternative when running outside the platform)
- [`../platform-snapshot.md`](../platform-snapshot.md) — current default template and SKU shortcuts
