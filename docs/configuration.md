# Configuration

MatCreator reads its persistent settings from a single YAML file, `config.yaml`. This
page documents where the file lives, how to edit it, every supported section, and how
to set up **multiple LLM models** ("executor LLM cards") so different agent steps can
use different models.

## Where config.yaml lives

By default the file lives at `~/.matcreator/config.yaml`. You can relocate it with
either of these environment variables:

```bash
export MATCREATOR_HOME=/path/to/matcreator-home     # config.yaml lives at $MATCREATOR_HOME/config.yaml
export MATCREATOR_CONFIG_PATH=/path/to/config.yaml  # points directly at the file
```

`MATCREATOR_CONFIG_PATH` takes precedence over `MATCREATOR_HOME` if both are set.

## Editing config.yaml

For simple scalar values, use the `matcreator config` CLI:

```bash
matcreator config set llm.model=openai/qwen3-plus
matcreator config get llm.model
matcreator config show               # print all settings (secrets masked)
matcreator config show --reveal-secrets
```

`config set` only writes a single string value to a dotted key (e.g. `llm.api_key`), so
it works well for the top-level `llm`/`bohrium`/`compute` fields shown below. For
**nested structures** — most importantly the multi-model `llm.executor_cards` block —
open `config.yaml` in an editor and write the YAML directly, as shown in the
[Multi-model LLM cards](#multi-model-llm-cards) section.

Sensitive fields (`llm.api_key`, `bohrium.password`, `bohrium.access_key`,
`benchmark.token`) are masked automatically by `matcreator config show` and
`matcreator config get` unless you pass `--reveal`/`--reveal-secrets`.

## Precedence: environment variables vs. config.yaml

MatCreator loads `config.yaml` once at startup and copies its values into environment
variables that the rest of the code reads. The precedence rule differs by deployment
mode:

- **Local mode** (default, `MATCREATOR_MODE=local` or unset): any environment variable
  that was already set *before* MatCreator started wins over the value in
  `config.yaml`. This lets you temporarily override a setting for one shell session
  without editing the file.
- **Server mode** (`MATCREATOR_MODE=server`): `config.yaml` values win over
  pre-existing environment variables, since a mounted user config should override
  injected deployment defaults.

## Configuration reference

| Section | Field | Env var override | Notes |
|---|---|---|---|
| `llm` | `model` | `LLM_MODEL` | Default model for the harness. |
| | `api_key` | `LLM_API_KEY` | API key for `model`. |
| | `base_url` | `LLM_BASE_URL` | Custom/OpenAI-compatible base URL. |
| | `embedding_model` | `EMBEDDING_MODEL` | Model used for knowledge-graph embeddings. |
| | `graph_agent_model` | `GRAPH_AGENT_MODEL` | Overrides the model used by knowledge extraction/query agents. Falls back to `llm.model`. |
| | `review_agent_model` | `REVIEW_AGENT_MODEL` | Overrides the model used by the knowledge review agent. Falls back to `GRAPH_AGENT_MODEL`. |
| | `executor_cards` | *(see below)* | Multi-model configuration for per-step executor agents — see [Multi-model LLM cards](#multi-model-llm-cards). |
| `bohrium` | `email` | `BOHRIUM_USERNAME` | Bohrium account email. |
| | `password` | `BOHRIUM_PASSWORD` | Bohrium account password. |
| | `access_key` | `BOHRIUM_ACCESS_KEY` | Bohrium access key (alternative to password). |
| | `api_url` | `BOHRIUM_API_URL` | Bohrium API endpoint. |
| | `project_id` | `BOHRIUM_PROJECT_ID` | Bohrium project ID for job submission. |
| `compute` | `vasp_image` | `BOHRIUM_VASP_IMAGE` | Container image for VASP jobs. |
| | `vasp_machine` | `BOHRIUM_VASP_MACHINE` | Machine type for VASP jobs. |
| | `deepmd_image` | `BOHRIUM_DEEPMD_IMAGE` | Container image for DeepMD jobs. |
| | `deepmd_machine` | `BOHRIUM_DEEPMD_MACHINE` | Machine type for DeepMD jobs. |
| | `deepmd_model_path` | `DEEPMD_MODEL_PATH` | Path to a trained DeepMD model. |
| `benchmark` | `server_url` | `MAT_BENCH_SERVER_URL` | Benchmark server endpoint. |
| | `token` | `MAT_BENCH_TOKEN` | Benchmark auth token. |
| | `question_bank_root` | `MAT_BENCH_QUESTION_BANK_ROOT` | Local path to the benchmark question bank. |
| `runtime` | `execution_timeout_seconds` | `MATCREATOR_EXEC_TIMEOUT_SECONDS` | Timeout for Python/Bash/skill script execution (default 3600s). |
| `knowledge` | `memorization_frequency` | `MATCREATOR_MEMORIZATION_FREQUENCY` | How often (in successful completions) to extract knowledge. |
| | `review_frequency` | `MATCREATOR_REVIEW_FREQUENCY` | How often to run the knowledge review agent. |
| `skills` | `module_root` | `MATCREATOR_MODULE_SKILLS_ROOT` | Path to a custom skills module directory. |
| | `disabled` | *(none)* | List of skill names disabled for knowledge graph search. |
| `planning` | `extra_skills` | *(none)* | List of extra skill names promoted to planning access. |
| `env` | *(any key)* | *(itself)* | Arbitrary extra environment variables to inject, e.g. `MP_API_KEY`, `BOHRIUM_USERNAME`. See [Arbitrary environment overrides](#arbitrary-environment-overrides). |

### Arbitrary environment overrides

The `env` section lets you set any additional environment variable your skills might
need, without a dedicated config field:

```yaml
env:
  MP_API_KEY: your-materials-project-api-key
  SOME_OTHER_TOOL_TOKEN: ...
```

Keys must be valid environment variable names and cannot overwrite protected variables
such as `HOME`, `PATH`, `PYTHONPATH`, `LD_LIBRARY_PATH`, `MATCREATOR_HOME`,
`MATCREATOR_MODE`, or `MATCREATOR_USER_ID`.

## Multi-model LLM cards

By default every executor step uses the single model configured under `llm.model`.
For more control — for example, routing vision tasks to a multimodal model, or using a
cheaper/faster model for simple tool calls — define multiple **LLM cards** under
`llm.executor_cards` and MatCreator will automatically pick the best card for each
step.

### Structure

```yaml
llm:
  model: openai/qwen3-plus
  api_key: sk-...
  base_url: https://api.example.com/v1

  executor_cards:
    default: balanced        # name of the card used when no other card scores higher
    cards:
      balanced:
        model: openai/qwen3-plus
        description: General executor model for routine tool use.
        skills: [filesystem, python]
        tags: [general]
        cost_tier: medium
        latency_tier: medium
        priority: 0

      vision:
        model: openai/qwen3-vl-plus
        api_key: sk-...                 # optional: override api_key just for this card
        base_url: https://api.example.com/v1  # optional: override base_url just for this card
        description: Multimodal model for image/vision tasks and diagram analysis.
        modalities: [image, vision]
        skills: [structure-conversion, ketcher]
        tags: [vision, multimodal]
        routing_keywords: [image, diagram, screenshot, plot, visualize]
        cost_tier: high
        latency_tier: medium
        priority: 5

      fast:
        model: openai/qwen3-turbo
        description: Cheap, fast model for trivial or high-volume tool calls.
        skills: [filesystem]
        tags: [cheap, fast]
        cost_tier: low
        latency_tier: low
        priority: 1
```

### Card fields

| Field | Type | Description |
|---|---|---|
| `model` | string | **Required.** The model identifier passed to the LLM provider. Falls back to `llm.model` if omitted. |
| `api_key` | string | Optional per-card API key. Falls back to `llm.api_key` if omitted. Use this when a card points at a different provider than your default. |
| `base_url` | string | Optional per-card base URL. Falls back to `llm.base_url` if omitted. |
| `description` | string | Free-text description used both for humans and for matching against the current task/action when selecting a card. |
| `modalities` | list of strings | e.g. `[image, vision]`. Used to detect cards that support image input, and contributes to selection scoring. |
| `skills` | list of strings | Skill names this card is well-suited for (e.g. `[vasp-pymatgen, phonon]`). Strongly weighted in card selection. |
| `tags` | list of strings | Free-form labels (e.g. `[vision, cheap]`) that also feed into selection scoring. |
| `routing_keywords` | list of strings (alias: `keywords`) | Keywords matched against the step's action text/prior context to bias selection toward this card. |
| `cost_tier` | string | Informational label (e.g. `low`, `medium`, `high`) surfaced to users/UI; not used in selection logic. |
| `latency_tier` | string | Informational label (e.g. `low`, `medium`, `high`); not used in selection logic. |
| `priority` | integer | Base score added before token matching. Higher priority wins ties and biases selection toward this card. Default `0`. |

You can declare `cards` either as a mapping (name → fields, as above, where the key is
the card name) or as a list of objects that each include a `name` field — both forms
are supported.

### How card selection works

For each executor step, MatCreator scores every card against the step's action text,
prior context, and suggested skills:

1. Tokenize the step's action/skills text and each card's `name`, `description`,
   `modalities`, `skills`, `tags`, and `routing_keywords`.
2. Score = `priority` + `6 × (matching skill tokens)` + `3 × (matching routing_keyword
   tokens)` + `2 × (other matching tokens)`.
3. The highest-scoring card is selected. If no card scores above `0`, the
   `executor_cards.default` card is used instead.

This means you don't need to write routing rules by hand — just describe each card
accurately (skills, tags, routing keywords) and the closest match is chosen
automatically per step.

### Forcing a specific card

To bypass automatic selection and force every executor step to use one card, set:

```bash
export MATCREATOR_EXECUTOR_LLM_CARD=vision
```

This must match a card `name` defined under `llm.executor_cards.cards`; if the name
isn't found, automatic selection is used instead.

### Checking your configuration

```bash
matcreator config show
```

prints the full `llm` section (with `api_key` masked) so you can confirm your cards
were saved correctly. If `llm.executor_cards` is missing or empty, MatCreator falls
back to a single implicit `default` card built from `llm.model` / `llm.api_key` /
`llm.base_url`.

### Separate model overrides for knowledge agents

The knowledge-graph extraction, query, and review agents are independent of executor
LLM cards and use their own single-model overrides:

```yaml
llm:
  graph_agent_model: openai/qwen3-plus     # used by extraction and query agents
  review_agent_model: openai/qwen3-max     # used by the review agent; falls back to graph_agent_model
```

Both fall back to `llm.model` if unset.

## Next Steps

- Follow the [Getting Started](getting-started.md) guide for the fastest path to a
  running session.
- See the [Overview](overview.md) for how the executor, planner, and knowledge agents
  fit together.
- See [Deployment](deployment.md) for how config.yaml behaves differently between
  personal and server deployments.
