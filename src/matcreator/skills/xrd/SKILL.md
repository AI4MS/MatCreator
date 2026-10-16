---
name: xrd
description: "Operate the local MC XRD workbench through its native Agent tools: import inputs, search reference libraries, refine candidate structures, construct multiphase combinations, and read audited results."
---

# XRD workbench

Use `xrd_list_workspaces`, `xrd_get_results`, and `xrd_action`. These use the same authoritative MC Web service as the XRD UI. Do not execute an independent copy of the engine or edit workspace JSON/SQLite directly.

1. Read the workspace ID supplied by the user, or list workspaces and identify the relevant one by evidence. Read existing state first.
2. Follow MC's current mode and approval rules. Reading results is not authorization to start a new analysis. In Standard mode include analysis execution in the approved plan.
3. For a single-phase workflow use `start` with one stable `request_id` and a `config` object. Reuse the same request ID after a timeout. Use `workflow_stage` search, then preopt, then fit. For later stages provide the verified `workflow_match_run_id`, selected candidate IDs, and (for fit) `workflow_preopt_run_id`.
4. Poll `xrd_get_results`. If running/waiting, reattach; do not launch a replacement. The service stores worker heartbeats, immutable input snapshots, run IDs, source hashes, stage status, frame histories and SQLite journal entries.
5. Multiphase: `multiphase_start` prepares a bounded harness. For each construction request `multiphase_decision` with `draft_candidate_ids`; choose only the returned legal ADD/SUBMIT actions. Preserve each decision token and construction path. Use `multiphase_evaluate` with `candidate_ids`, `reason`, `selection_token`, `construction_path`. Do not invent metrics or bypass duplicate/pool checks. Exhaust the configured Agent budget, then call `multiphase_baseline`, then `multiphase_finish`. Independent baseline evidence must not guide prior Agent choices. `multiphase_review` takes `run_id` and distinct measured `combinations`.
6. Read `history_list`, `history_inspect` and `history_records` through xrd_action for lineage. Keep execution status, input integrity, convergence/scientific acceptance and claim scope separate. Candidate matches are hypotheses; profile contributions are not mass fractions. A lower Rwp does not establish phase identity. Retain failed candidates, stopping reasons and fallback structures.

Input preparation uses `ensure_input` (kind pattern/reference/cif/library), followed by `document` with action import/configure/associate, arguments, expected revision and target ID. An import requires filename and base64-encoded content; a library mount requires its existing local .sq path and slot. Never upload local inputs to an external service. COD fetches only public candidate IDs.
