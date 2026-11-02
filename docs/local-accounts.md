# Local accounts and personal profiles

Local account mode keeps registration, password verification and personal data
on the machine running MC. It does not create a cloud account or upload data.
The browser uses a username and password; worker access tokens are an internal
connection credential and are not the normal login interface.

Review status: the account and SRT implementation is available for review.
Complete two-account SRT acceptance, resource pressure testing and migration of
existing profiles are still pending. The process backend remains the default.

## First use and existing data

Open the private address provided by the installation owner's local launcher.
That launch session authorizes first-account creation without showing an
internal token-entry form. Choose your own username and password. The account
has a stable internal UUID; its visible
name is not used as a filesystem path or as proof of data ownership.

Anonymous visitors cannot register execution accounts. A trusted owner launch
session or an already authenticated local account must authorize registration.
This matters on shared compute nodes: an anonymously created account must not
gain the installation owner's system-user code-execution privileges merely by
choosing a username and password. Authorized existing users can provision
another local account; its new profile is still empty by default.

A normal registration creates a new, empty profile. MC does not automatically
assign an installation's old conversations, configuration, API keys or remote
jobs to whoever registers first. Existing installation data can be claimed by
one account only after proving ownership through the trusted local launch
session. The claim must be selected explicitly during registration.

Claiming existing data keeps the existing storage location and task identities;
it does not resubmit, cancel, rename or relabel remote jobs. Until a successful
claim, the existing data remains on disk and is not visible to newly registered
accounts. Keep the installation's backup until the imported profile has been
checked.

The application does not choose a password for a real user. There is no shared
default username/password. Passwords are stored as salted password hashes,
not plaintext or reversible encrypted login passwords.

## Logging in, switching and logging out

Logging in establishes an expiring, server-verified browser session for one
local account. API requests derive the account from that session, rather than
trusting a client-supplied user ID. Browser state and selected conversations
are scoped by both installation identity and account identity.

Switching accounts or logging out clears the displayed conversation and cached
personal state. Logging out revokes that browser session and any owner-setup
proof left in that browser. Successful account registration also consumes its
owner-setup proof. Logging out does not stop the
account's background worker or cancel its remote computations. A background
task remains associated with the account and profile that created it, and the
same worker can be reattached after a broker restart.

Each account uses a separate profile directory and a separate Web/ADK runtime.
Personal conversations, memory, configuration, credentials, custom skills and
files belong to that profile. Program code and maintained built-in resources
may be shared read-only. A new profile does not inherit the previous account's
API keys or compute-platform credentials.

New accounts' workspace, custom-work-directory and file API selections are
confined to their own profile directory. An external project path is not
implicitly owned by an account merely because it can be typed into a setting;
external project access needs an explicit ownership-grant design. The account
that explicitly claims existing installation data may retain its previous
external workspace, while new accounts cannot select or read that directory.
Use the normal upload/import controls to bring selected input files into a new
account's workspace.

The normal local login does not authorize an administrator to browse every
profile. A forged user ID, another account's session ID, or an internal worker
token must not select another account's runtime through the browser broker.

## Execution backends

`MATCREATOR_LOCAL_RUNTIME=srt` selects the opt-in isolated Linux backend.
It uses Anthropic sandbox-runtime (SRT) with bubblewrap on Linux, including
Linux inside WSL on Windows. It requires no Docker daemon or account image.
Native Windows SRT is deliberately refused: upstream's write-restricted token
does not block reads of another sandbox's files. A Windows process profile is
not silently converted to a Linux profile.

Follow [Linux/WSL provisioning](local-srt-provisioning.md) before selecting SRT.
An administrator provisions one shared, root-owned Linux runtime in
`/opt/matcreator-srt-runtime` and Node/SRT in `/opt/matcreator-srt-tools`.
The public snapshot contains only maintained code/resources and a shared
Python environment. Never grant read access to an entire development checkout:
it may contain user databases, credentials, logs or backups. A manifest pins
the public files and dependency freeze; each account record pins its source,
policy and runtime identity. Changing an active worker's installation requires
an explicit rollout rather than silently adopting a different runtime.

