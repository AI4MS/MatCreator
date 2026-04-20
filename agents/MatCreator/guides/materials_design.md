---
name: materials-design
description: Iterative workflow for materials design through candidate generation, property prediction, screening, and refinement.
skills: [mattergen-generation, mattergen-finetune, mattergen-evaluation, structure_conversion, mattersim, cgcnn_predictor]
tags: [materials design, inverse design, iterative, screening, generation]
---
Use this guide when the goal is to discover or optimize crystal materials by repeatedly generating candidates, predicting target properties, and narrowing the search space.

## Standard Loop

1. Define the design target
- Confirm the chemical system, target properties, and screening rules, including thresholds, ranking metrics, batch size, and maximum iterations.

2. Generate candidate structures
- Prefer using `mattergen` to create a batch of candidate crystals.
- Always save generated candidates to a dedicated iteration directory and report absolute paths.

3. Relax generated structures
- Before screening, use `mattersim` to relax generated structures.
- Save relaxed structures to a dedicated output path or directory and report the absolute paths.

4. Predict properties and rank candidates
- Convert or reorganize generated structures into the format required by the downstream predictor or validation workflow.
- Use an available property-prediction workflow on the screened candidate set, such as `cgcnn_predictor` or another model/tool appropriate for the target property.
- Rank or filter candidates according to the user-defined objective.

5. Select the next round
- Keep the top candidates and summarize why they were selected.
- If the predicted structures and properties do not satisfy the target requirements, you may need to improve the workflow iteratively, for example by fine-tuning the model or using other approaches.

6. Evaluation
- Evaluate the screened structures with the S.U.N criteria, which can be carried out using the `mattergen-evaluation` skill.

7. Optionally validate the final shortlist with DFT
- After screening, optionally run DFT on a few top shortlisted candidates.
- If DFT strongly disagrees with the screening model, summarize the mismatch and decide whether another iteration is needed.

## Recommended Defaults

- Start with 10 to 50 generated candidates per round, depending on compute budget.
- Use a fast property predictor for early screening and save stricter evaluation for the final shortlist.
- Reserve DFT for optional final validation of the best few candidates rather than for every iteration.
- Keep a per-iteration summary table with generated structure paths, converted structure paths or directories, prediction model, predicted properties, and pass/fail status.

