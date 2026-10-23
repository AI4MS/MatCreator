# MC XRD integration

The OAW XRD implementation is vendored from `theAfish/open-agent-world` at
`eb31455`; original source hashes and checkout identity are in `SOURCE.json`.
The scientific engine license is retained in `vendor/OAW_XRDfit`.

## Entry points

- MC tab bar **+ → XRD 工作台** creates an independent tab and workspace, including
  when another XRD tab is open. Workspaces and history persist independently of
  chat sessions. Import an experimental spectrum from the input card or canvas;
  mount a local reference database from the input section.
- A draggable edge button opens one movable, resizable floating area with the
  spectrum above the crystal structure. Both follow the selected result.
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

## Verification

Run `pytest tests/test_xrd_*.py` with MC's Python and
`MC_TEST_XRD_PYTHON` pointing to the scientific interpreter. The tests cover
immutable snapshots, numerical recovery on synthetic mixtures, bounded Jev
decisions, cancellation, HTTP argument contracts and Windows atomic-save locks.
Run `npm test` and `npm run build` from `web/vite-frontend` for UI checks.

`script/xrd_local_smoke.py` creates a clearly labelled synthetic Si workspace
and exercises search, preoptimization and profile fitting through the local
API. It requires the scientific interpreter. Set `MC_XRD_API_URL` to the
running API base URL; the default is `http://127.0.0.1:8001/api/xrd`.

The local browser acceptance on 2026-10-15 completed a real Jev search over
27 parseable COD candidates, with 24 distinct evaluated combinations, followed
by joint refinement of the highest search-score combination. The review
completed 20 iterations with `converged=false`. This validates the software
path, not phase identity, purity, optimizer superiority or mass fractions.
Experimental input, COD databases, API credentials and local audit logs are
not distributed in this repository.

QualX is optional and must be installed separately under `OAW_XRD_ROOT` for
QualX database searches. The native peak matcher works with imported reference
tables. The pinned scientific requirements are recorded in
`vendor/OAW_XRDfit/requirements.txt`; use a separate compatible Python
environment rather than replacing MC's runtime dependencies.

## MC TypeSafe / Jev controller

The XRD header's **Jev** button configures the model ID (default `jev-1.13.0`), HTTPS base URL and credential. A saved API key is stored in the local MC XRD home `jev-config.json`; the read endpoint returns only `configured`, never the key. Alternatively set `TYPESAFE_API_KEY` (or the configured environment-variable name) before starting Web/API. This is a TypeSafe `/v1/systemone` connection, not an OpenAI chat endpoint.

Select **TypeSafe / Jev** in the multiphase run settings, then start. The Web/API process owns the bounded controller. It calls the existing workspace-scoped decision/evaluate/baseline/finish operations, validates the full ADD/SUBMIT construction, preserves persistent cloud-request reservations, and never retries or silently substitutes another optimizer after an API failure. Stop cancels the controller and numerical worker. Interrupted runs are retained for inspection and are not automatically restarted.

Only compact candidate/peak evidence and measured trial summaries are sent to Jev; BO results are withheld during selection. Model responses, probabilities, usage, latency and construction tokens are recorded in `<workspace>/<run_id>.jev.jsonl`; accepted construction paths also remain in the numerical harness records. Review recommendations do not start expensive joint refinement automatically.

Port provenance is in `JEV_SOURCE.json`. Tests cover mocked HTTP protocol, workspace scope, cancellation, duplicate start prevention and redacted configuration. Real cloud acceptance requires a valid credential; mocked responses are not scientific or provider-availability validation.
