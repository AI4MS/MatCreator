# Getting Started

This page gives the fastest path from a fresh checkout to a running MatCreator session.

## Installation

```bash
git clone https://github.com/AI4MS/MatCreator.git
cd MatCreator

pip install uv
uv venv .venv --python 3.12
source .venv/bin/activate

uv pip install -e .
```

## Configure LLM Credentials

Use the MatCreator CLI to write persistent settings to `~/.matcreator/config.yaml`:

```bash
matcreator config set llm.model=openai/qwen3-plus
matcreator config set llm.api_key=your-api-key
matcreator config set llm.base_url=https://api.example.com/v1
```

Check the current configuration without revealing secrets:

```bash
matcreator config show
```

## Install the Vite frontend

The Vite frontend uses Ketcher and requires Node.js 24.14.1 or later. Install
its dependencies with:

```bash
cd web/vite-frontend
npm install
```

Check the installed versions with:
```bash
node --version
npx vite --version
```

## Start the CLI

Start an interactive session in the current project workspace:

```bash
matcreator chat --workspace .
```

By default, `matcreator chat` uses Flash mode for direct interaction. Use `--plan` for the full planning and graph-execution workflow:

```bash
matcreator chat --workspace . --plan
```

Try a simple prompt:

```text
Generate a Li7La3Zr2O12 structure and save the result in the workspace.
```

MatCreator stores session data and generated files under the selected workspace.

## Start the Web UI

```bash
bash script/start_matcreator.sh
```

This starts the ADK API server, FastAPI middle layer, and Vite frontend.

Open the frontend at:

```text
http://localhost:5173
```

## Useful Commands

Show saved configuration:

```bash
matcreator config show
```

Run a one-shot prompt without entering the chat loop:

```bash
matcreator run -p "Build a silicon FCC structure"
```

Query the knowledge graph:

```bash
matcreator knowledge query "structure generation"
```

## Next Steps

- Read the [Overview](overview.md) to understand the harness architecture.
- Choose a [Deployment](deployment.md) path for personal or shared use.
- Learn how MatCreator's [Knowledge](knowledge/index.md) layer works.
