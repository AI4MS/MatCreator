---
name: skill-creation
description: Guide for creating, checking, and evaluating reusable ADK skill bundles under the user-global MatCreator skill root.
metadata:
  tools:
    - get_user_skills_root
    - run_python
    - run_bash
    - refresh_skills
  tags:
    - skill-authoring
    - adk
    - validation
---

# Skill Creation Guide

Use this guide when the user asks to create, improve, or evaluate a reusable MatCreator skill.

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

## Authoring Workflow

1. Clarify the intended task, inputs, outputs, required tools, and success criteria.
2. Search existing skills before creating a new one. Update or extend an existing user skill only when that is what the user wants.
3. Design the bundle layout and write `SKILL.md`.
4. Add references, assets, scripts, or tests only when they make the skill more reliable.
5. Keep generated scripts deterministic and self-contained when possible.

## Required Checks

After creating or changing a skill, run these checks and fix failures before reporting success:

1. **Static load check**: verify `google.adk.skills.load_skill_from_dir(<skill_dir>)` loads the bundle.
2. **Collision check**: verify the name does not conflict with bundled skills.
3. **Refresh check**: call `refresh_skills()` so the current session can discover the new skill.
4. **Discovery check**: verify the skill can be found or loaded through MatCreator skill discovery.
5. **Behavior check**: run a minimal representative test. For instruction-only skills, simulate the expected decision path. For script-backed skills, run at least one safe script invocation or syntax check.

## Reporting

Report:

- Absolute path to `SKILL.md`.
- Files created or changed.
- Checks performed and their pass/fail result.
- Any unsupported assumptions, missing external dependencies, or limitations.
