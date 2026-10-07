# Silicon local-loop checks

This is the repeatable validation entry point for MatCreator's silicon local loop. The current runtime policy is [final B / T5](../silicon_vasp_t5.md). [T4](../silicon_vasp_t4.md) records historical acceptance under development policy A; these checks do not change either policy.

## Isolated regression

From the repository root, with both Python environments installed:

```bash
.venv/bin/python script/check_silicon_loop.py tests --output .workspace/checks/loop.json
```

From the Windows PowerShell checkout on WSL:

```powershell
./script/check_silicon_loop.ps1 tests --output .workspace/checks/loop.json
```

Use a new output filename for every run. The command refuses an existing pytest report, writes JSON counts and exact failure identities, a log and JUnit XML, and fails on skipped tests or any failure. Missing aidb dependencies fail rather than quietly skipping. `AI_READY_DB_ROOT` and `AI_READY_DB_PYTHON` select the public bridge checkout and its Python; defaults use the adjacent `Ai-ready_Database` checkout and `.venv/bin/python`.

The `silicon-local-loop` job in [the workflow](../../.github/workflows/test.yml) pins the aidb commit, installs both packages, runs this same command against isolated test databases and retains its artifacts. The dependency must be available on GitHub before Actions can fetch it. The prepared version is `87148777fc9995a6d2e3bd85571ee8e20ff53181`, retained locally in `.workspace/aidb-ci`; it contains the public bridge and bounded preflight contract. A checkout of GitHub's older aidb main alone lacks that bridge. Publication is tracked separately from local validation.

## Full-suite failure baseline

```bash
.venv/bin/python script/check_silicon_loop.py tests --full \
  --baseline docs/testing/known-failures.json --output .workspace/checks/full.json
```

[known-failures.json](known-failures.json) records the exact failed test IDs and collection errors observed at T5's verified commit `5f88f95`. Comparison uses identity and outcome, not an old test count. New failures, skips, interrupted runs and inconsistent reports fail. Resolved baseline entries appear in the report. A run accepted against this baseline still reports `all_tests_passed: false` while failures remain; the isolated CI job never uses this baseline.

Each baseline entry carries reason, owner, tracking issue and status. Existing failures remain `needs-triage`, with owner `unassigned` and tracking issue `null` until someone takes responsibility. Assign the owner and link a diagnosis issue during triage; remove an entry only after its test passes. Do not add new entries just to make a check green. The two collection errors currently refer to obsolete `agents.MatCreator` imports. Fixing the existing unrelated failures is separate work.

## Read-only real reuse audit

```bash
.venv/bin/python script/check_silicon_loop.py verify-reuse \
  --workspace .workspace/silicon-t4-natural-final \
  --source-report .workspace/silicon-t2/silicon-t2-results/silicon-t3-final-report.json \
  --output .workspace/checks/reuse.json
```

Supply `--config` if the retained real records live in a nondefault aidb configuration. Both real source files and the existing tracked-job store must still be present. This command audits the retained independent MatCreator session; it does not start a new LLM session. It derives identities from the session's tool feedback and original request, performs fresh public query/export and reuse selection, compares the complete selected receipt, rereads raw VASP evidence and verifies artifact hashes and archive receipts. No record ID is supplied on the command line.

The audit writes reports, public exports and the actual final response into a unique subdirectory beside `--output`. It neither submits computations nor archives records nor uploads to a cloud database. Its job-store check verifies that the retained acceptance session owns zero tracked jobs; it is not a claim about unrelated sessions or all provider history.

Read the saved final response with [the scientific review standard](../../CODING_STANDARDS.md). The report deliberately marks `scientific_claim_review: required`; matching hashes and energy values cannot decide whether a prose claim of scientific accuracy is justified.

For the evidence chain, start with [T3 real archive verification](../silicon_vasp_t3.md), then [T4 independent discovery and reuse](../silicon_vasp_t4.md), and [T5 policy switch](../silicon_vasp_t5.md). The runtime interface and configuration remain documented in [aidb_skill_hook.md](../aidb_skill_hook.md).
## Implementation verification, 2026-10-08

Validated against a clean checkout of the pinned dependency, rather than its developer working tree:

- Isolated loop: 150 passed, no failures, errors or skips (`.workspace/retro-checks/loop-v4.json`).
- Full suite: 864 passed, 39 failed, two collection errors, no skips. Exact failures match the recorded baseline; the suite is still not clean (`full.json`).
- Public bridge and normalization identity contract: 15 passed in the prepared aidb checkout. The pin includes bounded preflight support and the missing Hubbard-U identity argument; no unrelated developer changes were copied.
- Fresh readback of both retained real records passed (`reuse-final.json`), with zero tracked jobs in the independent session. A changed structure was rejected; a missing store was rejected without creating it.
- PowerShell wrapper, workflow YAML loading, Python compilation and authored-file whitespace checks passed.

Reports are local under `.workspace/retro-checks/`; original T3/T4 evidence is retained. GitHub Actions has not run this new job: the prepared dependency commit must first be published. Local validation does not establish remote CI success.
