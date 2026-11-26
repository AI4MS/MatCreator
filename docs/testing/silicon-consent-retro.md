# T5 consent retrospective

This supplements [the T4 retrospective tooling](silicon-local-loop.md) for
[Ai-ready_Database #6](https://github.com/Zikkying/Ai-ready_Database/issues/6),
under [the main specification](https://github.com/Zikkying/Ai-ready_Database/issues/1).

The initial implementation tested `继续，ENCUT=600`, but missed standalone
`ENCUT=600`: task classification returned no calculation task and retained the
old failure receipt. A later exact confirmation resumed the old authorization.
An intervening user update without tool calls exposed the same lifecycle gap.
The independent Spec review reproduced this at the ADK task boundary;
`4dc04d3` fixed both paths. Confirmation now checks the preceding human
invocation, the exact failure event, session and complete task conditions.

## Deterministic regression evidence

On 2026-10-08, the same six public task-boundary tests were run in independent
pytest processes. One process loaded the historical `587becd` hook from Git;
the other used the current hook. The checkout and shared branch were unchanged.

| Cases | Historical hook | Current hook |
| --- | --- | --- |
| Standalone ENCUT, KPOINTS and HSE06 updates, followed by old confirmation | 3 failures | 3 passes |
| Condition update with no tool calls, followed by old confirmation | 1 failure | 1 pass |
| Current valid confirmation and original preparation-only authorization | 2 passes | 2 passes |
| Errors / skipped tests | 0 / 0 | 0 / 0 |

The four historical failure IDs exactly match the negative cases. Positive
controls show the guard still allows valid confirmation and respects original
preparation-only scope. Tests observe controlled preparation/submission tools
and actual ADK feedback, rather than private guard calls or prompt strings.
The public aidb bridge intentionally reports a missing isolated config to
produce lookup failure; `_query` is not replaced with a mock. No real
calculation or cloud upload was performed.

Exact counts and failure identities are in
[silicon-consent-retro-proof.json](silicon-consent-retro-proof.json).
The temporary replay driver and detailed logs remain under
`.workspace/silicon-t5-retro-20261008/`. This historical replay establishes
regression sensitivity; it is not another permanent CI harness.

## Deduplication with T4 retro

The existing `script/check_silicon_loop.py tests` command and the
`silicon-local-loop` CI job already run all 29 consent cases, require the
public integration dependency, reject unexpected skips/failures and retain
reports. Reuse them; no second CI job, dependency branch, baseline file or
global steering paragraph is needed for T5. The T4 retro also owns scientific
claim review, navigation and the PowerShell/WSL entry point.

For future authorization changes, keep standalone condition updates and human
turns without callbacks in the task suite. Permission lifetime crosses language
classification, session history and executor delegation; a syntax linter alone
cannot establish it. Retain the independent Spec review that detected the gap.

Publication/local validation of the pinned aidb dependency is separate from
successful remote CI. The combined delivery PR reports the actual remote
checks separately from these historical/local results.
