# Silicon VASP T2: tracked collection and scientific completion

Scope: [Ai-ready_Database #3](https://github.com/Zikkying/Ai-ready_Database/issues/3),
with [#1](https://github.com/Zikkying/Ai-ready_Database/issues/1) as the main spec.
T1 (#2) is closed. Development A remains in force: a failed aidb lookup pauses
new preparation and submission. T2 does not implement local archive, reuse or B.

MatCreator owns the calculation. The existing atomic-structure / vasp-pymatgen
skills choose and generate the structure and pymatgen input sets. Bohrium
discovery, tracked submission, monitoring, reconnect and deduplicated collection
continue to use the existing remote-job tools. No new submission platform or
VASP parameter framework is introduced.

For fixed diamond bulk Si / ordinary PBE, the VASP skill directs both agents to
`collect_silicon_vasp_result`. The tool is registered on the thinking agent and
the isolated step executor. It checks session ownership, respects user controls,
and collects only a platform-successful Batch Job. Running, failed, unknown and
unsupported jobs return a needs-replanning receipt without submitting anything.
Already-collected jobs reuse their durable artifact paths after reconnect.

The parser requires complete XML, original and retained INCAR/POSCAR, CONTCAR,
OUTCAR, OSZICAR and original POTCAR provenance. Tracked Batch Job submission
captures SHA-256 checksums for materialized VASP inputs in its durable
specification before invoking the provider. Collection verifies local/retained
inputs against that snapshot, and matches submitted POTCAR TITEL metadata with
the actual XML potential identity. Old jobs without submission evidence remain
unverified. Replaying a submit does not overwrite existing provenance.
It checks electronic convergence,
ionic convergence and the OUTCAR accuracy/termination evidence for relaxation,
separate static-step settings, PBE pseudopotential/method evidence, diamond Si
symmetry, finite energy, and agreement between XML and structure files. These
are fixed-example checks, not a general scientific compatibility scorer.

Static total energy additionally requires the existing relaxation job ID in the
same session. The source output is parsed again; cached success JSON alone does
not establish success. The static input must match that relaxed structure and
use the same pseudopotentials. Changed, incomplete or unverified sources are
rejected. The two steps retain their separate identities and energies.

`silicon-result.json` retains success/invalid status, tracked/provider identities,
actual structure, energy in eV, INCAR and effective parameters, pseudopotential
identity/hash, available KPOINTS, submission image/machine/settings, raw artifact
paths/checksums and relaxation source. Original outputs are preserved on failure.
`archived` is always false. T3 must only consume verified successful handoffs;
T2 never claims a record was saved or a local loop was completed.

## Validation and real-run evidence

Final related regression run: **286 passed**, covering ADK preflight callbacks,
workspace subprocesses, scientific collection, tracked jobs and the Bohrium
Batch Job adapter. Synthetic XML tests use the actual pymatgen parser; agent
cases use a real isolated aidb bridge/database. They are not real calculations.

The final full suite had **786 passed, 39 failed and 2 collection errors**.
Failure names exactly match the initial baseline snapshot. Logs are retained in
`.workspace/silicon-t2/debug-pbe/final-full-fixed.log` and
`.workspace/silicon-t2/baseline-tests.log`. Unrelated legacy failures were not
changed. Standards and Spec reviews found zero new implementation findings.

The real run remains in session `silicon-t2-20261007-env2`. A real preflight
succeeded with empty Si/PBE/bulk candidates; no database records were deleted.
The user-supplied potential and the account-private image resolved the original
resource/configuration blockers. MatCreator owns input generation and submission;
Codex only diagnosed the runtime, repaired platform validation and resumed the
existing session. See [runtime diagnosis](vasp_runtime_diagnosis.md) and
[PBE research](pbe_bohrium_research.md).

Verified real relaxation:

- Durable job ID: `56072cf15fe94e639d7b71f563ed320a`.
- Provider batchjob ID: `5ed5945230884cf88f30475c1afed598`.
- VASP image: `registry.dp.tech/dptech/prod-28882/vasp:6.3.0`.
- POTCAR: `PAW_PBE Si 05Jan2001`, SHA256
  `52dbe99da884e0191b2d348dfcc281aaba36e2eacd3019a49d93cf07d84da86b`.
- Electronic and ionic convergence, three ionic steps, completed OUTCAR.
- Total energy: **-10.84124445 eV per Si2 cell**.
- Verified report: `silicon-t2-results/relax-collected/silicon-result.json`.

All paths above are relative to `.workspace/silicon-t2/`. Actual source outputs,
submission input hashes, query reports/audits, `events-pbe.jsonl` and trajectories
are retained. The relaxation was never resubmitted. MatCreator completed the
static continuation using the verified CONTCAR and the same potential.
No archive or full local-loop completion is claimed. Issue #3 remains open;
both steps now have real scientific completion evidence.

Verified real static continuation:

- Durable job ID: `c266a691a9874ab9ad097bfd09dca9b6`.
- Provider batchjob ID: `6ce96bb830764a01976fec7ef9d16a99`.
- Total energy: **-10.84601452 eV per Si2 cell**.
- Electronic convergence, one static step and completed OUTCAR.
- `NSW=0`; effective `IBRION=-1`; actual ENCUT 520 eV.
- Static input matches the verified relaxed structure; unchanged POTCAR SHA256.
- Verified report: `silicon-t2-results/static-collected/silicon-result.json`,
  with the relaxation IDs and energy retained in `relaxation_source`.

Both authorized steps now have real scientific completion evidence. No third
job, probe computation, archive or cloud database upload was performed. The
first collection round produced the successful report but ended with ADK
`StaleSessionError`; a subsequent child collection reported a workspace-path
error. A public-tool replay in the original workspace returned success from
both original VASP output sets without altering inputs or outputs. These
runner incidents and recovery logs remain in `debug-pbe/`; this does not claim
a general fix for ADK concurrent session updates.

T2's real-computation acceptance is evidenced. T3 archive/requery and the final
A-to-B switch in the main specification remain separate work. Issue #3 was not
closed by this session.

MatCreator's final report is
`.workspace/silicon-t2/silicon-t2-results/silicon-t2-final-report.json`.
Its listed output and scientific-report checksums match the retained files.
