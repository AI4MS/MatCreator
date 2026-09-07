---
name: skill-creation
description: Guide for creating, checking, and evaluating reusable ADK skill bundles under the user-global MatCreator skill root.
metadata:
  tools:
    - get_user_skills_root
    - run_python
    - run_bash
    - refresh_skills
  dependent_skills: []
  tags:
    - skill-authoring
    - adk
    - validation
---

# Skill Creation Guide

Use this guide when the user asks to create, improve, or evaluate a reusable MatCreator skill. Treat a skill as both an executable instruction bundle and a testable knowledge artifact: it must be discoverable, operational, and possible to judge after use.

## Target Location

1. Call `get_user_skills_root()` before writing any skill files.
2. Put every generated skill under:
   `<user_skills_root>/<skill-name>/`
3. The primary instruction file must be:
   `<user_skills_root>/<skill-name>/SKILL.md`
4. Do not write generated reusable skills under the workspace skills directory.
5. Reject or revise any path that would escape the returned user skills root.

## Bundle Layout

Use the standard ADK skill layout:

```text
<skill-name>/
  SKILL.md
  references/      optional long-form references
  assets/          optional examples, templates, or static data
  scripts/         optional executable helper scripts
  tests/           optional validation scripts or fixtures
```

Keep `SKILL.md` concise. Move lengthy command references, scientific background, examples, or API notes into `references/` or `assets/`.

## Know-Do Graph Model

MatCreator seeds skill bundles into the Know-Do Graph. Use the graph's native four-level model and do not invent a second hierarchy:

| Level | KDG meaning | Use it for |
|---|---|---|
| L1 | capability, workflow, or the main skill entry | What the skill can do and when it applies |
| L2 | procedure | Ordered, repeatable steps, usually a guide or detailed operating procedure |
| L3 | heuristic | A rule of thumb, decision aid, or context-dependent tactic attached to a parent skill |
| L4 | constraint | A limitation, prohibition, safety condition, or known failure boundary attached to a parent skill |

For a bundle represented directly in the graph, declare `metadata.entry_type` and `metadata.skill_level`. Use `capability`/`L1` for a normal atomic skill, `workflow`/`L1` for a multi-stage skill, and `procedure`/`L2` when the node itself is an ordered procedure. Use `heuristic`/`L3` or `constraint`/`L4` only for sidecar knowledge, and list the parent skill in `metadata.dependent_skills` so MatCreator can create the `heuristic_for` or `constraint_on` edge. A guide is seeded as `procedure`/`L2` automatically.

Do not use L3/L4 as a disguised quality score. They describe the kind of knowledge; quality and runtime outcome belong in the evaluation contract below. The current loader consumes `entry_type`, `skill_level`, `dependent_skills`, and `tags`; keep other frontmatter fields informational unless their consumer is verified.

## `SKILL.md` Format

Use YAML frontmatter followed by Markdown instructions:

```markdown
---
name: <kebab-case-or-snake_case-name>
description: <one sentence that helps the planner decide when to use the skill>
metadata:
  tools:
    - run_bash
  dependent_skills: []
  tags:
    - relevant-tag
---

# <Human-readable title>

Clear, operational instructions for the agent.
```

Rules:

- Use a stable, lowercase name with only letters, digits, hyphens, or underscores.
- Do not use a name that conflicts with a bundled skill.
- Make the description specific enough for retrieval.
- List required tool names in `metadata.tools`.
- List related skill names in `metadata.dependent_skills`.
- Do not invent commands, flags, APIs, file formats, or scientific claims. If uncertain, gather evidence first.

## Evaluation Contract

Every non-trivial skill must state how to tell whether its use was correct and useful. Put this contract in `SKILL.md` or a clearly linked reference; do not rely on a vague "check the output" instruction. Define:

- **Inputs and preconditions:** required files, parameters, environment, permissions, and assumptions.
- **Expected result:** the output type, required fields or files, units, and acceptable status.
- **Correctness criteria:** deterministic checks such as schema validation, exit status, conservation or domain constraints, reference comparison, or a user-provided target. Give thresholds and tolerances where meaningful.
- **Quality criteria:** usefulness beyond mere execution, such as completeness, reproducibility, numerical accuracy, ranking quality, resource cost, or absence of unsupported claims. State which are hard gates and which are trade-offs.
- **Evidence:** commands, logs, reports, plots, or source paths that prove each criterion. Prefer machine-readable output and parse it instead of trusting prose.
- **Failure handling:** recognizable failure signals, likely causes, recovery or retry limits, and when to stop and ask the user.
- **Minimal evaluation case:** one representative invocation with a known or independently checkable expected result. Include a negative or boundary case for risky behavior.

Separate two kinds of metrics:

1. **Build metrics** validate the bundle itself: it loads, has valid frontmatter, resolves dependencies, exposes the promised tools/resources, and passes a safe representative invocation.
2. **Use metrics** validate an execution: the task reached the expected state, outputs satisfy correctness gates, quality thresholds were met, and the evidence is sufficient to reproduce the judgment.

If no objective reference exists, label the criterion as a human or LLM rubric and define the rubric dimensions, score scale, required evidence, and pass threshold. Never turn an unchecked run into a success merely because a command exited with code 0.

## Authoring Workflow

1. Clarify the intended task, inputs, outputs, required tools, graph level, parent links, and both build and use metrics.
2. Search existing skills and graph entries before creating a new one. Update or extend an existing user skill only when that is what the user wants.
3. Choose the smallest valid KDG representation. Keep L3/L4 knowledge attached to an existing parent instead of creating an unrelated top-level skill.
4. Design the bundle layout and write `SKILL.md`, including the evaluation contract and a minimal evaluation case.
5. Add references, assets, scripts, or tests only when they make the skill more reliable.
6. Keep generated scripts deterministic and self-contained when possible.

## Required Checks

After creating or changing a skill, run these checks and fix failures before reporting success:

1. **Static load check**: verify `google.adk.skills.load_skill_from_dir(<skill_dir>)` loads the bundle and inspect the parsed frontmatter.
2. **Schema check**: verify the name is stable and valid, the declared tools and dependencies are real, and `entry_type`/`skill_level` are one of the supported KDG values.
3. **Graph check**: seed or inspect the graph and verify the expected node level/type plus every dependency, `heuristic_for`, or `constraint_on` parent edge. Confirm that an L3/L4 node is not orphaned.
4. **Collision check**: verify the name does not conflict with bundled, official, or existing user skills.
5. **Refresh and discovery checks**: call `refresh_skills()` and verify the skill can be found and loaded through MatCreator discovery.
6. **Build behavior check**: run a minimal representative test. For instruction-only skills, simulate the expected decision path. For script-backed skills, run at least one safe script invocation or syntax check and inspect its structured result.
7. **Use evaluation check**: apply every hard correctness gate and record the evidence. Run the boundary or negative case when one is defined; report pass, fail, or indeterminate rather than inferring quality from process completion.

## Reporting

Report:

- Absolute path to `SKILL.md`.
- Files created or changed.
- Checks performed and their pass/fail result.
- KDG node level/type and parent edges created or verified.
- Build-metric results and use-metric results, including evidence and any indeterminate criteria.
- Any unsupported assumptions, missing external dependencies, or limitations.
