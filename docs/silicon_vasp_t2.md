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

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_silicon_vasp_workflow.py tests/test_aidb_skill_hook.py tests/test_remote_job_tools.py -q
```

The new tests use a controlled provider and explicitly synthetic VASP XML,
parsed by the real pymatgen parser. Agent-runner cases use a real isolated aidb
public bridge/database for query ordering and user-visible receipts. These
tests prove orchestration and scientific failure handling, not real computation.

On 2026-10-07 a real natural request was dispatched to MatCreator with session
`silicon-t2-20261007`, using the existing user configuration and local aidb.
The real preflight succeeded with no Si/PBE/bulk candidates (no records deleted).
Private run evidence is retained under `.workspace/silicon-t2/`, including
`events.jsonl`, `trajectories/`, `.aidb/runs/` reports and audits. Real Bohrium
completion is still pending; no successful job ID or real T3 handoff is claimed.

The corrected launch session `silicon-t2-20261007-env2` also queried the real
local database successfully (empty candidates) and generated diamond Si2 using
the existing ASE skill. MatCreator stopped before submission after an actual
MPRelaxSet POTCAR check raised `PmgVaspPspDirError`: no licensed PBE potential
directory is configured. `compute.vasp_image` / `BOHRIUM_VASP_IMAGE` is also
empty. Bohrium CLI authentication and machine discovery succeeded. The specific
diagnostic is `.workspace/silicon-t2/preflight_env_report_step2.json`; no tracked
job was submitted and no successful real calculation is claimed.

Automated validation: the initial targeted run passed 117 tests; new workflow
tests also passed in both production agent entry points. Full-suite execution
passed 756 tests, with 39 failures and two collection errors. A snapshot of
the starting commit reproduced exactly the same failure names (no new failed
tests). The baseline snapshot skipped aidb integration because it was outside
the adjacent-checkout layout. Final review added regression coverage for mutable
POTCAR inputs, dispersion corrections, and submission replay provenance.

The initial noninteractive launcher omitted the NVM bohr path; the installed
CLI is `/home/shik-mechrevo-wsl/.nvm/versions/node/v24.14.1/bin/bohr` (2.6.100).
Future launches should prepend both the repository `.venv/bin` and this Node
directory to PATH, so `run_python` and bohr use the intended environment.
