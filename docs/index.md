# MatCreator

MatCreator is an agentic AI platform for computational materials science. It helps researchers build workflows, generate structures, run simulations, and accumulate reusable skills through a long-lived knowledge graph.

## Documentation Map

- [Getting Started](getting-started.md): install MatCreator, configure LLM credentials, and run the first CLI or web session.
- [Configuration](configuration.md): a detailed reference for `config.yaml`, including how to set up multiple LLM models with executor LLM cards.
- [Overview](overview.md): understand the MatCreator harness architecture and runtime flow.
- [Architecture Components](architecture_components.md): understand the frontend, middleware, agent framework, and how requests move between them.
- [Deployment](deployment.md): run MatCreator as a personal application or deploy it as a shared server.
- [Knowledge](knowledge/index.md): learn how MatCreator stores skills, guides, working memory, and distilled knowledge.

## Core Capabilities

- Agentic workflow planning for computational materials tasks
- Interactive CLI access with `matcreator chat`
- A web interface for graph visualization and artifact management
- Modular skills for simulation, structure generation, analysis, and workflow automation
- Persistent configuration and memory under `~/.matcreator`

## Local Documentation Preview

Install the documentation dependencies and start the local preview server:

```bash
pip install -r docs/requirements.txt
mkdocs serve
```

The site is served at `http://127.0.0.1:8000` by default.
