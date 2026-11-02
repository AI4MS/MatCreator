# Local account isolation: review status

This change is submitted for design and code review. It is not a completed
production security acceptance or a migration of existing installations.

## Reproducible software checks

Run `python script/check_local_accounts.py -q --tb=short` from an environment
with the project and pytest installed. It uses a disposable home so tests do not
read a developer's normal MC configuration or credentials. The GitHub test
workflow also runs this command.

On Windows, the review checkout passed **426 tests**, with **5 skips** and one
deselected preexisting server bind-mount drive-letter parsing test. The skips
concern platform permissions/capabilities. This result covers account sessions,
profile paths, signed runtime probes, startup conflicts, frontend routing
contracts, SRT policy/controller/channel and offline SSH proxy behavior. It
does not launch two full SRT account workers.

The first-install provisioning helper also passed **13 contract tests**, with
one Windows symlink-permission skip. These tests use temporary paths and fake
venv/pip operations; they perform no installation, download or service action.

The unchanged frontend sources passed **262 tests** with Node 24 and
`node --test --test-concurrency=1`. A fresh local production build was withheld
because desktop memory was low; the GitHub Frontend job runs `npm run check`,
including the build. CI results should be checked independently of local tests.

## Real Linux primitive evidence

Prior synthetic probes used SRT 0.0.81, Node 24.19.0 and Ubuntu bubblewrap
0.9.0. They demonstrated:

- A non-root process could read/write its own profile but could not read/write
  another profile, even when both fixture directories had the same OS owner.
- PID/network namespaces prevented observing the host processes or reaching
  host loopback directly. New Unix/VSOCK sockets remained restricted.
- Anonymous pipe ingress supported byte transfer and Python asyncio. An
  inherited host TCP descriptor was experimentally shown to permit host
  reconnection and was rejected; production ingress uses only pipes.
- A short-lived systemd/SRT probe had distinct PID/network/IPC/UTS namespaces,
  NoNewPrivileges, a 192MiB memory ceiling, zero swap, 64 tasks, a 50% CPU quota
  and logical CPU 2. Kernel cgroup files matched those budgets, and
  `sched_setaffinity` could not move it outside that cpuset. The probe stopped
  and its exact transient unit was collected.

These checks establish specific boundary mechanisms. They do not establish
the complete MC lifecycle, performance or resistance to kernel vulnerabilities.
Local logs, identities, account databases and machine-specific audit paths are
intentionally excluded from the repository.

## Required before deployment acceptance

- Run the full Web/ADK stack for two simultaneous SRT accounts, checking session,
  file, memory/configuration, terminal, HTTP/SSE/WebSocket and logout/switching
  boundaries, including delayed responses from the previous account.
- Confirm exact worker/task reattachment after broker shutdown and a WSL idle
  interval, without starting a replacement or duplicate job.
- Measure idle/per-task overhead, multiple accounts under load, and neighbor
  interference at explicitly assigned logical CPUs. Resource limits do not
  reserve physical CPUs or impose persistent disk quotas.
- Plan, back up and validate any legacy data migration separately. Existing
  profiles are not automatically moved or assigned to a new SRT identity.

The previous full-account functional attempt stopped before creating any
worker because available desktop memory was below its 2560MiB reserve. The
original account/session database hashes remained unchanged. Code completion
and the checks above must not be described as full two-account acceptance.
