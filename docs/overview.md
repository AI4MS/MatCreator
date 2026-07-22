# Overview

MatCreator is organized as an agent harness around the Google ADK runtime, a skill library, a workspace, and a persistent knowledge base. The harness gives users a stable CLI and web interface while allowing the agent to plan, execute, remember, and improve over time.

## Harness Architecture

```text
User
  |
  v
CLI / Web UI / API
  |
  v
MatCreator Agent App
  |
  +-- Flash mode: direct interactive execution
  |
  +-- Plan mode: thinking agent -> execution graph -> execution agent
  |
  v
Skills, Tools, Workspace, Knowledge
```

## Runtime Layers

| Layer | Role |
| --- | --- |
| CLI | Provides `matcreator chat`, `matcreator run`, `matcreator config`, and knowledge commands. |
| ADK server | Hosts the MatCreator agent app for web and API usage. |
| Web backend | Adds project-specific APIs, artifact management, settings, and server-mode control-plane behavior. |
| Vite frontend | Provides the browser interface for chat, graph visualization, and artifact interaction. |
| Skills | Modular Markdown capabilities that describe domain procedures and required tools. |
| Workspace | Stores user sessions, generated artifacts, local skills, guides, and memory files. |
| Knowledge graph | Stores durable capabilities, procedures, memories, and distilled heuristics. |

## Execution Modes

### Flash Mode

Flash mode is the default for `matcreator chat`. It lets the agent respond and act directly, which is useful for interactive exploration and quick tasks.

```bash
matcreator chat --workspace .
```

### Plan Mode

Plan mode asks the thinking agent to build an execution graph before work is handed to the execution agent. It is useful when a task has multiple dependent steps or when the user wants to inspect the workflow shape.

```bash
matcreator chat --workspace . --plan
```

The graph is a directed acyclic graph whose nodes are discrete actions and whose edges encode dependencies. Independent nodes can run in parallel, while failed nodes block their dependents.

## Configuration And State

MatCreator stores persistent user-level configuration at:

```text
~/.matcreator/config.yaml
```

ADK session state and the default Know-Do Graph database live under:

```text
~/.matcreator/.adk/
```

Workspace-specific sessions and artifacts live under the workspace selected with `--workspace` or `MATCLAW_WORKSPACE`.

### Session-Derived Benchmark Questions

The session list can generate a review-only benchmark question draft from bounded observable session evidence. Drafts are staged under the user's evaluation workspace and require an explicit YAML review, approval, and export before they can be discovered by `mat-agent-bench`.

Question generation uses a file-oriented plugin. The host writes a versioned `session.json`, passes it and the maintained `question_templates/mab_qa.json` authoring template to the plugin, and validates the plugin's `question.yaml` output. The default `builtin_llm` plugin uses the same `llm` settings as the rest of MatCreator:

```yaml
session_question_generator:
  plugin: builtin_llm
  # Optional development override; the packaged template is used by default.
  template_path: /absolute/path/to/mab_qa.json
```

Plugins do not read `~/.matcreator/.adk/session.db` directly. Session authorization and database access remain host responsibilities, so providers can be replaced without depending on the ADK SQLite schema.

`mkb_projection` is available when the pinned `mat-know-base[materials]` dependency is installed. It uses MKB's `qa_benchmark` projection prompt and agent runner, but receives the bounded MatCreator session payload directly rather than creating MKB projects, frames, or database records. By default it uses the same `llm.model`, `llm.api_key`, and `llm.base_url` as MatCreator's other agents. Use plugin or MKB environment values only when an explicit override is needed:

```yaml
session_question_generator:
  plugin: mkb_projection
  # Optional: defaults to llm.model.
  model: openai/qwen3-plus
  # Optional: defaults to llm.api_key and llm.base_url.
  api_key: your-mkb-specific-api-key
  base_url: https://api.example.com/v1
```

```bash
export MKB_LLM_API_KEY=your-api-key
export MKB_LLM_API_BASE=https://api.example.com/v1
```

`MKB_EXTRACTION_MODEL`, `MKB_LLM_API_KEY`, and `MKB_LLM_API_BASE` override the respective plugin and MatCreator values when set. The MKB adapter has no database tools, returns one bare JSON question object, and writes it as MatCreator's validated `question.yaml`; all draft review, refinement, approval, and export remain in MatCreator.

When generating from the session list, MatCreator presents a generator dropdown. Its options are supplied by the backend registry, so additional extraction agents only need a registered definition (identifier, display metadata, and factory) and automatically appear in the UI. A draft keeps its selected generator for later refinement.

Generated drafts are saved independently of the active workspace under:

```text
~/.matcreator/evals/question-drafts/<draft-id>/<question-id>/question.yaml
```

Server mode uses the equivalent path inside each user's `.matcreator` home. Drafts created by older versions under `workspace/evaluations/question-drafts` remain readable and move to the stable location on their next edit, refinement, approval, or export. The review dialog's **Drafts** action lists saved drafts so they can be reopened after closing the dialog.

**Refine with feedback** sends the complete current question, MatCreator validation errors, bounded source-session evidence, and an optional user instruction through the same configured plugin. The revised `question.yaml` is validated and replaces the current draft atomically. Refinement metadata retains hashes, feedback, status, and timestamps but not previous full YAML revisions. Refining an approved question requires approval again.

Configure the live benchmark bank with either `MAT_BENCH_QUESTION_BANK_ROOT` or the following user configuration value:

```yaml
benchmark:
  question_bank_root: /absolute/path/to/mat_agent_bench/question_bank
```

Generation, review, approval, and export always perform MatCreator's executable-verifier validation. They do not require importing `mat_bench`; its `QuestionItem` schema can be used as an optional compatibility check by integrations that install that package. Generated data files are not exported in the current implementation; questions requiring input files must be completed through the benchmark-bank authoring workflow.

For local development, MatCreator can automatically request and save `benchmark.token` when it is absent. Start the benchmark server with `--allow-token-registration` and configure only `benchmark.server_url`. The first catalog load or evaluation start registers a token through `POST /token` and persists it to `~/.matcreator/config.yaml`. This fallback is intentionally unavailable when the benchmark server disables registration; production deployments should configure `benchmark.token` or `MAT_BENCH_TOKEN` explicitly.
