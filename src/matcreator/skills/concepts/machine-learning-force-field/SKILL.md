---
name: machine-learning-force-field
description: Concept skill for Machine Learning Force Fields (MLFFs). Describes what MLFFs are, the unified init-model fine-tuning workflow with base-model selection (DPA4 vs DPA4c), and which tool skills to use. Load this before selecting a specific MLFF framework (DeePMD, MatterSim, etc.).
metadata:
  dependent_skills:
    - deepmd
    - mattersim
    - ase
    - lammps
  tags:
    - MLFF
    - machine-learned-force-fields
    - deep-potential
    - force-field
    - training
---

# Machine Learning Force Field (MLFF)

A Machine Learning Force Field (MLFF) is a surrogate model trained on DFT reference data that predicts atomic energies and forces at a fraction of the computational cost.
MLFFs enable large-scale and long-timescale molecular dynamics simulations that would be prohibitively expensive with DFT directly.

## MLFF-related tasks

| Task name                 | Description                                                                                                   |
|---------------------------|---------------------------------------------------------------------------------------------------------------|
| **Fine-tuning**           | Init-model fine-tune a pre-trained model to a target system using DFT-labeled (or optionally model-labeled) data in that system. |
| **Inference / MD**        | Deploy the MLFF model for structure relaxation or molecular dynamics.                                         |

## When to Use

