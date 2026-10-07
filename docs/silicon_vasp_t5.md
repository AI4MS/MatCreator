# Silicon T5: final B after verified T4

Scope: [Ai-ready_Database #6](https://github.com/Zikkying/Ai-ready_Database/issues/6),
main specification [#1](https://github.com/Zikkying/Ai-ready_Database/issues/1).
The implementation lives in MatCreator. The default is now final B; no A
configuration switch remains. Historical T1–T4 evidence stays unchanged.

## Prerequisite and switch

Starting point: `df6116dcb9886e85f287702812bf44be80bcfe10`.
Before changing behavior, checked its `docs/silicon_vasp_t4.md` and committed
`docs/silicon_vasp_t4_acceptance.json` against the retained acceptance JSON.
Re-ran `.workspace/silicon-t4/verify-acceptance.py` and `verify-t3.py`.
Both passed: the independent natural-language session had no supplied record
IDs, found both real T3 records, and owned zero new jobs. Fresh public
query/export, reparsed raw VASP outputs and artifact hashes again verified:

| Stage | Record ID | Job ID | Energy (eV/cell) |
| --- | --- | --- | ---: |
| Relaxation | `694772dca0777de504b25ba70366fc3d` | `56072cf15fe94e639d7b71f563ed320a` | -10.84124445 |
| Static | `1a1e16e62710d4bec6721ad350ee1f05` | `c266a691a9874ab9ad097bfd09dca9b6` | -10.84601452 |

T4's two-axis review had zero unresolved findings; its 39 test failures and
two collection errors matched the pre-existing T3 baseline. Issue status alone
was not used to decide completion. No real calculation was resubmitted and
no cloud database was accessed for this implementation.

## Current behavior

Lookup failure explains that data existence is unknown and continuing may
duplicate computation. Preparation/submission pauses until the human responds
with the displayed `确认继续 <failure_id>` (English `confirm continue <failure_id>`
also works). The ordinary-language reply contains no internal query JSON.
The event ID makes consent unambiguous; plain “continue”, “yes”, negated replies
and internal executor text do not grant it. The session must contain the actual
warning response from an earlier invocation, and the saved task must still
match, including known conditions and the digest of an explicitly supplied file.
New targets, methods, cutoff, k mesh or failure events invalidate old consent.

Confirmation resumes the original scope without querying again for the same
failed event. Preparation-only remains preparation-only. Registered Flash/Plan
step runners may delegate the current invocation's grant to a fresh isolated
executor session; changed step conditions revoke that grant. A copied previous
session receipt cannot grant consent in a new session.

The lookup stays `failed`, `query_executed=false`, with failure ID and diagnostics;
the feedback and collected result retain `loop_complete=false`. Scientific
completion still triggers the existing local archive-and-requery/readback.
Archive failure is reported with retained artifacts; recovery can save and
compare the local record without resubmission. Even successful local saving
after bypass is not a successful complete lookup/compute/archive loop.
Material clarification and context extraction errors remain blocking.

## Verification

The first red task test failed because A lacked a confirmation event. After
the switch, 23 task cases passed using ADK, real isolated aidb configuration,
SQLite and public bridge calls, and controlled calculation tools. They cover
confirmation, refusal/absence, target/condition/event/session changes, original
preparation-only scope, internal text, both agent entries, actual Flash-to-step
delegation, archive failure and recovered local readback. The controlled
provider's synthetic VASP outputs are orchestration evidence only. The real
calculation/reuse prerequisite remains the separate T3/T4 evidence above.

Commands:

```bash
.venv/bin/python -m pytest tests/test_silicon_lookup_consent.py -q
.venv/bin/python -m pytest tests/ -q --continue-on-collection-errors
```

Logs are retained in `.workspace/silicon-t5-{red,current,related,full}.log`.
The full-suite comparison and final independent review are recorded below
after completion. There is no configured typechecking command; changed modules
are checked with compileall. Diff checks cover only this change, preserving
the user's unrelated AGENTS.md and untracked files.
