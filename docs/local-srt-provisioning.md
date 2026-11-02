# First installation of the shared Linux SRT runtime

This is an administrator workflow for **Ubuntu 24.04 x86-64**, either native
Linux or the selected WSL distribution. The account controller currently pins
Node 24.19.0 and sandbox-runtime 0.0.81 at the paths below. Docker is not used.
Other architectures and native Windows SRT are not supported by this backend.
On WSL, systemd must already be enabled in the selected distribution.

The helper performs first installation only. `prepare` refuses any existing
`/opt/matcreator-srt-runtime`, and `finalize` refuses an existing manifest. It
never removes a runtime, migrates profiles, updates an active installation,
starts an account service or changes a systemd unit. An existing or interrupted
installation requires administrator inspection and a separate update plan.

## System and pinned tools

Run these commands in the target Linux distribution. The private tools prefix
must be new; do not reuse another installation or overwrite it.

```bash
set -eu
test "$(uname -m)" = x86_64
sudo apt-get update
sudo apt-get install -y bubblewrap socat ripgrep python3.12-venv curl xz-utils git
systemctl show --property=Version --value
cat /sys/fs/cgroup/cgroup.controllers
# The controller requires cpu, memory, pids and cpuset controllers.

test ! -e /opt/matcreator-srt-tools
test ! -L /opt/matcreator-srt-tools
sudo install -d -m 0755 /opt/matcreator-srt-tools
download_dir="$(mktemp -d)"
cd "$download_dir"
curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
  --output node-v24.19.0-linux-x64.tar.xz \
  https://nodejs.org/dist/v24.19.0/node-v24.19.0-linux-x64.tar.xz
curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
  --output SHASUMS256.txt https://nodejs.org/dist/v24.19.0/SHASUMS256.txt
rg '  node-v24\.19\.0-linux-x64\.tar\.xz$' SHASUMS256.txt | sha256sum --check -
sudo tar --no-same-owner --no-same-permissions -xJf node-v24.19.0-linux-x64.tar.xz \
  -C /opt/matcreator-srt-tools
sudo /opt/matcreator-srt-tools/node-v24.19.0-linux-x64/bin/node \
  /opt/matcreator-srt-tools/node-v24.19.0-linux-x64/lib/node_modules/npm/bin/npm-cli.js \
  install --ignore-scripts --no-audit --no-fund \
  --prefix /opt/matcreator-srt-tools/srt-0.0.81 @anthropic-ai/sandbox-runtime@0.0.81
sudo chown -R root:root /opt/matcreator-srt-tools
sudo chmod -R go-w /opt/matcreator-srt-tools
```

The SHA-256 check uses the official Node release checksum list over HTTPS.
`--no-same-owner` is important: preserving the archive's numeric UID can make
the extracted runtime owned by an ordinary Linux user. Keep the tools and all
path components root-owned and not writable by group or other users.

## Public application snapshot and shared dependencies

Use a reviewed checkout of this PR. Replace the example source path with that
checkout's Linux path. On WSL it may be the Windows checkout's `/mnt/...` path;
the helper copies selected public files into Linux storage rather than mounting
the development checkout into an account sandbox.

```bash
cd /path/to/MatCreator
sudo python3.12 -I script/provision_local_srt.py prepare --source-root "$PWD"

# This is the explicit network-enabled package installation step. It installs
# MC dependencies once for all accounts; prepare/finalize do not download them.
sudo /opt/matcreator-srt-runtime/venv/bin/python -I -m pip install \
  -e /opt/matcreator-srt-runtime/app
sudo /opt/matcreator-srt-runtime/venv/bin/python -I -m pip check
sudo python3.12 -I script/provision_local_srt.py finalize
```

`prepare` copies `src/**` with private/data/cache/environment files excluded,
only direct `web/*.py` modules, `pyproject.toml` and `README.md`. It creates a
shared venv and a trusted `bwrap` wrapper adding IPC/UTS namespaces. It does not
copy databases, backups, credentials, runtime homes or an entire workspace.

`finalize` checks root ownership, write permissions and the exact prepared
source. It includes the new public setuptools `matcreator.egg-info` metadata,
captures `dependencies.freeze.txt`, and writes `manifest.json`. The source
digest is SHA-256 of canonical JSON for the manifest's file map; the separate
runtime digest is SHA-256 of the complete manifest bytes. Unexpected files,
public symlinks, modified source or an unsafe venv fail without repairing or
overwriting an existing installation. Root-owned venv links to immutable system
interpreters are allowed. Retain the preparation record, manifest and freeze.

## Read-only preflight and broker selection

The following checks the real tools, immutable files, systemd and cgroup
capabilities. It does not register an account or start a worker:

```bash
printf '%s\n' '{"version":1,"action":"preflight","installation_id":"00000000000000000000000000000000","runtime_root":"/opt/matcreator-srt-runtime","state_root":"/var/lib/matcreator-srt"}' \
  | sudo python3.12 -I /opt/matcreator-srt-runtime/app/src/matcreator/scripts/local_srt_host.py
```

Require `"ok":true` and retain the returned source/runtime hashes before
starting the broker. Native Linux provisioning and controller execution require
trusted administrator control; the controller is not a public remote service.
On Windows the default controller selects `Ubuntu-24.04` through WSL; use that
same distribution for every step above. Then select `-RuntimeBackend srt` in
the PowerShell launcher, or `MATCREATOR_LOCAL_RUNTIME=srt` on native Linux.

Follow [local accounts](local-accounts.md) for authentication, resource limits,
administrator `host:port` egress grants and lifecycle behavior. Egress defaults
to denied. Only new empty profiles are currently supported: existing process
profiles, old conversations and active remote jobs need a separately reviewed
offline migration. Selecting SRT does not migrate or resubmit them.

The provisioning helper's contract tests do not establish full MC multi-account
acceptance. Complete the two-account Web/ADK/session/file/terminal/reconnection
checks in an environment with sufficient RAM before treating the backend as
deployment-ready.
