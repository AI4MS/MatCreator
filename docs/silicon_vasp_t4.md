# Silicon T4: verified reuse in a new MatCreator session

Scope: [Ai-ready_Database #5](https://github.com/Zikkying/Ai-ready_Database/issues/5),
main specification [#1](https://github.com/Zikkying/Ai-ready_Database/issues/1).
Development A remains active; final B belongs to T5. No new real computation
or cloud database upload was performed. Database access uses only the existing
Skill adapter/client/server and public query/export contracts.

## T3 prerequisite verified before implementation

Issue #4 is still open with no comments; that alone is not evidence of failure
or completion. A fresh public preflight/export read both real T3 records from
the default local database. The verifier reparsed retained raw VASP outputs,
checked durable collected-job/provider identities and submitted inputs, original
report and artifact hashes, archive receipts, parent linkage, structure, energy,
units, conditions and scientific completion. All checks passed. Evidence:
`.workspace/silicon-t4/t3-gate.json`, `gate-query.json`, `gate-export/`,
`verify-t3.py`. Original T2/T3 artifacts were retained without resubmission.

## Behavior

Preflight now exports and checks the exact current candidate set, then separates
candidate discovery from scientific applicability. Reuse requires ordered,
periodic diamond Si (space group 227), ordinary PBE without corrections,
successful electronic/ionic completion, final structure, finite eV/cell energy,
source identity, potential, cutoff and k-point evidence. Known input structure
and explicit INCAR/k-mesh constraints must agree. Retrieval defaults remain
visible and do not silently become reuse requirements.

The current task and all extracted conditions bind the lookup/selection. A
fresh turn queries again; a changed material, method, cutoff or target cannot
reuse an old choice. Preparation, tracked submission and Flash executor entry
return existing results without starting work when reuse succeeds. Explicit
recalculation still queries first; internal executors inherit the human's
recalculation intent and preparation-only limits. Negated recalculation is not
interpreted as permission to recompute.

Exact same-condition options are ordered by persistent timezone-aware archive
time. Missing/tied times or materially different complete conditions require
scientific clarification. Recency does not imply higher precision. Linked
relaxation/static records require the persisted parent and job relationship,
common potential/cutoff/spin and matching final structure. Both stages keep
their own settings and energies. Explicit static goals cannot be satisfied by
a relaxation energy. No general structural-equivalence or ranking system was
introduced.

## Real natural-language acceptance

A new process created a fresh SQLite ADK session
`silicon-t4-natural-final-20261007`, with no inherited conversation, receipt or
record ID in the user request. It ran the production MatCreator app/model in
Flash mode. Request:

> 请给我金刚石结构单质硅体相的普通 PBE 弛豫后结构和总能。如果已有兼容且成功的结果，请直接使用并说明结构、能量单位、来源和选择依据。本次仅获取已有结果，不提交任何新计算，也不上传云端数据库。

The agent naturally reached the registered hook, queried/exported the same
local database, selected both associated real T3 records and reported their
structure, separate energies, conditions, sources and selection basis:

| Stage | Record ID | Job ID | Energy (eV / 2-atom cell) |
| --- | --- | --- | ---: |
| Relaxation | `694772dca0777de504b25ba70366fc3d` | `56072cf15fe94e639d7b71f563ed320a` | -10.84124445 |
| Static | `1a1e16e62710d4bec6721ad350ee1f05` | `c266a691a9874ab9ad097bfd09dca9b6` | -10.84601452 |

The primitive cell has vectors (0, 2.73483634, 2.73483634) and permutations,
fractional Si positions (0,0,0), (1/4,1/4,1/4), volume 40.90948591 Å³.
The final agent response explicitly says selection does not establish highest
precision and no convergence comparison was performed. The initial acceptance
run had overstated static accuracy; shared agent guidance was corrected and
this second independent session verified the final feedback.

Evidence under `.workspace/silicon-t4-natural-final/`: `request.txt`, `run.py`,
`session.db`, `events.jsonl`, `runner.log`, `result.json` and fresh `.aidb/`
query/export/audit artifacts. The harness retained the production model and
hook, with an additional no-computation/no-archive guard for acceptance only.
No guard denial occurred. This is real persistent reuse of the already completed
Bohrium results, not a replay or another real calculation. Synthetic regression
fixtures below are separate evidence.

## Regression

28 new task-level tests pass using ADK, both registered agents, controlled
remote tools and real isolated aidb databases/public bridges. They cover reuse
without preparation/submission, explicit/negated recalculation, parent intent,
recent archive order, ambiguous order, different conditions, linked/unlinked
stages, explicit static targets, missing/invalid scientific evidence, query
failure, parameter/target changes and Flash entry. The original archive suite
retains failure, missing output, unconverged, duplicate and uncertain-write
recovery coverage; archive errors preserve outputs rather than resubmitting.
Related T1–T4 run: 102 passed before the last seven additional regression cases.
Changed modules compile. Full-suite results and independent review follow.

Full suite: **823 passed, 39 failed, 2 collection errors**. Failure/error names
exactly match the T3 baseline; no new failures. Machine-readable verification:
`.workspace/silicon-t4/acceptance.json` and `verify-acceptance.py` compare the
current public query, tool feedback and final answer against the verified T3
records, prove the request contains no IDs, and find zero jobs owned by the new
session. Full log: `.workspace/silicon-t4/full-final-alias.log`. No typechecking command is
configured in this repository; changed modules pass compileall and diff checks.


Final related preflight/reuse regression: **73 passed**, including all 28 reuse
cases. KPOINTS, KMESH and K-MESH matching syntax is covered by the same task
regression. The uppercase parser collision was reproduced, then fixed by
excluding the already recognized mesh span from INCAR constraints.

## Standards

Final review of `7ea0698...100b17a`: zero documented-standard violations and
zero unresolved judgment-call suggestions. The initial duplicated candidate
setup suggestion was resolved by `derive_candidate`; mesh aliases share one
recognizer and no per-alias special cases. Source/test layout follows AGENTS.

## Spec

Final review against #1 and the live #5 body: zero remaining actionable
findings. The initial P2 matching-KPOINTS rejection was fixed and checked for
all three supported spellings. All eight #5 acceptance criteria are supported
by the public-interface implementation, isolated regression and real independent
session evidence. The extra acceptance guard denied no calls and did not
manufacture reuse. Development A remains active; B has not been delivered here.

Review total: Standards 0 unresolved, Spec 0 unresolved; neither axis has an
outstanding issue. Current evidence summary is committed as
[silicon_vasp_t4_acceptance.json](silicon_vasp_t4_acceptance.json).
No issue comment or closure was performed in this implementation session.
