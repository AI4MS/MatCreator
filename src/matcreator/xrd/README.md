# MC XRD integration

The OAW XRD implementation is vendored from `theAfish/open-agent-world` at
`eb31455`; original source hashes and checkout identity are in `SOURCE.json`.
The scientific engine license is retained in `vendor/OAW_XRDfit`.

## Entry points

- MC tab bar **+ → XRD 工作台**. Workspaces and run history persist independently
  of chat sessions. **参考输入** imports reference peak tables and CIF associations.
- The spectrum and structure knobs open one draggable, resizable floating area.
  Structure views mount MC's existing `StructureViewer.svelte` in read-only XRD
  mode. The original OAW spectrum, frame cursor and workflow views are retained.
- MC thinking and execution agents have `xrd_list_workspaces`, `xrd_get_results`
  and `xrd_action`. These are native ADK FunctionTools backed by the same local
  Web service as the UI, not a standalone MCP server. Read the bundled XRD skill
  for request IDs, candidate decision tokens and scientific claim boundaries.
- Result discussion prepares a prompt with the workspace ID in Chat; the user
  sends it. Merely opening XRD does not invoke an LLM or start an analysis.

## Runtime and persistence

`MATCREATOR_XRD_PYTHON` selects a Python with numpy/scipy/pymatgen/ASE and the
OAW engine dependencies. `OAW_XRD_ROOT` identifies the existing scientific
runtime (including optional QualX). The local review launcher uses the existing
XRD environment and starts all three MC services with hidden windows.

Data live under `MATCREATOR_XRD_HOME`, defaulting to `MATCREATOR_HOME/xrd`.
Each workspace has revisioned documents and a SQLite journal. Worker input
snapshots, hashes, logs and terminal status live under `runs`. Single-stage
workers are supervised outside the Web request and completed detached workers
are reconciled on reconnect. Unknown live ownership blocks duplicate starts.
Multiphase interruptions retain history and are reported as interrupted; they
are not silently replayed. Local mode only; server mode rejects these routes.

## Acceptance on 2026-09-30

- Existing frontend suite: 193 passing; production Vite build passed.
- XRD service / frames / refinement / decision tests: 43 passing.
- Multiphase numerical tests: 7 passing.
- Real local synthetic Si API workflow completed search, preoptimization and
  full-profile fit. This used one iteration to exercise transport and engine
  execution; fit convergence was false. This is software acceptance, not an
  experimental phase assignment or a scientifically converged refinement.
- Native Agent tool reads, source download and run ZIP download passed.
- Browser checked shared crystal rendering, spectrum, floating-window zoom,
  manual input list, and both MC skins with both light/dark variants.
- Complete LLM-driven multiphase optimization was not run in this acceptance.
  The harness and decision/numerical tests are retained; native tools expose it.

Reproducible synthetic smoke: `script/xrd_local_smoke.py` (creates a clearly
labelled new test workspace; run with the scientific interpreter). Local
receipts and screenshots are under `.local-review-logs/`.

## MC TypeSafe / Jev controller

The XRD header's **Jev** button configures the model ID (default `jev-1.13.0`), HTTPS base URL and credential. A saved API key is stored in the local MC XRD home `jev-config.json`; the read endpoint returns only `configured`, never the key. Alternatively set `TYPESAFE_API_KEY` (or the configured environment-variable name) before starting Web/API. This is a TypeSafe `/v1/systemone` connection, not an OpenAI chat endpoint.

Select **TypeSafe / Jev** in the multiphase run settings, then start. The Web/API process owns the bounded controller. It calls the existing workspace-scoped decision/evaluate/baseline/finish operations, validates the full ADD/SUBMIT construction, preserves persistent cloud-request reservations, and never retries or silently substitutes another optimizer after an API failure. Stop cancels the controller and numerical worker. Interrupted runs are retained for inspection and are not automatically restarted.

Only compact candidate/peak evidence and measured trial summaries are sent to Jev; BO results are withheld during selection. Model responses, probabilities, usage, latency and construction tokens are recorded in `<workspace>/<run_id>.jev.jsonl`; accepted construction paths also remain in the numerical harness records. Review recommendations do not start expensive joint refinement automatically.

Port provenance is in `JEV_SOURCE.json`. Tests cover mocked HTTP protocol, workspace scope, cancellation, duplicate start prevention and redacted configuration. Real cloud acceptance requires a valid credential; mocked responses are not scientific or provider-availability validation.