Each account has a distinct Linux UID/GID, a private 0700 home, one dedicated
SRT process and its own Web/ADK pair. bubblewrap supplies mount, PID, user and
network namespaces; the trusted wrapper also isolates IPC and UTS. The policy
denies filesystem reads by default, grants public system/runtime reads and the
account's own home, and confines persistent writes to that home. Shared default
temporary paths are denied. New Unix/VSOCK sockets remain restricted.

The trusted per-account supervisor listens on host loopback. Only anonymous
stdin/stdout pipes enter SRT; a bounded framed channel relays raw requests to
the fixed Web listener inside that account's namespace. HTTP, SSE and WebSocket
use the same path. Host network socket descriptors are never passed into the
sandbox. Web and ADK must both pass nonce-bound signature challenges before
the broker sends an internal credential.

systemd cgroup v2 limits apply to all of an account's processes, including the
supervisor, SRT and tools. Administrator settings are
`MATCREATOR_LOCAL_SRT_MEMORY_MB` (default 1536), `MATCREATOR_LOCAL_SRT_CPUS`
(default one core-equivalent), `MATCREATOR_LOCAL_SRT_PIDS` (default 256), and
`MATCREATOR_LOCAL_SRT_CPUSETS` (a non-overlapping JSON UUID-to-CPU-list map).
Swap is disabled for account service cgroups (`MemorySwapMax=0`). AllowedCPUs
is a kernel cpuset constraint, not just taskset affinity. It does
not reserve a physical CPU from other host services; WSL CPU numbers are Linux
virtual/logical CPUs. This release does not impose persistent disk quotas.

Outbound networking defaults to denied. Set
`MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS` to a finite JSON list of explicit
`host:port` entries (for example `["api.example.org:443"]`); bare hosts are
refused. `MATCREATOR_LOCAL_SRT_DENIED_DOMAINS` further restricts access.
`MATCREATOR_LOCAL_SRT_PARENT_PROXY` optionally selects the administrator's
upstream HTTP/HTTPS proxy; it does not grant additional destinations. Account
settings cannot widen this policy. Direct sockets do not bypass the namespace;
applications need an appropriate HTTP/SOCKS transport for allowed egress.

On Windows, use `-RuntimeBackend srt` with `script/start_local_review.ps1`,
or set the runtime environment variable. The default controller command is
`["wsl.exe","-d","Ubuntu-24.04","--"]`; it can be selected with
`MATCREATOR_LOCAL_SRT_COMMAND` or the launcher's `-SrtCommand` argument.
Native Linux uses an administrator-controlled Python controller. Provisioning
requires trusted administrator control; the controller is not a public network
service. Missing prerequisites, conflicting identities or failed readiness
checks stop startup without falling back to an unsandboxed process.

Logout and broker shutdown preserve account workers and their tasks. A hidden,
installation-scoped foreground WSL hold keeps the distro alive while the exact
account systemd units run; it exits when none remain. No global WSL settings or
scheduled task is changed. Test-owned stopping requires the exact original
ownership receipt and retains the account UID, private data and audit records.
SRT currently accepts new empty profiles only. Legacy claims and populated
process profiles require a separately planned offline migration.

`MATCREATOR_LOCAL_RUNTIME=process` remains the compatibility default. It starts a
private Web/ADK process pair per account under the launcher's OS user.

## Deployment boundary

All backends enforce access through MC's authenticated HTTP, streaming and
WebSocket interfaces. Process mode does not sandbox arbitrary local code:
profiles under the same OS user still share that user's filesystem privileges.
SRT adds Linux identities, namespaces, read/write and network policy, plus
cgroup resource boundaries. It shares the Linux kernel; the root controller,
immutable installation, supervisor and SRT remain trusted. It is not a VM or
a claim of protection from kernel vulnerabilities. Mutually hostile public
workloads need a deployment-specific
security review and may require a stronger execution runtime.

Keep the launcher address, hostname and port together. Local browser hostnames
prevent ordinary cookie collisions between installations; they do not provide
network namespaces or protect against an arbitrary malicious local HTTP service.

## Future server accounts

The local account UUID and explicit profile ownership provide a foundation for
later server identity links, synchronization, projects and groups. This release
does not implement those services. A future cloud identity must be verified and
explicitly linked; identical local and server usernames are not proof of the
same person. Passwords and internal worker tokens are not personal documents
to upload. Group membership and shared-resource permissions must be checked by
the server when those features are introduced.
