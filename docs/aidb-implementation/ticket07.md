# Ticket 07 — Managed VASP execution paths

| Entry | Backend | Before execution | After execution |
| --- | --- | --- | --- |
| `vasp-pymatgen` prepared inputs and `submit_bohr_batchjob` | Bohrium Batch Job via CLI | Shared prepared-input guard and launcher staging | Tracked Batch Job collection into private storage, then shared VASP result receiver |
| `vasp-pymatgen` prepared inputs and `start_managed_vasp_sandbox` | Bohrium sandbox via CLI | Same guard, before fresh sandbox allocation, then same launcher staging before upload and tracked background command | Existing background-command poll/monitor, then `collect_remote_job_outputs` transfers the sandbox files and invokes the same receiver |
| `aidb_archive_vasp` | Existing workspace outputs | Explicit active import from chat | Shared archive parser, write and readback with active import provenance |

The preparation skill supplies input-only and result-reading references. Those
operations do not launch a tracked job or invoke passive AIDB hooks. The
generic workspace shell and sandbox commands are also used for preparation,
diagnostics and non-VASP tasks. The repository has no separately registered
managed local or Slurm VASP execution route. Legacy Bohrium job/job-group
instructions are historical. E2B remains a provider for existing jobs, and its
submission tool is absent from the executor registry.

New managed VASP execution routes must call the prepared-input guard with the
actual inputs and send verified outputs through the shared result receiver.
Keep database calls at those boundaries. The sandbox regression uses the public
job tools to verify the documented direct VASP commands route to the managed
launch instead of starting through the generic command tools.

A sandbox allocation and its tracked command have separate lifecycles. A
completed command is collectable while the sandbox remains running. The
collector transfers the original output files into private storage before
validation, keeps successfully transferred files across retries, and publishes
safe artifacts even when a required transfer, command, result check or archive
fails. The database and artifact delivery outcomes are available independently.
One sandbox ID tracks one managed VASP run and completion. On a failed write,
repeating collection uses the original input revision, provider ID, output and
session binding.

The automated replay uses the repository's real historical Si relaxation
XML/OUTCAR and a synthetic POTCAR header fixture. It verifies a zero-match
query, successful collection and installed-AIDB readback, reuse from a sandbox
archive without allocating a second sandbox, and distinct outcomes for a
command failure, missing output, interrupted transfer and database write
failure. The replay does not create an external sandbox or submit a VASP job.

## Normal-chat and browser acceptance — 2026-11-13

The managed Web and ADK routes accepted a natural-language sandbox relaxation request. A scripted planner selected the production VASP tool. The provider adapter replayed the repository's original Si XML and OUTCAR through the production launcher, background command and collection boundaries. The run had a private copy of the installed AIDB skill, an isolated SQLite database and loopback-only networking.

- Run `fa6b3694a28d4fd88b8b8c8e18603870` allocated one sandbox after the precheck, launched one command and downloaded the two raw outputs and launcher receipt. AIDB read back record `f317386f078bfcaec63f6e4802c96ab1` at −43.38227814 eV/cell. The completion reference persisted in the original chat.
- A second natural-language request with the same input and sandbox settings paused on that record before allocating another sandbox. The subsequent explicit selection reread the record and delivered the training export. The provider call counts and one-row database stayed stable.
- Four additional managed chat turns prepared an input, explained parameters, inspected an older OUTCAR and inspected an ABACUS input. They performed zero AIDB lookups or provider operations and the database stayed at one row.
- Chromium opened, collapsed and reopened Details from the completion row, fetching the same record twice. Two export clicks downloaded EXTXYZ with the bound record ID, eight atoms, per-atom forces and the original energy. The XML and OUTCAR downloads were byte-identical to the replayed outputs. Reload recovered the same completion and record.

Private logs and screenshots are under `.workspace/aidb-normal-chat-replay/sandbox-sbh9c1do/` in the integration worktree. The comparable Batch Job replay is in `batch-masou014/` alongside it, and the authorized real Batch Job launch is recorded in [ticket 06](ticket06.md). The sandbox and the planner were replayed locally.