- Init-model fine-tuning of a pre-trained model is the most common use case.
- When the pre-trained or fine-tuned model is too large for productive MD simulations, choose the lightweight **DPA4c** base model; if a fine-tuned model is available, DPA4c can optionally use **model labeling** (the fine-tuned model's predictions replace DFT) to generate large datasets cheaply.
- Act as the energy, force and stress provider for the majority of atomistic property calculations
  (after confirmed accuracy on DFT-labeled testing set.)

## Related Skills

| Skill       | Use case of related skills                                                                                                 |
|-------------|----------------------------------------------------------------------------------------------------------------------------|
| `deepmd`    | Fine-tuning, evaluating and inferencing with all deepmd models (including DPA-1, DPA-2, DPA-3 and DPA-4 / DPA-4c) |
| `mattersim` | Fine-tuning, evaluating and inferencing with the Mattersim model                                                           |
| `ase`       | Running MD with a pre-trained or fine-tuned MLFF model via ASE interface to yield samples; or light productive simulations |
| `lammps`    | Running MD with a frozen deepmd model via LAMMPS, only for heavy, large-scale simulations                                  |

Load the appropriate tool skill when needing detailed instructions (e.g., `load_skill("deepmd")`).

> **Note:** The `deepmd` skill is recommended over `mattersim` for most use cases as deepmd models provide better accuracy.

---

# Base-model selection: DPA4 vs DPA4c

Two deepmd base models cover all MLFF generation needs. Both use **init-model fine-tuning** — the only systematic differences are the **base model** and the **input.json** template. Choose based on the intended use:

| Base model | Choose when | Typical role |
|------------|-------------|--------------|
| **DPA4** | **High-accuracy** force fields | Pre-trained general-purpose model, init-model fine-tuned on DFT-labeled data of the target system. Best accuracy. |
| **DPA4c** | **High-efficiency / large-scale simulation** force fields | Lightweight model, init-model fine-tuned on DFT-labeled or model-labeled data. Best inference speed for production MD (> 100 K atoms, > 1 ns). |

> **Summary in plain language:**
> - Need **high precision** → choose **DPA4**.
> - Need **high efficiency** or **large-scale simulation** → choose **DPA4c**.
> - Both are fine-tuned the same way (init-model); the only differences are the base model and input.json.

### DPA4c model labeling

When a **fine-tuned DPA4 model** for the target system is available, DPA4c can
optionally use **model labeling** — the fine-tuned model's predictions replace
DFT single-point calculations for labeling structures. This enables generating
much larger datasets (~10× more frames) at far lower cost. This path is only
available for DPA4c.

A valid **fine-tuned model** for model labeling must satisfy BOTH conditions:
1. It is a **fine-tuned** model on the target system (has already gone through
   the DPA4 fine-tuning procedure on DFT-labeled data of that system).
   **Pretrained models must NEVER be used as the fine-tuned model for model labeling.**
2. It is a **single-task** model dedicated to the target system. For models that
   support multi-task training (such as DPA-3), the fine-tuned model must be a
   single-task model specifically fine-tuned on the target system — a multi-task
   model is **not** a valid fine-tuned model.

If no valid fine-tuned model exists, use **DFT labeling** instead (the default
for both DPA4 and DPA4c). If the user specifically wants model labeling but has
no fine-tuned model, return to the DPA4 fine-tuning workflow first, produce a
fine-tuned single-task model, and only then come back to DPA4c.

### DPA4c multi-component warning

> **WARNING — DPA4c and multi-component systems:**
> Training a single DPA4c model on a **mixed multi-component** dataset (i.e. several
> unrelated chemical systems trained together in one model) typically yields
> **insufficient accuracy** and can produce **MD trajectories that diverge or crash**.
>
> **Recommended:** train **separate DPA4c models per component group** —
> one DPA4c model per chemically coherent subsystem — and only combine them at
> simulation time.
>
> **If the user explicitly insists** on a single mixed model despite this warning,
> proceed with mixed training, but flag the accuracy / MD-stability risk to the
> user before delivering.

---

# Unified MLFF generation workflow (init-model fine-tuning)

Both DPA4 and DPA4c follow the **same procedure** below. The only systematic
differences are the **base model** and the **input.json** template — all other
stages are identical. DPA4c additionally supports **optional model labeling**
when a fine-tuned model is available (see Stage 0).

> **Strict stage ordering — Stage 0 → Stage A → Stage B → Stage C.**
> Execute in order; do not skip, reorder, or omit any stage. The only allowed
> variation is tuning *parameters within each stage* (temperature, pressure,
> frame count, epochs, etc.).

> **No concrete CLI in this concept file.** All DPA4 / DPA4c commands, scripts,
> and environment setup are owned by the `deepmd` skill. Load the `deepmd`
> skill when executing any stage below.

## Stage 0 — Gate: data & model availability

Ask the user about available resources before proceeding:

**Ask all users (both DPA4 & DPA4c):** *Do you have a DFT-labelled dataset?*
A "DFT-labeled dataset" means structures whose energy, forces, and virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained ML model.
- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO
  dataset and follow Stages A–C.
- **If the user HAS a DFT-labeled dataset:** skip to Stage B.
- **If the user has NO DFT-labeled dataset:** follow Stages A–C.

**For DPA4c only — optional:** *Do you have a fine-tuned model that can
replace DFT?* (i.e., a model whose predictions serve as labels instead
of DFT single-point calculations.)

> **Key principle:** The pretrained model is only a **surrogate for
> structural-space exploration** via MD, **not a ground truth**. All ground-truth
> labels for fine-tuning and evaluation must come from **DFT calculations**,
> unless model labeling is explicitly chosen (Stage B).

> See [DPA4c model labeling](#dpa4c-model-labeling) above for the validity
> requirements of a fine-tuned model. If no valid fine-tuned model exists,
> use DFT labeling (the default for both DPA4 and DPA4c).

## Stage A — Candidate structure generation via NPT MD

1. **Classify the problem complexity:**
   - **Simple systems** — bulk crystals, random alloys, simple compounds.
   - **Complex systems** — defects, dopants, surfaces, interfaces, transition
     states, high-entropy alloys, amorphous structures, etc. Also treat the
     system as complex when the provided initial structures span **multiple
     distinct cell types** (e.g. different Bravais lattices or coordination
     environments).

2. **For simple systems:** proceed directly to step 4.

3. **For complex systems:** ask the user if they already have initial structure
   files. If yes, use the user's structures as the starting point. If no,
   generate an initial structure (or multiple, if needed) using the
   `atomic-structure` skill (or `matcraft-kit` for surfaces/defects).

4. **Choose simulation cell size for MD:** determine whether the initial
   structures need to be replicated into supercells. Do supercell operations
   only if needed, and perform it only **ONCE** in the entire workflow.
   > **Rules for judging MD simulation cell size:** Keep each structure at
   > roughly **50 atoms** when possible. For systems exceeding this size, do
   > NOT perform supercell operations — use the original cell as-is.

5. **Generate candidate structures** for MD exploration:
   - Use the resulting structure(s) from step 4 as the starting simulation cell.
   - Use the pretrained model (DPA4) or the pretrained / fine-tuned model
     (DPA4c) to set the simulation cell's calculator.
   - Relax the structure (optimize both atomic coordinates and lattice vectors)
     first to avoid MD collapse.
   - Explore configuration space via **NPT-ensemble MD**.
   - **MD sampling skill choice:** `ase` >> `lammps`. Try `ase` first; if it
     fails repeatedly, switch to `lammps`. Never use `atomic-structure` for MD.

   **MD sampling parameters (NPT ensemble):**

   | Parameter                 | Default value           | Description                                                                                       |
   |---------------------------|-------------------------|---------------------------------------------------------------------------------------------------|
   | Ensemble                  | **NPT**                 | NPT ensemble is mandatory for structure exploration                                               |
   | Temperature               | **300 K, 600 K, 900 K** | Target temperatures. For solid-state materials, **approach but never exceed the melting point**. |
   | Pressure                  | **1 bar, 10 GPa**       | Target pressure. For regular conditions, try from 1 bar and 10 GPa; adjust to user needs.       |
   | Step size                 | **2 fs**                | Highest safe step size; decrease to 1 fs above 2000 K or when unstable (volume explosion).       |
   | Structure saving interval | Every **5** steps       | Recommendation: 1 frame per 5 MD steps.                                                           |
   | Duration                  | **10 ps**               | Total simulation time per temperature and per pressure.                                          |

   **Output frames recommendation:**

   | System complexity | DFT labeling (both DPA4 & DPA4c) | Model labeling (DPA4c only when Fine-tune model ready) |
   |--------------------|-----------------------------------|------------------------------|
   | Simple             | **100**                           | **1 000**                    |
   | Complex            | **200**                           | **2 000**                    |
   | Very complex       | **500**                           | **5 000**                    |

   > Model labeling uses roughly **10×** the frames of DFT labeling because
   > labeling is essentially free (no DFT jobs).

6. **Entropy-based structure selection (MANDATORY)**
   After MD sampling, use entropy-based filtering to select a subset of ~50% of
   the structures **with diversity** from the obtained MD frames before labeling,
   to reduce labeling cost. For example, use the `quests` skill's
   `active_learning.py` script with `filter-by-entropy`. The chunk size had
   better be 1/50 of the total number of MD frames, but never below 10.
   > **CRITICAL:** Always run entropy-based selection BEFORE labeling. Never
   > send all sampled frames directly to labeling — use the selected structures.

> **Seed ≠ training set.** The POSCAR / cif structure you start with is a
> **seed** for MD exploration, not a training frame. You MUST first run
> **NPT-ensemble MD** on the seed (Stage A) to generate diverse configurations,
> label those configurations (Stage B), and only then train (Stage C). Directly
> labeling the static seed structure (or a handful of manually-built structures)
> and training on it is a typical failure mode and is strictly forbidden.

## Stage B — Labeling

Label the entropy-selected structures to obtain energy, forces, and virial:

- **Default (both DPA4 & DPA4c):** Run **DFT single-point calculations** on the
  selected structures. Use the `vasp` or `abacus` skill (VASP preferred). See
  `concepts/dft-calculation` for guidance on choosing a DFT code. Job submission
  is handled by the `bohrium` skill.

- **DPA4c with model labeling (optional):** If a valid fine-tuned model was
  identified in Stage 0, run **model inference** on the selected structures
  instead of DFT. The fine-tuned model's predictions replace DFT single-point
  calculations — **no DFT jobs are launched**. The fine-tuned model is
  guaranteed to be a single-task model by the Stage 0 gate, so there is **no
  model head selection** involved — run inference with the fine-tuned model
  as-is. MD/inference skill choice follows the same rules as Stage A (`ase`
  first, `lammps` as fallback).

## Stage C — Training & Evaluation

> Do NOT reuse any existing workdir. **Always create a fresh workdir.**

1. **Prepare input files** in the fresh workdir. For DPA models, use the
   `deepmd` skill's preparation script. Train/test split ratio is **4:1** for
   all labeled frames.

2. **Train (both DPA4 & DPA4c):** Init-model fine-tune the pre-trained model
   on the labeled data. Both base models use `--init-model` initialization
   (see the `deepmd` skill for the concrete CLI, which differs between DPA4
   and DPA4c). Both base models default to **50 epochs** (set via the
   `num_epochs` keyword — do NOT instruct training in steps). Submit the
   training job on Bohrium via the `bohrium` skill.

   > **Model-labeling data-gate (DPA4c only):** Before training, confirm that
   > the training frames were **produced by Stage A → Stage B** (fine-tuned
   > model NPT MD sampling + entropy selection + model inference labeling),
   > **not** statically labeled from the seed or relabeled from existing DFT
   > structures; that the total frame count is ≥ the model-labeling
   > recommendation above; and that the frames span a diverse configuration
   > space. If any check fails, STOP and return to Stage A.

   > **Model labeling is a single-round procedure:** train the DPA4c model once
   > and evaluate. Do NOT iterate (no repeated model-relabeling rounds).

3. **Evaluate:**
   - **Default (both):** Perform testing to obtain predicted energy (and
     per-atom energy), forces, virials (and per-atom virials) or stress, then
     compute MAE errors. Also evaluate the original pretrained model for
     comparison. For DPA models, evaluation of both pretrained and fine-tuned
     models is handled by the `deepmd` skill's preparation script, so results
     come back together with the fine-tuned model. For other MLFF models,
     manually run evaluation via the MLFF's native ASE calculator interface
     (see `ase` skill).
   - **Model labeling (DPA4c only) — two levels required:**
     - **DPA4c vs fine-tuned model:** compare the DPA4c model's
       predictions against the fine-tuned model's labels on the held-out test
       set (the 1/5 test split from step 1). Checks that the DPA4c model
       faithfully reproduces the fine-tuned model.

4. **Energy-bias adjustment (if needed):** When the study system uses very
   different DFT settings from the pretrained model's training set, energy MAE
   may not be directly comparable (different energy zero points). Adjust by
   shifting the predicted per-atom energy by the mean difference
   (`e_shift = mean(e_peratom_dft − e_peratom_predicted)`) and then evaluate
   with `e_peratom_predicted + e_shift` for a comparable energy MAE.

5. **Report and compare the results:**
   - Pretrained: energy per atom MAE = X, force MAE = Y
   - Fine-tuned model: energy per atom MAE = X′, force MAE = Y′
   - Improvement: energy per atom MAE reduced by Z%, force MAE reduced by W%

---

# Post-delivery: model compression (freeze)

After training and evaluation, freeze the model ( **and compress for DPA4c** ) before productive
inference (ASE, LAMMPS). The freeze procedure — including CLI, precision
settings, and hardware-specific guidance — is owned entirely by the **`deepmd`
skill**. Load it for concrete instructions; this concept file does not repeat
CLI commands.

---

# MLFF inference instructions

Inference means using an MLFF to calculate the energy, force, and stress of
given structures.

MLFFs can be applied as fast calculators, and further be used to calculate and
simulate any atomistic properties unrelated to electronic structure.

Most MLFFs support the ASE calculator interface, which can be used to perform
any type of calculation that the `ase` skill supports, such as MD simulations,
structure optimization, and so on. See the `ase` skill for details.

Currently, only deepmd models are bundled with support to LAMMPS. A deepmd
model must be **frozen** before being used in a LAMMPS simulation input file.
See the `deepmd` skill for details.

> In deepmd models, LAMMPS often outperforms ASE in terms of simulation speed.
> When performing large-scale simulations, it is recommended to use `lammps`
> over `ase`.

---

# Constraints

- **NPT ensemble mandatory** for data sampling. Never switch to NVT/NVE
  without explicit user approval (they lack diversity in strain variation).
  When NPT simulation fails, fix the simulation code — do not switch to NVT/NVE
  as a detour.
- **Entropy-based structure selection is MANDATORY** before labeling.
- **Structure size:** ~50 atoms/structure. Large systems must NOT be extended
  into supercells.
- **Evaluation always compares pretrained vs fine-tuned** for both DPA4 and
  DPA4c; if model labeling was used, also compare DPA4c vs DFT (two-level).
- Both base models (DPA4 and DPA4c) use **init-model fine-tuning** and default
  to **50 epochs**.
