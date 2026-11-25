# Internal token workers and legacy private instances

The normal local entry point now opens the username/password account broker;
start with [local accounts](local-accounts.md). For the isolated Linux/WSL
backend, also follow [SRT provisioning](local-srt-provisioning.md).

This document describes the lower-level token-protected private worker and
legacy instance contract. It is not the normal account login interface. Use
`-TokenWorker` with the Windows review launcher or
`MATCREATOR_LOCAL_PROFILE_WORKER=1` with the shell launcher only when explicitly
starting an internal personal worker. Each such worker has an access token,
an opaque instance identifier and its own `MATCREATOR_HOME`; keep the token
private. Token workers under the same OS user are not execution sandboxes.

## Launch on Linux or HPC

Run as your own operating-system account. A normal private instance uses your
own `~/.matcreator` and its `workspace` directory:

```bash
export MATCREATOR_HOME="$HOME/.matcreator"
export MATCREATOR_LOCAL_PROFILE_WORKER=1
export MATCREATOR_ADK_PORT=18000
export MATCREATOR_WEB_PORT=18001
export MATCREATOR_FRONTEND_PORT=15173
./script/start_matcreator.sh
```

When multiple people use the same node, choose unused ports for each instance.
The launcher rejects an occupied endpoint and the ADK process holds a
process-lifetime lock on its data directory. Another instance needs another
home, not just another frontend port. A lock left on disk after a crash does
not prevent recovery: the operating system releases the actual lock.

The startup log shows the instance identifier, data directory and token-file
path without printing the token. Open the frontend and paste the token from
that private file. Alternatively, explicitly request a private launch URL:

```bash
matcreator access --url http://127.0.0.1:15173/
```

This command prints a secret. Do not put its output in shared logs, screenshots,
messages or issue reports. The launch link carries the token in its URL
fragment, which is removed by the frontend before it requests a login session.
The token is not kept in browser local storage or sent as a URL query string.

Browser access uses an instance-specific hostname such as
`mc-<instance-id>.localhost`. The access command converts a loopback URL to
this hostname while retaining the frontend or SSH-forwarded port. Modern
browsers resolve `.localhost` to loopback. Keep this exact hostname in the
launch link: cookies are scoped by host, not by TCP port. The server never
issues or accepts browser session cookies on a shared `localhost` or
`127.0.0.1` host; explicit Bearer API clients can still use those addresses.
Each identity file is bound to its canonical data directory and OS user.
Copying `.local-access` to a different home is rejected, not silently repaired.
The launcher rewrites a generic loopback address to the instance's dedicated
`mc-<instance-id>.localhost` browser hostname, keeping the port and path. Use
that hostname rather than a generic `localhost` address. Browser cookies do
not isolate different TCP ports; the dedicated hostname keeps one instance's
login cookie away from another instance on the same computer. The CLI rejects
URLs for unrelated hosts or another instance's browser hostname before
printing a token link.

Keep the services bound to loopback. Forward the frontend through your own SSH
connection when the server is remote, for example:

```bash
ssh -N -L 25173:127.0.0.1:15173 your-account@your-host
matcreator access --url http://127.0.0.1:25173/
```

Run `matcreator access` on the server in the instance's environment. Use the
resulting URL in the browser on your computer. A token is still necessary:
other users on the same compute node may be able to connect to its loopback
TCP ports. Different ports and an SSH tunnel are routing controls, not a
replacement for HTTP authentication.

## Windows local review launcher

The review helper uses this checkout's `.local-runtime/home` by default.
Pass `-RuntimeHome` explicitly to reuse an existing personal home. For an
explicit token-worker test, choose a private home and unused ports:

```powershell
.\script\start_local_review.ps1 -TokenWorker -RuntimeHome C:\MC\alice -AdkPort 28000 -WebPort 28001 -FrontendPort 25173
.\script\start_local_review.ps1 -TokenWorker -RuntimeHome C:\MC\bob -AdkPort 29000 -WebPort 29001 -FrontendPort 26173
```

The launch receipt is written to each home's `logs/launch.json` and contains
only process identifiers, paths, ports, the canonical frontend URL and
instance identity, never a token.

## Isolation boundary

Tokens protect HTTP, streaming and WebSocket access to an instance. Data must
also be protected by operating-system permissions. Independent directories
under the same operating-system account do not protect against that account
running arbitrary code to read another directory. On shared HPC systems use
separate system accounts with private homes. Team deployments need verified
server-side identities, authorization and restricted per-user execution
environments; local mode is not a substitute for that architecture.

Distinct browser hostnames prevent ordinary A/B cookie sharing; they are not
network namespaces. An intentionally malicious loopback service can impersonate
an instance hostname on a different port if it persuades a browser to visit
that URL. Use only the trusted launch URL with its correct port, and do not
open other services under an instance's hostname. This local HTTP design does
not claim protection against arbitrary hostile services on the same machine.

Do not copy a private instance's token file into another user's installation.
Do not share a configuration, database or working directory accidentally.
Starting a new home does not migrate or relabel old sessions and remote jobs.
