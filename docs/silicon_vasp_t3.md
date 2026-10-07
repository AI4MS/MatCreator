# Silicon VASP T3: automatic local archive and content verification

Scope: [Ai-ready_Database #4](https://github.com/Zikkying/Ai-ready_Database/issues/4),
using [#1](https://github.com/Zikkying/Ai-ready_Database/issues/1) as the main spec.
Development A remains active. Natural candidate reuse (T4) and final B are
separate work. This implementation submits no calculation and uploads nothing
to a cloud database. No database internals are imported by MatCreator.

## T2 gate and workflow

Before implementation, both existing T2 jobs were verified against the durable
job store, immutable submitted-input checksums, complete original VASP outputs
and the T2 final report. Electronic/ionic completion and the static-to-relaxed
structure/potential link passed. Original report/output checksums still match
after T3. Issue #3 remains open; its real successful artifacts, rather than its
issue state alone, establish this prerequisite. See [T2 evidence](silicon_vasp_t2.md).

The registered main-agent and step-executor after-tool callback automatically
archives successful `collect_silicon_vasp_result` responses. The public
`archive_silicon_vasp_result` tool retries storage for an existing job. Both
revalidate raw outputs and tracked provenance, then use the existing aidb Skill
adapter, client/server, query, archive-and-requery and JSONL export interfaces.
Failed/running/unknown/unconverged calculations retain outputs and do not write
successful records. Scientific success and storage verification have separate
receipts; `silicon-result.json` remains the unchanged scientific handoff.

Records contain final structure, cell total energy (eV), actual INCAR and
effective conditions, successful completion evidence, tracked/provider IDs,
session, submission settings, source paths and hashes. Additional properties
are not invented. Static metadata links the verified parent record and its
original relaxation job; the two energies remain separate.

Each attempt queries and exports first. Matching job identities are compared
before reuse; differing content is reported without overwriting. A new result
uses archive-and-requery, verifies the returned ID and compares exported
structure, energy/unit, conditions, completion and provenance. Per-job file
locks serialize duplicate notifications. A timed-out write stays unverified;
the next attempt queries durable state before another write. Adapter failures,
receipts and exported content remain under `.aidb/archives/<job_id>/`. Only a
verified readback is reported as archived. A UTC `archived_at` value persists in
record metadata and is preserved on duplicate processing.

## Real acceptance, 2026-10-07

MatCreator resumed the original session `silicon-t2-20261007-env2` and called
the public archive tool for each original collected job, relaxation first.
Its tool events, graph/trajectories and final report remain in the original
workspace. Both real archive receipts passed ID requery and content comparison.

| Step | Tracked job ID | Local record ID | Cell energy (eV) | Archive time (UTC) |
| --- | --- | --- | ---: | --- |
| Relaxation | `56072cf15fe94e639d7b71f563ed320a` | `694772dca0777de504b25ba70366fc3d` | -10.84124445 | 2026-10-07 13:30:20.838212 |
| Static | `c266a691a9874ab9ad097bfd09dca9b6` | `1a1e16e62710d4bec6721ad350ee1f05` | -10.84601452 | 2026-10-07 13:30:46.863940 |

A separate process subsequently queried/exported these same records through
the public adapter. Its independent checks matched CONTCAR, INCAR, the known
T2 energy values and units, completion, parent record ID and every retained
output hash. This proves persistent round trips, not T4 natural-language reuse.

Evidence paths relative to `.workspace/silicon-t2/`:

- `t3-t2-verification.json`: prerequisite reparse and hash verification.
- `silicon-t2-results/silicon-t3-final-report.json`: MatCreator's real report,
  with both archive IDs and complete receipt/query/export/audit paths.
- `events-pbe.jsonl`, `t3-runner.log`, `trajectories/`: original-session execution.
- `t3-independent-query.json`, `t3-independent-export/`,
  `t3-independent-verification.json`: independent persistent readback.

## Regression and limitations

Nine new full-tool/callback tests use synthetic completed VASP outputs and real
isolated aidb databases/public bridges. They cover both registered ADK agents,
automatic saving, duplicates, static parent links, invalid scientific outputs,
lookup failure/recovery, uncertain write recovery and changed readback units.
They are regression evidence, not real calculation evidence.

Related regression: **356 passed**. Full suite: **795 passed, 39 failed,
2 collection errors**; the exact failure/error names match the T2 baseline
(`debug-pbe/final-full-fixed.log`). Logs: `t3-targeted-tests.log` and
`t3-full-tests.log`. Changed Python modules compile successfully. No repository
typechecking command is configured. No aidb code/schema change was needed:
its public record metadata/export contract retained the required content.

One regression corruption call initially omitted its isolated config and wrote
synthetic record `e506b6d0b06ebb6b3a866195dec9a4b8` to the default local database.
The test now supplies the config explicitly. That exact record was corrected
through public archive-local to `rejected_records`, `status=invalid`,
`test_only=true`, `real_computation=false`. It was not counted as real evidence;
no user records were deleted. Recovery payload: `t3-accidental-fixture.json`.

Standards/Spec review results are recorded after implementation review. #4 and
#3 have not been closed or commented on by this session.
