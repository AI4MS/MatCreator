# Know-Do Graph

The Know-Do Graph is MatCreator's long-term knowledge base. It stores what the agent can do, how tasks are performed, what happened in previous sessions, and which observations have become reliable enough to reuse.

## Data Model

Durable knowledge and working memory are stored together in one SQLite-backed graph. The default location is:

```text
~/.matcreator/.adk/know_do_graph.db
```

The graph contains entries and typed edges:

| Entry type | Meaning |
| --- | --- |
| Capability | A reusable skill or ability. |
| Procedure | A guide, workflow, or multi-step method. |
| Heuristic | A distilled lesson from repeated successful evidence. |
| Memory | A session-level observation, result, or intermediate learning. |

## Runtime Behavior

- Skills are durable capability entries.
- Guides are durable procedure entries.
- Agent saves are native memory nodes.
- Memory-to-memory relationships use `related_memory` edges.
- Memory linked to a durable skill uses `memory_of` edges.
- Promoted memory links to refined knowledge with `refinement_of` edges.
- Retrieval searches durable entries and unpromoted memory in the same graph.

## Progressive Retrieval

MatCreator first searches high-level capabilities and procedures. It then conditionally expands into heuristics, constraints, and memories connected to the selected knowledge. This keeps retrieval focused while still allowing the agent to recover detailed lessons when they are relevant.

## Review And Distillation

The knowledge reviewer can examine graph entries, session memory, and repeated patterns. Successful repeated memories can be promoted into durable heuristics:

```bash
matcreator knowledge distill --min-evidence 3
```

Nodes marked as peer-reviewed or community-tested are protected from mutation during review.

## Maintenance

Clear only session memory while preserving skills and durable graph nodes:

```bash
matcreator graph clear-memory
```

Reset learned graph data and rebuild from the currently installed built-in,
official, user-global, and workspace custom skill files:

```bash
matcreator graph reset
```

Both commands ask for confirmation and create a timestamped SQLite backup in a
`backups/` directory beside the active graph database. Pass `--yes` for
non-interactive use or `--no-backup` to explicitly skip the backup. A reset
treats the skill files as the source of truth, so current custom skills and
their attachments are restored while deleted custom skills stay removed.

## Useful Commands

```bash
matcreator knowledge seed
matcreator knowledge query "structure generation"
matcreator knowledge search-skills "VASP relaxation"
matcreator knowledge related-skills <start-node>
matcreator knowledge stats
matcreator knowledge distill --min-evidence 3
```

For legacy database migration, see [Knowledge Migration](knowledge/migration.md).
