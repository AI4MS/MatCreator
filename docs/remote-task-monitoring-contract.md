# Remote task presentation and monitoring contract

Status: design contract for the PR #251 preview integration.

This contract separates three facts that must never be collapsed into one
status:

1. `remote_job.status` is the provider resource lifecycle.
2. `task_run.status` is the command/workload lifecycle inside that resource.
3. `task_phase` is a workload-specific presentation phase.

A reachable Sandbox does not prove that an MD, VASP, training, or analysis
command started. A finished command does not prove that outputs were
collected or validated.

## What is unified

Workloads do not need one physical input-file format. MD, VASP, model
training, and generic analysis keep their native files. They share a
versioned envelope and event protocol:

```json
{
  "schema_version": "mc.remote-task.v1",
  "task_run_id": "stable-logical-task-identity",
  "attempt": 1,
  "workload_profile": "md",
  "phase_plan": [
    {
      "phase": "preparation",
      "label": "Prepare MD structure, model, and run controls",
      "progress_applicable": false
    },
    {
      "phase": "execution",
      "label": "Run molecular dynamics",
      "progress_applicable": true
    }
  ],
  "inputs": [
    {
      "role": "structure",
      "remote_path": "/workspace/task/structure.extxyz",
      "size": 1234,
      "sha256": "...",
      "required": true
    }
  ],
  "contract_digest": "sha256:<canonical-envelope-digest>"
}
```

The contract above is the public, deterministic submission envelope. Runtime
monitor paths and process handles live separately in the durable job snapshot:
`/tmp/matcreator-monitor-<job_id>.events.jsonl`,
`/tmp/matcreator-monitor-<job_id>.state`, and
`/tmp/matcreator-cmd-<job_id>.exit`.

Provider credentials, arbitrary environment values, and raw command text are
private submission data and must not be exposed through the browser-facing
configuration projection.

## Stable identity and replay

One logical attempt is tracked by all available identities:

- stable task/run name;
- MC `job_id`;
- provider Sandbox or batch ID;
- an internal, scoped provider association ID derived from the MC owner,
  session, node, template, attempt, and stable task name;
- request/submission ID when available;
- immutable input/manifest digest;
- attempt number.

The friendly stable name is not assumed to be globally unique. For Bohrium
Sandbox creation, MC hashes the owning scope into an opaque
`provider_session_id` and passes that value as `--session-id`. The mapping is
persisted for recovery but excluded from the browser projection; the UI keeps
showing the friendly stable name.

The Sandbox-side monitor emits a monotonically increasing `source_seq` under
one `source_instance_id`. The local store accepts an event once per
`(job_id, source_instance_id, source_seq)` and keeps a replay cursor. Browser,
Web process, or network reconnection therefore resumes from the same job and
must not resubmit it.

## Event protocol

The remote monitor is an observer. It may record process identity, heartbeat,
stage, progress evidence, log size, and exit markers. It must not collect,
validate, terminate, or decide scientific success.

Each event has the following public shape:

```json
{
  "schema_version": "mc.task-event.v1",
  "source_instance_id": "...",
  "source_seq": 12,
  "observed_at": 1787856000.0,
  "event_type": "heartbeat",
  "task_status": "executing",
  "phase": "execute",
  "process_active": true,
  "exit_code": null,
  "log_bytes": 8192,
  "progress": null
}
```

The remote journal is append-only. The latest state file is written through a
temporary file plus atomic rename. If the local monitor is offline, remote
events continue to accumulate. Once connectivity returns, the local monitor
pulls only events after its durable cursor and appends replayable local
`task_monitor_event` records.

The POSIX state record always contains six non-empty tab-delimited fields.
An inactive process uses the non-numeric `-` sentinel rather than an empty PID,
because POSIX `read` collapses adjacent tab whitespace. State construction
uses one direct `printf`; paths are never interpolated into a `sed`
replacement expression.

## Presentation profiles

Presentation phases are orthogonal to provider status and do not add states
to the canonical Remote Job state machine.

| Profile | Ordered phases | Progress phase |
| --- | --- | --- |
| `md` | Preparation, Provisioning, Input staging, Execution, Validation, Collection | Execution / Simulate |
| `vasp` | Preparation, Provisioning, Input staging, Execution, Validation, Collection | Execution / Solve |
| `training` | Preparation, Provisioning, Input staging, Execution, Validation, Collection | Execution / Train |
| `generic` | Preparation, Provisioning, Input staging, Execution, Validation, Collection | Execution |

MD preparation may include input validation, resource/environment setup,
file staging and verification, and a bounded preflight/smoke check. Optional
equilibration is execution-domain metadata, not an invented provider state.

Only the declared execution phase may render a progress bar. A determinate
bar requires an explicit reliable metric such as MD step/target step or
training epoch/target epoch. All other phases show a stage label and recent
evidence; they never manufacture a percentage. `collected` means collection
finished, not validation passed.

## Ownership

Exactly one component owns finalization. The Sandbox-side monitor records
facts. The local monitor reconciles and syncs. A separately configured
finalizer may collect, validate, and close after its gates pass. The browser
never becomes a finalization owner.

## Minimal end-to-end acceptance

The integration probe uses one short-lived, non-GPU Sandbox and no scientific
workload. It must demonstrate:

1. one durable MC job and one provider Sandbox identity;
2. one small input with size and digest evidence;
3. a detached probe command plus Sandbox-side monitor events;
4. local disconnect/reconnect replay without duplicate submission;
5. browser presentation of the recovered phase and execution-only progress;
6. exact-resource cleanup and a structured provider terminal/absence result.

Formal MD, VASP, training, and Bohrium batch execution are outside this probe.
